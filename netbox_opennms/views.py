# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI views for plugin models (Requisition redesign)."""

from copy import deepcopy

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import View
from netbox.plugins import get_plugin_config
from netbox.views import generic
from utilities.rqworker import any_workers_for_queue

from . import filtersets, forms, tables
from .client import OpenNMSClient, OpenNMSError
from .dryrun import dry_run
from .jobs import (
    SyncForeignSourceJob,
    unknown_locations,
)
from .membership import (
    filter_errors,
    requisition_conflicts,
    resolve,
    resolve_all,
)
from .models import (
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)
from .validation import validate_resolution

# Sync jobs are enqueued without an instance, so they run on the default RQ
# queue (get_queue_for_model(None) -> RQ_QUEUE_DEFAULT). FR-13 / AD-16.
SYNC_QUEUE = "default"
SYNC_PERM = "netbox_opennms.change_requisition"


def _no_worker_running():
    """True if no live RQ worker is servicing the Sync queue (best-effort, AD-16)."""
    try:
        return not any_workers_for_queue(SYNC_QUEUE)
    except Exception:
        return True


def _location_warnings(locations):
    """Best-effort warnings for chosen locations with no Minion (FR-5/AD-16)."""
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
    """Validate a Foreign Source's resolved intent and enqueue a sync (FR-8)."""
    resolution = resolve(foreign_source)
    result = validate_resolution(resolution, removing=allow_empty)
    for warning in result.warnings:
        messages.warning(request, warning)
    if result.errors:
        for error in result.errors:
            messages.error(request, error)
        return None

    locations = set()
    if resolution is not None:
        locations.add(resolution.requisition.location)
        locations.update(node.location for node in resolution.nodes)
    for warning in _location_warnings(locations):
        messages.warning(request, warning)

    return SyncForeignSourceJob.enqueue_sync(
        foreign_source, user=request.user, allow_empty=allow_empty
    )


# --- Requisition ------------------------------------------------------------


class RequisitionView(generic.ObjectView):
    queryset = Requisition.objects.prefetch_related("detectors", "policies")

    def get_extra_context(self, request, instance):
        # The post-save landing page doubles as the overlap warning surface (C2):
        # a save with an overlapping filter succeeds, and the conflict banner here
        # names the object + parties immediately. requisition_conflicts tests only
        # THIS requisition's members against the other filters — narrow queries,
        # no fleet-wide node resolution (review #12). Stale-value warnings and a
        # rejected filter are surfaced too — the post-save page must not be
        # silent about a filter that now matches nothing (review #7).
        resolution_warnings = []
        conflicts = requisition_conflicts(instance, resolution_warnings)
        return {
            "no_worker_warning": _no_worker_running(),
            "conflicts": conflicts,
            "filter_problems": filter_errors(instance),
            "resolution_warnings": resolution_warnings,
        }


class RequisitionListView(generic.ObjectListView):
    queryset = Requisition.objects.all()
    table = tables.RequisitionTable
    filterset = filtersets.RequisitionFilterSet


class RequisitionEditView(generic.ObjectEditView):
    queryset = Requisition.objects.all()
    form = forms.RequisitionForm


class RequisitionDeleteView(generic.ObjectDeleteView):
    queryset = Requisition.objects.all()


class RequisitionBulkDeleteView(generic.BulkDeleteView):
    queryset = Requisition.objects.all()
    table = tables.RequisitionTable


class RequisitionDuplicateView(PermissionRequiredMixin, View):
    """Deep-copy a Requisition (rules, services, filter) into a new named one (R4)."""

    permission_required = "netbox_opennms.add_requisition"

    def post(self, request, pk):
        source = get_object_or_404(Requisition, pk=pk)
        detectors = list(source.detectors.all())
        policies = list(source.policies.all())

        # Bound to Requisition.name max_length (100), leaving room for a "-N" tag.
        base = f"{source.name}-copy"[:100]
        name = base
        suffix = 2
        while Requisition.objects.filter(name=name).exists():
            tag = f"-{suffix}"
            name = f"{base[: 100 - len(tag)]}{tag}"
            suffix += 1

        clone = Requisition(
            name=name,
            description=source.description,
            object_types=source.object_types,
            filter_params=deepcopy(source.filter_params),
            scan_interval=source.scan_interval,
            default_interfaces=source.default_interfaces,
            services=list(source.services or []),
            location=source.location,
        )
        clone.save()
        for detector in detectors:
            MonitoringDetector.objects.create(
                requisition=clone,
                name=detector.name,
                preset=detector.preset,
                rule_class=detector.rule_class,
                parameters=deepcopy(detector.parameters),
            )
        for policy in policies:
            MonitoringPolicy.objects.create(
                requisition=clone,
                name=policy.name,
                preset=policy.preset,
                rule_class=policy.rule_class,
                parameters=deepcopy(policy.parameters),
            )
        messages.success(request, f"Duplicated {source.name} → {clone.name}.")
        # A verbatim filter copy overlaps the source on every CURRENT member —
        # warn only when that actually froze something (a zero-member source
        # duplicates harmlessly, review #4-fix).
        if requisition_conflicts(clone):
            messages.warning(
                request,
                f"{clone.name} has the same filter as {source.name}: every "
                "shared member is now a conflict and BOTH requisitions are "
                f"frozen. Edit {clone.name}'s filter (or delete it) to unfreeze.",
            )
        else:
            messages.info(
                request,
                f"{clone.name} shares {source.name}'s filter — edit it before "
                "the filters match the same objects, or they will conflict.",
            )
        return redirect(clone.get_absolute_url())


class RequisitionSyncView(PermissionRequiredMixin, View):
    """Enqueue a Sync for the Foreign Source this Requisition owns (AD-4/5)."""

    permission_required = SYNC_PERM

    def post(self, request, pk):
        requisition = get_object_or_404(Requisition, pk=pk)
        job = _enqueue_foreign_source(request, requisition.name)
        if job is not None:
            messages.success(
                request, f"Sync submitted for {requisition.name} (job #{job.pk})."
            )
        return redirect(requisition.get_absolute_url())


class RequisitionDryRunView(PermissionRequiredMixin, View):
    """Show the per-node diff of a Requisition against the live OpenNMS state (R7).

    Permission-gated (not merely login) because it issues live outbound calls to
    OpenNMS and returns the node/interface/service topology (review #7).
    """

    permission_required = "netbox_opennms.view_requisition"
    template_name = "netbox_opennms/dry_run.html"

    def get(self, request, pk):
        requisition = get_object_or_404(Requisition, pk=pk)
        error = None
        result = None
        try:
            result = dry_run(requisition.name)
        except OpenNMSError as exc:
            error = str(exc)
        return render(
            request,
            self.template_name,
            {"object": requisition, "dryrun": result, "error": error},
        )


# --- Monitoring Detector ----------------------------------------------------


class MonitoringDetectorView(generic.ObjectView):
    queryset = MonitoringDetector.objects.all()


class MonitoringDetectorListView(generic.ObjectListView):
    queryset = MonitoringDetector.objects.select_related("requisition")
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
    queryset = MonitoringPolicy.objects.select_related("requisition")
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


# --- Monitored Interface ----------------------------------------------------


class MonitoredInterfaceView(generic.ObjectView):
    queryset = MonitoredInterface.objects.all()


class MonitoredInterfaceListView(generic.ObjectListView):
    queryset = MonitoredInterface.objects.select_related("override", "ip_address")
    table = tables.MonitoredInterfaceTable
    filterset = filtersets.MonitoredInterfaceFilterSet


class MonitoredInterfaceEditView(generic.ObjectEditView):
    queryset = MonitoredInterface.objects.all()
    form = forms.MonitoredInterfaceForm


class MonitoredInterfaceDeleteView(generic.ObjectDeleteView):
    queryset = MonitoredInterface.objects.all()


class MonitoredInterfaceBulkDeleteView(generic.BulkDeleteView):
    queryset = MonitoredInterface.objects.all()
    table = tables.MonitoredInterfaceTable


# --- Asset Mapping ----------------------------------------------------------


class AssetMappingView(generic.ObjectView):
    queryset = AssetMapping.objects.all()


class AssetMappingListView(generic.ObjectListView):
    queryset = AssetMapping.objects.select_related("requisition")
    table = tables.AssetMappingTable
    filterset = filtersets.AssetMappingFilterSet


class AssetMappingEditView(generic.ObjectEditView):
    queryset = AssetMapping.objects.all()
    form = forms.AssetMappingForm


class AssetMappingDeleteView(generic.ObjectDeleteView):
    queryset = AssetMapping.objects.all()


class AssetMappingBulkDeleteView(generic.BulkDeleteView):
    queryset = AssetMapping.objects.all()
    table = tables.AssetMappingTable


# --- Metadata Entry ---------------------------------------------------------


class MetadataEntryView(generic.ObjectView):
    queryset = MetadataEntry.objects.all()


class MetadataEntryListView(generic.ObjectListView):
    queryset = MetadataEntry.objects.select_related("requisition")
    table = tables.MetadataEntryTable
    filterset = filtersets.MetadataEntryFilterSet


class MetadataEntryEditView(generic.ObjectEditView):
    queryset = MetadataEntry.objects.all()
    form = forms.MetadataEntryForm


class MetadataEntryDeleteView(generic.ObjectDeleteView):
    queryset = MetadataEntry.objects.all()


class MetadataEntryBulkDeleteView(generic.BulkDeleteView):
    queryset = MetadataEntry.objects.all()
    table = tables.MetadataEntryTable


# --- Sync actions -----------------------------------------------------------


class ForeignSourceSyncView(PermissionRequiredMixin, View):
    """Enqueue a Sync (or Remove) for one Foreign Source named in the POST."""

    permission_required = SYNC_PERM

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
    """Enqueue a Sync for every syncable Requisition (FR-9).

    Fans out over one ``resolve_all()`` pass: requisitions that resolve to nodes
    are enqueued; **frozen** ones (conflicts) are skipped with a warning instead
    of being enqueued into a guaranteed-failed Job (review #2) — the freeze is
    enforced here just as it is on the per-requisition Sync path.
    """

    permission_required = SYNC_PERM

    def post(self, request):
        # The CANONICAL gate — validate_resolution — decides what is blocked,
        # exactly as the per-requisition Sync path does: conflicts (frozen),
        # rejected filters, and invalid locations are all skipped with a warning
        # here instead of becoming guaranteed-failed jobs or silent skips
        # (reviews #2/#8 applied symmetrically).
        submitted, blocked = 0, 0
        for resolution in resolve_all():
            if validate_resolution(resolution).errors:
                blocked += 1
                continue
            if not resolution.nodes:
                continue
            SyncForeignSourceJob.enqueue_sync(
                resolution.foreign_source, user=request.user
            )
            submitted += 1
        if blocked:
            messages.warning(
                request,
                f"Skipped {blocked} requisition(s) blocked by validation errors "
                "(frozen by a conflict, a rejected filter, or an invalid "
                "location) — open their pages to resolve.",
            )
        if submitted:
            messages.success(
                request, f"Submitted {submitted} Foreign Source sync(s)."
            )
        else:
            messages.info(request, "Nothing to sync.")
        return redirect("plugins:netbox_opennms:sync_preview")


class SyncPreviewView(LoginRequiredMixin, View):
    """The preview-and-sync overview: every Requisition + its resolved members.

    Lists every Requisition with its node count, any resolution warnings
    (rejected filters, member skips), and its blocking conflicts (a frozen
    Requisition cannot sync until the overlap is resolved — C1), so the operator
    sees what will go before pressing Sync. The per-node dry-run diff against
    OpenNMS is a per-Requisition action (RequisitionDryRunView).
    """

    template_name = "netbox_opennms/sync_preview.html"

    def get(self, request):
        rows = []
        for resolution in resolve_all():
            rows.append(
                {
                    "foreign_source": resolution.foreign_source,
                    "requisition": resolution.requisition,
                    "node_count": len(resolution.nodes),
                    # Rejected-filter errors join the warnings badge so a broken
                    # filter stays visible on the preview.
                    "warnings": [*resolution.rejected, *resolution.warnings],
                    "conflicts": resolution.conflicts,
                }
            )
        return render(
            request,
            self.template_name,
            {"rows": rows, "no_worker_warning": _no_worker_running()},
        )


class OpenNMSConnectionTestView(PermissionRequiredMixin, View):
    """"Connect OpenNMS" page: verify the CONFIGURED connection (Option D).

    Probes the OpenNMS instance defined in ``PLUGINS_CONFIG`` for reachability and
    valid credentials, and shows the effective URL/username (never the password)
    read-only. The connection is configured in NetBox's plugin config
    (``configuration.py`` / Helm ``pluginsConfig``), never here — so the view
    accepts no user-supplied URL or credentials (which could send the stored
    secret to an arbitrary host) and stores nothing (AD-13). Permission-gated,
    like the dry-run, because it issues an outbound call with stored credentials.
    """

    permission_required = "netbox_opennms.view_requisition"
    template_name = "netbox_opennms/connection_test.html"

    def _context(self):
        # Show the effective connection so the user knows what will be tested;
        # the password is reported only as set / not set, never rendered.
        return {
            "opennms_url": get_plugin_config("netbox_opennms", "opennms_url") or "",
            "opennms_username": get_plugin_config(
                "netbox_opennms", "opennms_username"
            )
            or "",
            "password_set": bool(
                get_plugin_config("netbox_opennms", "opennms_password")
            ),
        }

    def get(self, request):
        return render(request, self.template_name, self._context())

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
