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
so a moved object simply belongs to a different Foreign Source on the next sync.
Re-rendering the object's *old* Foreign Source to drop it is a 'sync all'
/ preview concern (it re-renders every governed Foreign Source), not per-job
move-tracking.

Outcome maps to the NetBox ``Job`` lifecycle (AD-12): a clean return is
*succeeded-accepted*; a render or port error raises ``JobFailed`` → *failed*. A
bare ``202`` from import is "accepted for import", never "provisioned".
"""

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from core.models import Job
from django.contrib.contenttypes.models import ContentType
from django_pglocks import advisory_lock
from netbox.jobs import JobRunner
from netbox.plugins import get_plugin_config

from .client import OpenNMSClient, OpenNMSError
from .derivation import foreign_source_for, validate_location_name
from .membership import (
    governing_assignment,
    monitored_foreign_sources,
    resolve,
)
from .models import MonitoringOverride
from .translation import (
    RenderError,
    render_foreign_source_definition,
    render_requisition,
)
from .validation import validate_resolution

PLUGIN_NAME = "netbox_opennms"


def enabled_foreign_sources():
    """The sorted set of Foreign Sources with a governing assignment + members.

    Used by the bulk / 'Sync all' actions to fan out one job per Foreign Source
    (AD-5: render-and-replace is per whole Foreign Source).
    """
    return monitored_foreign_sources()


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
                f"{foreign_source} has no governing assignment — skipped."
            )
            return False

        if resolution is not None and not resolution.nodes and not allow_empty:
            # A Sync must never mass-delete. An empty requisition tells OpenNMS to
            # remove every node — the deliberate Remove path (allow_empty), not
            # Sync. Surface why nothing resolved, then skip rather than wipe.
            for warning in resolution.warnings:
                self.logger.warning(warning)
            self.logger.info(
                f"nothing to sync for {foreign_source} (no monitorable members) "
                "— skipped; use Remove to clear the Foreign Source."
            )
            return False

        # Re-validate intent as a safety net (FR-8): the view blocks on errors,
        # but non-UI triggers must fail cleanly too, not push bad intent (AD-12).
        validation = validate_resolution(resolution)
        for warning in validation.warnings:
            self.logger.warning(warning)
        if validation.errors:
            for error in validation.errors:
                self.logger.error(error)
            raise JobFailed()

        nodes = resolution.nodes if resolution is not None else []
        locations = {default_location}
        if resolution is not None:
            locations.add(resolution.assignment.location)
            locations.update(node.location for node in nodes)

        try:
            requisition_xml = render_requisition(
                foreign_source, nodes, default_location=default_location
            )
            fs_xml = (
                render_foreign_source_definition(resolution.assignment.profile)
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
                # Best-effort advisory (FR-5/AD-16): warn on an unknown location.
                try:
                    for location in unknown_locations(client, locations):
                        self.logger.warning(
                            f"Location {location!r} is not a known OpenNMS "
                            "monitoring location — no Minion will poll it."
                        )
                except Exception:
                    pass
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

    Derives the object's Foreign Source, looks up whether a Monitoring Assignment
    governs it (and whether an override excludes it), and attaches the latest Job
    for that Foreign Source. Returns ``None`` for a missing or non-Device/VM
    target.
    """
    if target is None:
        return None
    try:
        foreign_source = foreign_source_for(target)
    except TypeError:
        return None
    assignment = governing_assignment(foreign_source)
    content_type = ContentType.objects.get_for_model(type(target))
    override = MonitoringOverride.objects.filter(
        assigned_object_type=content_type, assigned_object_id=target.pk
    ).first()
    excluded = bool(override and override.exclude)
    governed = assignment is not None and not excluded
    job = latest_sync_job(foreign_source)
    is_removal = bool(job) and job.name.endswith(" (remove)")
    return {
        "foreign_source": foreign_source,
        "assignment": assignment,
        "governed": governed,
        "excluded": excluded,
        "job": job,
        "outcome": sync_outcome(job, is_removal=is_removal, governed=governed),
    }
