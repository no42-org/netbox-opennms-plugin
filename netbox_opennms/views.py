# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""UI views for plugin models."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View
from netbox.views import generic
from utilities.rqworker import any_workers_for_queue

from . import filtersets, forms, tables
from .client import OpenNMSClient, OpenNMSError
from .derivation import foreign_source_for
from .jobs import (
    SyncForeignSourceJob,
    enabled_foreign_sources,
    enabled_profiles_for,
)
from .models import MonitoredService, MonitoringProfile
from .validation import validate_foreign_source

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
    # Adds the "Sync all" / "Sync selected" buttons (Story 3.3).
    template_name = "netbox_opennms/monitoringprofile_list.html"


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

        # Validate the whole Foreign Source's intent before enqueuing (FR-8):
        # errors block the sync; warnings are informational.
        result = validate_foreign_source(
            foreign_source, enabled_profiles_for(foreign_source)
        )
        for warning in result.warnings:
            messages.warning(request, warning)
        if result.errors:
            for error in result.errors:
                messages.error(request, error)
            return redirect(profile.get_absolute_url())

        job = SyncForeignSourceJob.enqueue_sync(foreign_source, user=request.user)
        messages.success(
            request,
            f"Sync submitted for Foreign Source {foreign_source} (job #{job.pk}).",
        )
        # The no-worker warning is surfaced by the detail page's banner (shown on
        # the redirect target and on every view) — no duplicate flash here.
        return redirect(profile.get_absolute_url())


class MonitoringProfileRemoveView(PermissionRequiredMixin, View):
    """Remove a node from OpenNMS: clear intent (disable) + render-and-replace.

    Disabling the profile drops it from the FS render, so the node is deleted on
    import (AD-5). Uses the same job as Sync with allow_empty=True so removing the
    last node pushes the (intentional) empty requisition.

    Unlike Sync, this skips the *view's* pre-flight validation so the action isn't
    blocked at submit time by the whole-FS gate. Removing the LAST node always
    succeeds (an empty Foreign Source validates clean). A non-last remove still
    re-renders the surviving siblings (render-and-replace can't push a partial
    FS), so an invalid/un-renderable sibling will fail that job — the outcome
    lives on the Job (AD-12 honest status), not a request-time error.
    """

    permission_required = "netbox_opennms.change_monitoringprofile"

    def post(self, request, pk):
        profile = get_object_or_404(MonitoringProfile, pk=pk)
        target = profile.assigned_object
        if target is None:
            messages.error(
                request, "This profile has no assigned object and cannot be removed."
            )
            return redirect(profile.get_absolute_url())
        try:
            foreign_source = foreign_source_for(target)
        except TypeError:
            messages.error(
                request,
                "This profile's object is not a Device or VirtualMachine.",
            )
            return redirect(profile.get_absolute_url())

        # Clear intent and enqueue atomically: if the enqueue raises, the disable
        # rolls back so we never strand a disabled profile with no job.
        with transaction.atomic():
            profile.enabled = False
            profile.save()
            job = SyncForeignSourceJob.enqueue_sync(
                foreign_source, user=request.user, allow_empty=True
            )
        messages.success(
            request,
            f"Remove submitted for Foreign Source {foreign_source} (job #{job.pk}).",
        )
        return redirect(profile.get_absolute_url())


class MonitoringProfileSyncAllView(PermissionRequiredMixin, View):
    """Enqueue a Sync for every Foreign Source that has an enabled profile (FR-9).

    Fans out one ``SyncForeignSourceJob`` per distinct Foreign Source (AD-5:
    render-and-replace is per whole Foreign Source). Like Remove, it skips the
    Sync view's pre-flight validation so one broken Foreign Source can't block the
    rest of the batch — each job validates itself and reports honest status
    (AD-12). ``enqueue_sync`` coalesces redundant pending jobs per FS (AD-6).
    """

    permission_required = "netbox_opennms.change_monitoringprofile"

    def post(self, request):
        foreign_sources = enabled_foreign_sources()
        for foreign_source in foreign_sources:
            SyncForeignSourceJob.enqueue_sync(foreign_source, user=request.user)
        if foreign_sources:
            messages.success(
                request,
                f"Submitted {len(foreign_sources)} Foreign Source sync(s).",
            )
        else:
            messages.info(request, "Nothing enabled to sync.")
        return redirect("plugins:netbox_opennms:monitoringprofile_list")


class MonitoringProfileBulkSyncView(PermissionRequiredMixin, View):
    """Sync the Foreign Sources of the selected profiles (per-FS bulk, FR-9).

    Groups the selected profiles by their derived Foreign Source and enqueues ONE
    job per distinct Foreign Source (not one per profile) — render-and-replace is
    per whole Foreign Source (AD-5), so three devices in the same role+site
    collapse to a single requisition push. Non-Device/VM or unassigned selections
    are skipped cleanly. No pre-flight validation (each job self-validates, AD-12).
    """

    permission_required = "netbox_opennms.change_monitoringprofile"

    def post(self, request):
        return_url = request.POST.get("return_url")
        if not return_url or not url_has_allowed_host_and_scheme(
            return_url, allowed_hosts={request.get_host()}
        ):
            return_url = reverse("plugins:netbox_opennms:monitoringprofile_list")

        # Only enabled profiles are syncable intent — match Sync-all and the
        # single Sync view (which refuses disabled profiles).
        enabled = MonitoringProfile.objects.filter(enabled=True)
        if request.POST.get("_all"):
            # NetBox's "select all N matching query" toggle. The dedicated bulk
            # endpoint doesn't receive the list's filter query, so this acts on
            # every enabled profile (== Sync all) rather than silently syncing
            # only the checked page (deferred: filter-scoped bulk sync).
            profiles = enabled
        else:
            # Drop non-numeric pks defensively (crafted POST) instead of 500ing.
            pks = [pk for pk in request.POST.getlist("pk") if pk.isdigit()]
            profiles = enabled.filter(pk__in=pks)

        foreign_sources = set()
        matched = 0
        for profile in profiles:
            target = profile.assigned_object
            if target is None:
                continue
            try:
                foreign_sources.add(foreign_source_for(target))
            except TypeError:
                # Non-Device/VM target (limit_choices_to is form-only) — skip it.
                continue
            matched += 1

        for foreign_source in sorted(foreign_sources):
            SyncForeignSourceJob.enqueue_sync(foreign_source, user=request.user)

        if foreign_sources:
            messages.success(
                request,
                f"Submitted {len(foreign_sources)} Foreign Source sync(s) for "
                f"{matched} profile(s).",
            )
        else:
            messages.warning(
                request, "No syncable, enabled Device/VM profiles were selected."
            )
        return redirect(return_url)


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
