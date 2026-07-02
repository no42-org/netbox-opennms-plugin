# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Background jobs — render-and-replace a Foreign Source against OpenNMS (AD-4/5/6).

``SyncForeignSourceJob`` turns the resolved membership of one Foreign Source (the
pure ``membership`` + ``translation`` layers) into a real OpenNMS push through
the ``OpenNMSClient`` port (AD-2). It runs in a NetBox ``JobRunner`` so it never
blocks a request (AD-4), re-renders the *complete* requisition for the Foreign
Source from its current members (AD-5), and serializes per Foreign Source via a
Postgres advisory lock so two syncs cannot race (AD-6).

Epic 5: membership is a live NetBox query (site+role), not per-object profiles,
so a moved/added/removed object re-resolves its scope on the next sync. A
non-last departure is reconciled automatically — re-syncing the surviving
members render-and-replaces the Foreign Source without the gone node. The one
gap: when the LAST member leaves a Foreign Source (object deleted, role/site
changed, or its assignment removed), that Foreign Source is no longer governed,
so neither Sync-All nor per-assignment Sync lists it, and its stale OpenNMS nodes
linger until a manual Remove. A periodic drift reconciler closes this window and
is tracked as deferred work (carried from v1).

Outcome maps to the NetBox ``Job`` lifecycle (AD-12): a clean return is
*succeeded-accepted*; a render or port error raises ``JobFailed`` → *failed*. A
bare ``202`` from import is "accepted for import", never "provisioned".
"""

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from core.models import Job
from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django_pglocks import advisory_lock
from netbox.jobs import JobRunner, system_job
from netbox.plugins import get_plugin_config
from virtualization.models import VirtualMachine

from .client import OpenNMSClient, OpenNMSError
from .derivation import validate_location_name
from .membership import (
    matching_requisitions,
    monitored_foreign_sources,
    resolve,
)
from .models import DeployedForeignSource, MonitoringOverride
from .translation import (
    RenderError,
    render_foreign_source_definition,
    render_requisition,
)
from .validation import validate_resolution

PLUGIN_NAME = "netbox_opennms"
# How often the drift reconciler runs (minutes). A literal — @system_job is
# evaluated at import, before plugin config is available; operators disable the
# pass entirely via the ``reconcile_orphans`` config flag, not the cadence.
RECONCILE_INTERVAL_MINUTES = 60


def unknown_locations(client, locations):
    """Locations OpenNMS doesn't know (Story 4.1, FR-5).

    Pure given a ``client``: returns the sorted distinct non-empty locations that
    are absent from ``client.list_locations()`` — each has no registered Minion,
    so the node is never polled. Skips the port call when no location is set.
    Callers run this best-effort and swallow ``OpenNMSError`` (AD-16).
    """
    wanted = {location for location in locations if location}
    if not wanted:
        return []
    return sorted(wanted - client.list_locations())


class SyncForeignSourceJob(JobRunner):
    """Render-and-replace one Foreign Source against OpenNMS, serialized per FS."""

    class Meta:
        name = "OpenNMS sync"

    @classmethod
    def job_name(cls, foreign_source, allow_empty=False):
        """The Job ``name`` for a Foreign Source (the single owner of the format).

        Shared by ``enqueue_sync`` (write) and ``latest_sync_job`` (read), so the
        observability lookup always matches what was enqueued. ``Job.name`` is
        max_length=200; the ``(remove)`` marker is budgeted INTO the cap so a long
        Foreign Source can't truncate it away (Story 3.1).
        """
        suffix = " (remove)" if allow_empty else ""
        return f"{cls.name}: {foreign_source}"[: 200 - len(suffix)] + suffix

    @classmethod
    def enqueue_sync(cls, foreign_source, user=None, allow_empty=False):
        """Enqueue a sync, coalescing a redundant pending sync for the same FS.

        The advisory lock in ``run`` is the hard race guard (AD-6); this skip-if-
        pending check is best-effort dedup. The ``(remove)`` marker keeps a Remove
        (allow_empty) from coalescing into a pending Sync that would refuse the
        empty requisition (Story 3.1).
        """
        job_name = cls.job_name(foreign_source, allow_empty=allow_empty)
        existing = Job.objects.filter(
            name=job_name,
            status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
        ).first()
        if existing is not None:
            return existing
        return cls.enqueue(
            name=job_name,
            foreign_source=foreign_source,
            user=user,
            allow_empty=allow_empty,
        )

    def run(self, foreign_source, allow_empty=False, **kwargs):
        # Validate config once (it applies to the whole job).
        default_location = get_plugin_config(PLUGIN_NAME, "default_location")
        if default_location:
            try:
                validate_location_name(default_location)
            except ValueError as exc:
                self.logger.error(f"Configured default_location is invalid: {exc}")
                raise JobFailed() from exc
        rescan = str(get_plugin_config(PLUGIN_NAME, "import_mode")).strip().lower()
        if rescan not in ("true", "false", "dbonly"):
            self.logger.error(
                f"Invalid import_mode {rescan!r} (expected true/false/dbonly)."
            )
            raise JobFailed()

        with advisory_lock(f"netbox_opennms:fs:{foreign_source}"):
            self._render_and_replace(
                foreign_source,
                allow_empty=allow_empty,
                default_location=default_location,
                rescan=rescan,
            )

    def _render_and_replace(
        self, foreign_source, allow_empty, default_location, rescan
    ):
        """Render-and-replace ONE Foreign Source (AD-5). Returns True if a push
        happened, False if skipped (not governed / empty and not allow_empty).

        Caller holds the advisory lock and has validated config; this resolves the
        membership, validates intent (AD-12 safety net), renders, and pushes.
        """
        resolution = resolve(foreign_source)

        if resolution is None and not allow_empty:
            self.logger.info(
                f"{foreign_source} has no Requisition — skipped."
            )
            return False

        # Validate FIRST (FR-8/AD-12): conflicts (C1 freeze) and rejected filters
        # fail the job loudly even when they resolve to ZERO nodes — the quiet
        # empty-skip below must never swallow a blocking error (review #8).
        validation = validate_resolution(resolution)
        for warning in validation.warnings:
            self.logger.warning(warning)
        if validation.errors:
            for error in validation.errors:
                self.logger.error(error)
            raise JobFailed()

        if resolution is not None and not resolution.nodes and not allow_empty:
            # Validated clean but nothing resolved. A Sync must never mass-delete:
            # an empty requisition tells OpenNMS to remove every node — that is
            # the deliberate Remove path (allow_empty), not Sync.
            self.logger.info(
                f"nothing to sync for {foreign_source} (no monitorable members) "
                "— skipped; use Remove to clear the Foreign Source."
            )
            return False

        nodes = resolution.nodes if resolution is not None else []
        locations = {default_location}
        if resolution is not None:
            locations.add(resolution.requisition.location)
            locations.update(node.location for node in nodes)

        try:
            requisition_xml = render_requisition(
                foreign_source, nodes, default_location=default_location
            )
            fs_xml = (
                render_foreign_source_definition(
                    foreign_source, resolution.requisition
                )
                if resolution is not None
                else None
            )
        except RenderError as exc:
            self.logger.error(f"Cannot render {foreign_source}: {exc}")
            raise JobFailed() from exc

        try:
            with OpenNMSClient.from_config() as client:
                # Order matters (AD-11): definition first, then requisition, then
                # import. A bare Remove (no assignment) has no definition to push.
                if fs_xml is not None:
                    client.post_foreign_source(fs_xml)
                client.post_requisition(requisition_xml)
                client.import_requisition(foreign_source, rescan_existing=rescan)
                # Record ownership so the reconciler can find this FS as an orphan
                # later even though its (user-chosen) name has no netbox. prefix.
                # Only when we actually pushed nodes — an empty push is a teardown.
                if nodes:
                    DeployedForeignSource.objects.get_or_create(name=foreign_source)
                # Best-effort advisory (FR-5/AD-16): warn on an unknown location.
                try:
                    for location in unknown_locations(client, locations):
                        self.logger.warning(
                            f"Location {location!r} is not a known OpenNMS "
                            "monitoring location — no Minion will poll it."
                        )
                except Exception:
                    pass
                # A Remove that resolved to ZERO nodes (a deleted/renamed Requisition,
                # or one that now matches nothing): the nodes are cleared, so also
                # drop the requisition + foreign-source shell AND the ownership record
                # — else the empty requisition lingers in OpenNMS and the reconciler
                # re-Removes it every interval forever (review #3). Best-effort: a
                # 404/transient here must not fail an otherwise-successful Remove.
                if allow_empty and not nodes:
                    try:
                        client.delete_requisition(foreign_source)
                        client.delete_foreign_source(foreign_source)
                        DeployedForeignSource.objects.filter(
                            name=foreign_source
                        ).delete()
                    except OpenNMSError as exc:
                        self.logger.warning(
                            f"could not purge orphan shell {foreign_source}: {exc}"
                        )
        except OpenNMSError as exc:
            self.logger.error(f"OpenNMS sync of {foreign_source} failed: {exc}")
            raise JobFailed() from exc

        self.logger.info(
            f"succeeded-accepted: import of {foreign_source} accepted by "
            "OpenNMS (HTTP 2xx/202 — submitted for import, not verified)."
        )
        return True


def latest_sync_job(foreign_sources):
    """The most recent sync/remove ``Job`` across one or more Foreign Sources.

    The Job is the audit record (user, timestamps, status, log, error). Matches
    both the sync and remove name forms via the shared ``job_name`` so the lookup
    can never drift from what ``enqueue_sync`` wrote. ``None`` if none found.
    """
    if isinstance(foreign_sources, str):
        foreign_sources = [foreign_sources]
    names = []
    for foreign_source in foreign_sources:
        names.append(SyncForeignSourceJob.job_name(foreign_source))
        names.append(SyncForeignSourceJob.job_name(foreign_source, allow_empty=True))
    if not names:
        return None
    return Job.objects.filter(name__in=names).order_by("-created").first()


def sync_outcome(job, is_removal=False, governed=True):
    """Map a sync ``Job`` to the honest outcome vocabulary (AD-12), or ``None``.

    Returns ``(label, color)``: ``submitted`` (pending/scheduled/running);
    ``succeeded-accepted`` (completed — accepted for import, never "provisioned");
    ``removed`` (a completed Remove, or an object no longer governed/excluded);
    ``failed`` (errored/failed).
    """
    if job is None:
        return None
    if job.status in JobStatusChoices.ENQUEUED_STATE_CHOICES:
        return ("submitted", "cyan")
    if job.status == JobStatusChoices.STATUS_COMPLETED:
        if is_removal or not governed:
            return ("removed", "gray")
        return ("succeeded-accepted", "green")
    return ("failed", "red")


def sync_status_for(target):
    """Last-sync state for a Device/VM — the single source the Device/VM template
    extension renders (Story 4.2).

    Finds every Requisition whose filter matches the object: exactly one =
    governed by it; several = **conflicted** between them (C1 — the panel names
    the parties); none = unmonitored. Also reports whether an override excludes
    it, and attaches the latest Job for the governing Foreign Source. Returns
    ``None`` for a missing or non-Device/VM target.
    """
    if target is None or not isinstance(target, (Device, VirtualMachine)):
        return None
    # matching_requisitions includes matches for an excluded object, so the panel
    # keeps the Foreign Source + last-sync Job (review #9); `governed` (actively
    # monitored) then excludes the excluded ones. An excluded object never
    # conflicts (C3) — its first match is shown as the (inactive) requisition.
    matches = matching_requisitions(target)
    content_type = ContentType.objects.get_for_model(type(target))
    override = MonitoringOverride.objects.filter(
        assigned_object_type=content_type, assigned_object_id=target.pk
    ).first()
    excluded = bool(override and override.exclude)
    conflicted = len(matches) >= 2 and not excluded
    requisition = None
    if matches and (len(matches) == 1 or excluded):
        requisition = matches[0]
    governed = requisition is not None and not excluded and not conflicted
    foreign_source = requisition.name if requisition is not None else None
    # Look up the last sync across EVERY matching Foreign Source (reviews #3/#9):
    # a conflicted/excluded multi-match object's node and Job history live under
    # whichever requisition actually synced it — the panel must keep them.
    names = [r.name for r in matches]
    job = latest_sync_job(names) if names else None
    is_removal = bool(job) and job.name.endswith(" (remove)")
    return {
        "foreign_source": foreign_source,
        "requisition": requisition,
        "governed": governed,
        "excluded": excluded,
        "conflicts": sorted(r.name for r in matches) if conflicted else [],
        "job": job,
        "outcome": sync_outcome(job, is_removal=is_removal, governed=governed),
    }


def orphaned_foreign_sources(client):
    """Foreign Sources OpenNMS holds that NetBox manages but no longer monitors.

    The reconciliation core (pure given a ``client``, so it tests against a fake).
    Requisition names are user-chosen, so ownership is tracked in
    ``DeployedForeignSource`` (written on each successful push) rather than a
    ``netbox.`` name prefix (review #4): orphans = (OpenNMS requisitions ∩ our
    managed names) − currently monitored. Scoped to managed names, so a requisition
    NetBox never created is never touched. Catches every drift cause uniformly:
    last-member departure, membership move, requisition rename, and deletion.
    """
    deployed = set(client.list_requisition_names())
    managed = set(DeployedForeignSource.objects.values_list("name", flat=True))
    return sorted((deployed & managed) - set(monitored_foreign_sources()))


@system_job(interval=RECONCILE_INTERVAL_MINUTES)
class ReconcileOrphansJob(JobRunner):
    """Periodically clear OpenNMS Foreign Sources NetBox no longer governs.

    Membership is a live query, so when the last member leaves a scope (object
    deleted, role/site changed, assignment removed) the Foreign Source drops out
    of ``monitored_foreign_sources()`` and its stale OpenNMS nodes would linger
    forever, still alerting. This drift reconciler enqueues an ``allow_empty``
    Remove for each orphan (which clears the nodes AND deletes the now-empty
    shell, so it doesn't recur). Opt-out via ``reconcile_orphans`` config.

    Best-effort: an OpenNMS outage logs and returns (no raise), so a transient
    failure doesn't mark the recurring system job failed.
    """

    class Meta:
        name = "OpenNMS reconcile orphans"

    def run(self, *args, **kwargs):
        if str(get_plugin_config(PLUGIN_NAME, "reconcile_orphans")).lower() != "true":
            self.logger.info("reconcile_orphans disabled — skipping.")
            return
        try:
            with OpenNMSClient.from_config() as client:
                orphans = orphaned_foreign_sources(client)
        except OpenNMSError as exc:
            self.logger.warning(f"reconcile skipped — OpenNMS error: {exc}")
            return
        if not orphans:
            self.logger.info("reconcile: no orphaned Foreign Sources.")
            return
        for foreign_source in orphans:
            SyncForeignSourceJob.enqueue_sync(foreign_source, allow_empty=True)
            self.logger.info(
                f"reconcile: enqueued Remove for orphaned Foreign Source "
                f"{foreign_source}."
            )
