# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""UI views for plugin models."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import View
from netbox.views import generic
from utilities.rqworker import any_workers_for_queue

from . import filtersets, forms, tables
from .client import OpenNMSClient, OpenNMSError
from .derivation import foreign_source_for
from .jobs import SyncForeignSourceJob
from .models import MonitoredService, MonitoringProfile

# Sync jobs are enqueued without an instance, so they run on the default RQ
# queue (get_queue_for_model(None) -> RQ_QUEUE_DEFAULT). FR-13 / AD-16.
SYNC_QUEUE = "default"


def _no_worker_running():
    """True if no live RQ worker is servicing the Sync queue (best-effort, AD-16).

    Never raises into the page/action (AD-16). If the liveness probe itself fails
    (broker unreachable), warn rather than stay silent — we can't confirm a worker
    is running, and that uncertainty is exactly what FR-13 surfaces.
    """
    try:
        return not any_workers_for_queue(SYNC_QUEUE)
    except Exception:
        return True


class MonitoringProfileView(generic.ObjectView):
    queryset = MonitoringProfile.objects.all()

    def get_extra_context(self, request, instance):
        # Surface the no-worker warning on the detail page (AC2); derived live so
        # it clears automatically once a worker starts (AC3).
        return {"no_worker_warning": _no_worker_running()}


class MonitoringProfileListView(generic.ObjectListView):
    queryset = MonitoringProfile.objects.all()
    table = tables.MonitoringProfileTable
    filterset = filtersets.MonitoringProfileFilterSet


class MonitoringProfileEditView(generic.ObjectEditView):
    queryset = MonitoringProfile.objects.all()
    form = forms.MonitoringProfileForm


class MonitoringProfileDeleteView(generic.ObjectDeleteView):
    queryset = MonitoringProfile.objects.all()


class MonitoringProfileBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoringProfile.objects.all()
    table = tables.MonitoringProfileTable


class MonitoredServiceView(generic.ObjectView):
    queryset = MonitoredService.objects.all()


class MonitoredServiceListView(generic.ObjectListView):
    queryset = MonitoredService.objects.select_related("profile", "ip_address")
    table = tables.MonitoredServiceTable
    filterset = filtersets.MonitoredServiceFilterSet


class MonitoredServiceEditView(generic.ObjectEditView):
    queryset = MonitoredService.objects.all()
    form = forms.MonitoredServiceForm


class MonitoredServiceDeleteView(generic.ObjectDeleteView):
    queryset = MonitoredService.objects.all()


class MonitoredServiceBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoredService.objects.all()
    table = tables.MonitoredServiceTable


class MonitoringProfileSyncView(PermissionRequiredMixin, View):
    """Enqueue a background Sync for a profile's whole Foreign Source (AD-4/5).

    Gated by the change permission (NFR-3 wants a distinct Sync permission; a
    dedicated action permission is a tracked follow-up). The view only enqueues —
    all OpenNMS I/O happens in the job (AD-4).
    """

    permission_required = "netbox_opennms.change_monitoringprofile"

    def post(self, request, pk):
        profile = get_object_or_404(MonitoringProfile, pk=pk)
        target = profile.assigned_object
        if not profile.enabled:
            messages.error(
                request, "This profile is disabled — enable it before syncing."
            )
            return redirect(profile.get_absolute_url())
        if target is None:
            messages.error(
                request, "This profile has no assigned object and cannot be synced."
            )
            return redirect(profile.get_absolute_url())

        try:
            foreign_source = foreign_source_for(target)
        except TypeError:
            # Non-Device/VM target (limit_choices_to is form-only) — fail cleanly.
            messages.error(
                request,
                "This profile's object is not a Device or VirtualMachine "
                "and cannot be synced.",
            )
            return redirect(profile.get_absolute_url())
        job = SyncForeignSourceJob.enqueue_sync(foreign_source, user=request.user)
        messages.success(
            request,
            f"Sync submitted for Foreign Source {foreign_source} (job #{job.pk}).",
        )
        # The no-worker warning is surfaced by the detail page's banner (shown on
        # the redirect target and on every view) — no duplicate flash here.
        return redirect(profile.get_absolute_url())


class OpenNMSConnectionTestView(LoginRequiredMixin, View):
    """Authenticated action: probe the configured OpenNMS for reachability + auth."""

    template_name = "netbox_opennms/connection_test.html"

    def get(self, request):
        return render(request, self.template_name)

    def post(self, request):
        try:
            with OpenNMSClient.from_config() as client:
                client.test_connection()
        except OpenNMSError as exc:
            messages.error(request, f"OpenNMS connection failed: {exc}")
        else:
            messages.success(
                request,
                "OpenNMS connection OK — reachable and credentials accepted.",
            )
        return redirect("plugins:netbox_opennms:connection_test")
