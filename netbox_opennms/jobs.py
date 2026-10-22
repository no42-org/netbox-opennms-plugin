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
from netbox.jobs import JobRunner, system_job
from netbox.plugins import get_plugin_config
from virtualization.models import VirtualMachine

# NetBox 4.7 moved from django-pglocks to django-pgware (netbox-community/netbox
# #22571); 4.6.x ships only the former. Both expose the same context manager.
try:
    from django_pg_utils import advisory_lock
except ImportError:  # NetBox < 4.7
    from django_pglocks import advisory_lock

from .client import OpenNMSClient, OpenNMSError
from .derivation import validate_location_name
from .membership import (
    matching_requisitions,
    monitored_foreign_sources,
    resolve,
)
from .models import DeployedForeignSource, MonitoringOverride, OpenNMSServer
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
    def job_name(cls, foreign_source, server_id, allow_empty=False):
        """The Job ``name`` for a Foreign Source on one server (owns the format).

        Shared by ``enqueue_sync`` (write) and ``latest_sync_job`` (read), so the
        observability lookup always matches what was enqueued. ``Job.name`` is
        max_length=200; the ``(remove)`` marker is budgeted INTO the cap so a long
        Foreign Source can't truncate it away (Story 3.1). The server id makes
        coalescing per server: a pending Sync to one server never swallows a
        Sync of the same Foreign Source to another.
        """
        suffix = " (remove)" if allow_empty else ""
        base = f"{cls.name}: {foreign_source} @{server_id}"
        return base[: 200 - len(suffix)] + suffix

    @classmethod
    def enqueue_sync(cls, foreign_source, server_id, user=None, allow_empty=False):
        """Enqueue a sync, coalescing a redundant pending sync for the same FS.

        The advisory lock in ``run`` is the hard race guard (AD-6); this skip-if-
        pending check is best-effort dedup. The ``(remove)`` marker keeps a Remove
        (allow_empty) from coalescing into a pending Sync that would refuse the
        empty requisition (Story 3.1).
        """
        job_name = cls.job_name(foreign_source, server_id, allow_empty=allow_empty)
        existing = Job.objects.filter(
            name=job_name,
            status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
        ).first()
        if existing is not None:
            return existing
        return cls.enqueue(
            name=job_name,
            foreign_source=foreign_source,
            server_id=server_id,
            user=user,
            allow_empty=allow_empty,
        )

    def run(self, foreign_source, server_id, allow_empty=False, **kwargs):
        server = OpenNMSServer.objects.filter(pk=server_id).first()
        if server is None:
            self.logger.error(
                f"Server #{server_id} for {foreign_source} no longer exists."
            )
            raise JobFailed()
        # Validate config once (it applies to the whole job).
        default_location = server.default_location
        if default_location:
            try:
                validate_location_name(default_location)
            except ValueError as exc:
                self.logger.error(
                    f"default_location of server {server.name!r} is invalid: {exc}"
                )
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
                server,
                allow_empty=allow_empty,
                default_location=default_location,
                rescan=rescan,
            )

    def _render_and_replace(
        self, foreign_source, server, allow_empty, default_location, rescan
    ):
        """Render-and-replace ONE Foreign Source (AD-5). Returns True if a push
        happened, False if skipped (not governed / empty and not allow_empty).

        Caller holds the advisory lock and has validated config; this resolves the
        membership, validates intent (AD-12 safety net), renders, and pushes.
        """
        resolution = resolve(foreign_source)
        # A Requisition moved to another server no longer governs this server's
        # copy: a Remove here tears that copy down instead of re-pushing it.
        if resolution is not None and resolution.requisition.server_id != server.pk:
            resolution = None

        if resolution is None and not allow_empty:
            self.logger.info(
                f"{foreign_source} has no Requisition on server {server.name!r} "
                "— skipped."
            )
            return False

        # Validate FIRST (FR-8/AD-12): conflicts (C1 freeze) and rejected filters
        # fail the job loudly even when they resolve to ZERO nodes — the quiet
        # empty-skip below must never swallow a blocking error (review #8). A
        # Remove is exempt from the rejected-filter block (the teardown escape
        # hatch); conflicts still block it.
        validation = validate_resolution(resolution, removing=allow_empty)
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
            with OpenNMSClient.for_server(server) as client:
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
                    DeployedForeignSource.objects.get_or_create(
                        server=server, name=foreign_source
                    )
                # Best-effort advisory (FR-5/AD-16): warn on an unknown location.
                try:
                    for location in unknown_locations(client, locations):
                        self.logger.warning(
                            f"Location {location!r} is not a known OpenNMS "
                            "monitoring location — no Minion will poll it."
                        )
                except Exception as exc:
                    # Advisory only: never fail a sync because the check errored.
                    self.logger.debug(f"location advisory skipped: {exc}")
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
                            server=server, name=foreign_source
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


def latest_sync_job(targets):
    """The most recent sync/remove ``Job`` across ``(foreign_source, server_id)`` pairs.

    The Job is the audit record (user, timestamps, status, log, error). Matches
    both the sync and remove name forms via the shared ``job_name`` so the lookup
    can never drift from what ``enqueue_sync`` wrote. ``None`` if none found.
    """
    names = []
    for foreign_source, server_id in targets:
        for allow_empty in (False, True):
            names.append(
                SyncForeignSourceJob.job_name(
                    foreign_source, server_id, allow_empty=allow_empty
                )
            )
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
    """Last-sync state for a Device/VM, one row per OpenNMS server — the single
    source the Device/VM template extension renders (Story 4.2).

    Groups every Requisition whose filter matches the object by its server. Per
    server: exactly one match = governed by it; several = **conflicted** between
    them (C1, per server — the panel names the parties). Each row also reports
    whether an override excludes the object and attaches the latest Job for that
    server's matching Foreign Sources. Rows are ordered by server name; a server
    appears only while it has a current match (sync history without a match is
    not looked up). Returns ``[]`` for an unmatched object and ``None`` for a
    missing or non-Device/VM target.
    """
    if target is None or not isinstance(target, (Device, VirtualMachine)):
        return None
    # matching_requisitions includes matches for an excluded object, so the panel
    # keeps the Foreign Source + last-sync Job (review #9); `governed` (actively
    # monitored) then excludes the excluded ones. An excluded object never
    # conflicts (C3) — its first match is shown as the (inactive) requisition.
    groups = {}
    for requisition in matching_requisitions(target):
        groups.setdefault(requisition.server_id, []).append(requisition)
    if not groups:
        return []
    content_type = ContentType.objects.get_for_model(type(target))
    override = MonitoringOverride.objects.filter(
        assigned_object_type=content_type, assigned_object_id=target.pk
    ).first()
    excluded = bool(override and override.exclude)
    # RD-6/h: a governed object with no management IP provisions as an interface-less
    # (inventory-only) node — surface it as a Warning on the panel (not blocking).
    management = None
    if override is not None and override.management_ip_id:
        management = override.management_ip
    if management is None:
        management = getattr(target, "primary_ip", None)
    rows = []
    for server in OpenNMSServer.objects.filter(pk__in=groups).order_by("name"):
        matches = groups[server.pk]
        conflicted = len(matches) >= 2 and not excluded
        requisition = None
        if len(matches) == 1 or excluded:
            requisition = matches[0]
        governed = requisition is not None and not excluded and not conflicted
        # Look up the last sync across EVERY matching Foreign Source on this server
        # (reviews #3/#9): a conflicted/excluded multi-match object's node and Job
        # history live under whichever requisition actually synced it.
        job = latest_sync_job([(r.name, server.pk) for r in matches])
        is_removal = bool(job) and job.name.endswith(" (remove)")
        # For the OUTCOME label a conflicted object still counts as governed: its
        # node is frozen-in-place, not removed — labelling a completed sync
        # "removed" would contradict the C1 freeze contract (review #2).
        rows.append(
            {
                "server": server,
                "foreign_source": requisition.name if requisition else None,
                "requisition": requisition,
                "governed": governed,
                "excluded": excluded,
                "warning_no_management_ip": governed and management is None,
                "conflicts": sorted(r.name for r in matches) if conflicted else [],
                "job": job,
                "outcome": sync_outcome(
                    job, is_removal=is_removal, governed=governed or conflicted
                ),
            }
        )
    return rows


def orphaned_foreign_sources(client, server):
    """Foreign Sources OpenNMS holds that NetBox manages but no longer monitors.

    The reconciliation core (pure given a ``client``, so it tests against a fake).
    Requisition names are user-chosen, so ownership is tracked in
    ``DeployedForeignSource`` (written on each successful push) rather than a
    ``netbox.`` name prefix (review #4): orphans = (OpenNMS requisitions ∩ our
    managed names) − currently monitored. Scoped to managed names, so a requisition
    NetBox never created is never touched. Catches every drift cause uniformly:
    last-member departure, membership move, requisition rename, deletion, and a
    Requisition moved to another server. Scoped to ``server`` throughout.
    """
    deployed = set(client.list_requisition_names())
    managed = set(
        DeployedForeignSource.objects.filter(server=server).values_list(
            "name", flat=True
        )
    )
    monitored = {
        name
        for server_id, name in monitored_foreign_sources()
        if server_id == server.pk
    }
    return sorted((deployed & managed) - monitored)


@system_job(interval=RECONCILE_INTERVAL_MINUTES)
class ReconcileOrphansJob(JobRunner):
    """Periodically clear OpenNMS Foreign Sources NetBox no longer governs.

    Membership is a live query, so when the last member leaves a scope (object
    deleted, role/site changed, assignment removed) the Foreign Source drops out
    of ``monitored_foreign_sources()`` and its stale OpenNMS nodes would linger
    forever, still alerting. This drift reconciler enqueues an ``allow_empty``
    Remove for each orphan (which clears the nodes AND deletes the now-empty
    shell, so it doesn't recur). Opt-out via ``reconcile_orphans`` config.

    Runs once per ``OpenNMSServer``. Best-effort: an OpenNMS outage on one
    server logs a warning and moves on to the next (no raise), so a transient
    failure doesn't mark the recurring system job failed.
    """

    class Meta:
        name = "OpenNMS reconcile orphans"

    def run(self, *args, **kwargs):
        if str(get_plugin_config(PLUGIN_NAME, "reconcile_orphans")).lower() != "true":
            self.logger.info("reconcile_orphans disabled — skipping.")
            return
        for server in OpenNMSServer.objects.order_by("name"):
            try:
                with OpenNMSClient.for_server(server) as client:
                    orphans = orphaned_foreign_sources(client, server)
            except OpenNMSError as exc:
                self.logger.warning(
                    f"reconcile skipped for server {server.name!r} — "
                    f"OpenNMS error: {exc}"
                )
                continue
            if not orphans:
                self.logger.info(
                    f"reconcile: no orphaned Foreign Sources on {server.name!r}."
                )
                continue
            for foreign_source in orphans:
                SyncForeignSourceJob.enqueue_sync(
                    foreign_source, server_id=server.pk, allow_empty=True
                )
                self.logger.info(
                    f"reconcile: enqueued Remove for orphaned Foreign Source "
                    f"{foreign_source} on {server.name!r}."
                )
