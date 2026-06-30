# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI views for plugin models (Epic 5)."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View
from netbox.views import generic
from utilities.rqworker import any_workers_for_queue

from . import filtersets, forms, tables
from .client import OpenNMSClient, OpenNMSError
from .jobs import (
    SyncForeignSourceJob,
    enabled_foreign_sources,
    unknown_locations,
)
from .membership import governing_assignment, resolve
from .models import (
    MonitoredService,
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
)
from .validation import validate_resolution

# Sync jobs are enqueued without an instance, so they run on the default RQ
# queue (get_queue_for_model(None) -> RQ_QUEUE_DEFAULT). FR-13 / AD-16.
SYNC_QUEUE = "default"


def _no_worker_running():
    """True if no live RQ worker is servicing the Sync queue (best-effort, AD-16)."""
    try:
        return not any_workers_for_queue(SYNC_QUEUE)
    except Exception:
        return True


def _location_warnings(locations):
    """Best-effort warnings for chosen locations with no Minion (FR-5/AD-16).

    Opens the port, asks OpenNMS which monitoring locations exist, and returns a
    warning string per location it doesn't know. ANY failure degrades to ``[]`` —
    the check never blocks Sync.
    """
    try:
        with OpenNMSClient.from_config() as client:
            missing = unknown_locations(client, locations)
    except Exception:
        return []
    return [
        f"Location {location!r} is not a known OpenNMS monitoring location — "
        "no Minion will poll it (check the OpenNMS Minion/location setup)."
        for location in missing
    ]


def _enqueue_foreign_source(request, foreign_source, allow_empty=False):
    """Validate a Foreign Source's resolved intent and enqueue a sync (FR-8).

    Errors block; warnings (member skips, unknown locations) are surfaced and the
    sync still proceeds. Returns the Job, or ``None`` when validation blocked it.
    """
    try:
        resolution = resolve(foreign_source)
    except ValueError as exc:
        # A tampered/garbled ``foreign_source`` POST value (not netbox.site.role).
        messages.error(request, f"Invalid Foreign Source {foreign_source!r}: {exc}")
        return None
    result = validate_resolution(resolution)
    for warning in result.warnings:
        messages.warning(request, warning)
    if result.errors:
        for error in result.errors:
            messages.error(request, error)
        return None

    locations = set()
    if resolution is not None:
        locations.add(resolution.assignment.location)
        locations.update(node.location for node in resolution.nodes)
    for warning in _location_warnings(locations):
        messages.warning(request, warning)

    return SyncForeignSourceJob.enqueue_sync(
        foreign_source, user=request.user, allow_empty=allow_empty
    )


# --- Monitoring Profile (template) -----------------------------------------


class MonitoringProfileView(generic.ObjectView):
    queryset = MonitoringProfile.objects.all()


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


# --- Monitoring Detector ----------------------------------------------------


class MonitoringDetectorView(generic.ObjectView):
    queryset = MonitoringDetector.objects.all()


class MonitoringDetectorListView(generic.ObjectListView):
    queryset = MonitoringDetector.objects.select_related("profile")
    table = tables.MonitoringDetectorTable
    filterset = filtersets.MonitoringDetectorFilterSet


class MonitoringDetectorEditView(generic.ObjectEditView):
    queryset = MonitoringDetector.objects.all()
    form = forms.MonitoringDetectorForm


class MonitoringDetectorDeleteView(generic.ObjectDeleteView):
    queryset = MonitoringDetector.objects.all()


class MonitoringDetectorBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoringDetector.objects.all()
    table = tables.MonitoringDetectorTable


# --- Monitoring Policy ------------------------------------------------------


class MonitoringPolicyView(generic.ObjectView):
    queryset = MonitoringPolicy.objects.all()


class MonitoringPolicyListView(generic.ObjectListView):
    queryset = MonitoringPolicy.objects.select_related("profile")
    table = tables.MonitoringPolicyTable
    filterset = filtersets.MonitoringPolicyFilterSet


class MonitoringPolicyEditView(generic.ObjectEditView):
    queryset = MonitoringPolicy.objects.all()
    form = forms.MonitoringPolicyForm


class MonitoringPolicyDeleteView(generic.ObjectDeleteView):
    queryset = MonitoringPolicy.objects.all()


class MonitoringPolicyBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoringPolicy.objects.all()
    table = tables.MonitoringPolicyTable


# --- Monitoring Assignment --------------------------------------------------


class MonitoringAssignmentView(generic.ObjectView):
    queryset = MonitoringAssignment.objects.select_related("profile", "site", "role")

    def get_extra_context(self, request, instance):
        return {"no_worker_warning": _no_worker_running()}


class MonitoringAssignmentListView(generic.ObjectListView):
    queryset = MonitoringAssignment.objects.select_related("profile", "site", "role")
    table = tables.MonitoringAssignmentTable
    filterset = filtersets.MonitoringAssignmentFilterSet


class MonitoringAssignmentEditView(generic.ObjectEditView):
    queryset = MonitoringAssignment.objects.all()
    form = forms.MonitoringAssignmentForm


class MonitoringAssignmentDeleteView(generic.ObjectDeleteView):
    queryset = MonitoringAssignment.objects.all()


class MonitoringAssignmentBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoringAssignment.objects.all()
    table = tables.MonitoringAssignmentTable


class MonitoringAssignmentSyncView(PermissionRequiredMixin, View):
    """Enqueue a Sync for every Foreign Source this assignment governs (AD-4/5).

    A (site, role) assignment governs one Foreign Source; a site-level assignment
    fans out to one per role present in the site (the more-specific assignment
    wins, so this never double-syncs a Foreign Source owned by another).
    """

    permission_required = "netbox_opennms.change_monitoringassignment"

    def post(self, request, pk):
        assignment = get_object_or_404(MonitoringAssignment, pk=pk)
        foreign_sources = [
            fs
            for fs in enabled_foreign_sources()
            if governing_assignment(fs) == assignment
        ]
        submitted = 0
        for foreign_source in foreign_sources:
            if _enqueue_foreign_source(request, foreign_source) is not None:
                submitted += 1
        if submitted:
            messages.success(request, f"Submitted {submitted} Foreign Source sync(s).")
        elif not foreign_sources:
            messages.info(request, "This assignment governs no monitored objects yet.")
        return redirect(assignment.get_absolute_url())


# --- Monitoring Override ----------------------------------------------------


class MonitoringOverrideView(generic.ObjectView):
    queryset = MonitoringOverride.objects.all()


class MonitoringOverrideListView(generic.ObjectListView):
    queryset = MonitoringOverride.objects.select_related(
        "assigned_object_type", "management_ip"
    )
    table = tables.MonitoringOverrideTable
    filterset = filtersets.MonitoringOverrideFilterSet


class MonitoringOverrideEditView(generic.ObjectEditView):
    queryset = MonitoringOverride.objects.all()
    form = forms.MonitoringOverrideForm


class MonitoringOverrideDeleteView(generic.ObjectDeleteView):
    queryset = MonitoringOverride.objects.all()


class MonitoringOverrideBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoringOverride.objects.all()
    table = tables.MonitoringOverrideTable


# --- Monitored Service ------------------------------------------------------


class MonitoredServiceView(generic.ObjectView):
    queryset = MonitoredService.objects.all()


class MonitoredServiceListView(generic.ObjectListView):
    queryset = MonitoredService.objects.select_related("override", "ip_address")
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


# --- Sync actions -----------------------------------------------------------


class ForeignSourceSyncView(PermissionRequiredMixin, View):
    """Enqueue a Sync (or Remove) for one Foreign Source named in the POST.

    Render-and-replace is per whole Foreign Source (AD-5). A Remove
    (``allow_empty``) pushes the intentional empty requisition that clears the
    Foreign Source; a plain Sync refuses an empty one (it would mass-delete).
    """

    permission_required = "netbox_opennms.change_monitoringassignment"

    def post(self, request):
        foreign_source = request.POST.get("foreign_source", "").strip()
        allow_empty = bool(request.POST.get("remove"))
        return_url = request.POST.get("return_url")
        if not return_url or not url_has_allowed_host_and_scheme(
            return_url, allowed_hosts={request.get_host()}
        ):
            return_url = reverse("plugins:netbox_opennms:sync_preview")
        if not foreign_source:
            messages.error(request, "No Foreign Source given.")
            return redirect(return_url)

        job = _enqueue_foreign_source(request, foreign_source, allow_empty=allow_empty)
        if job is not None:
            verb = "Remove" if allow_empty else "Sync"
            messages.success(
                request,
                f"{verb} submitted for Foreign Source {foreign_source} "
                f"(job #{job.pk}).",
            )
        return redirect(return_url)


class MonitoringSyncAllView(PermissionRequiredMixin, View):
    """Enqueue a Sync for every governed Foreign Source with members (FR-9)."""

    permission_required = "netbox_opennms.change_monitoringassignment"

    def post(self, request):
        foreign_sources = enabled_foreign_sources()
        for foreign_source in foreign_sources:
            SyncForeignSourceJob.enqueue_sync(foreign_source, user=request.user)
        if foreign_sources:
            messages.success(
                request, f"Submitted {len(foreign_sources)} Foreign Source sync(s)."
            )
        else:
            messages.info(request, "Nothing assigned to sync.")
        return redirect("plugins:netbox_opennms:sync_preview")


class SyncPreviewView(LoginRequiredMixin, View):
    """The preview-and-sync overview: every governed Foreign Source + its members.

    A read-only aggregate for now (the paginated, status-filtered detail and
    bulk-adjust are Story 5.5). Resolves each Foreign Source so the operator sees
    the node count and any skip warnings before pressing Sync.
    """

    template_name = "netbox_opennms/sync_preview.html"

    def get(self, request):
        rows = []
        for foreign_source in enabled_foreign_sources():
            resolution = resolve(foreign_source)
            rows.append(
                {
                    "foreign_source": foreign_source,
                    "assignment": resolution.assignment if resolution else None,
                    "node_count": len(resolution.nodes) if resolution else 0,
                    "warnings": resolution.warnings if resolution else [],
                }
            )
        return render(
            request,
            self.template_name,
            {"rows": rows, "no_worker_warning": _no_worker_running()},
        )


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
