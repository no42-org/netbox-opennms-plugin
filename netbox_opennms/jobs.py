# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Background jobs — render-and-replace a Foreign Source against OpenNMS (AD-4/5/6).

``SyncForeignSourceJob`` is the orchestration that turns rendered XML (the pure
``translation/`` layer) into a real OpenNMS push, through the ``OpenNMSClient``
port (AD-2). It runs in a NetBox ``JobRunner`` so it never blocks a request
(AD-4), re-renders the *complete* requisition for the affected Foreign Source
from all currently-enabled profiles and re-imports (AD-5), and serializes per
Foreign Source via a Postgres advisory lock so two syncs cannot race (AD-6).

Outcome maps to the NetBox ``Job`` lifecycle (AD-12): enqueued ``pending`` is the
caller's *submitted*; a clean return is *succeeded-accepted*; a render or port
error raises ``JobFailed`` → *failed*. A bare ``202`` from import is "accepted
for import", never "provisioned" (v1 has no read-back).
"""

from contextlib import ExitStack

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from core.models import Job
from django.contrib.contenttypes.models import ContentType
from django_pglocks import advisory_lock
from netbox.jobs import JobRunner
from netbox.plugins import get_plugin_config

from .client import OpenNMSClient, OpenNMSError
from .derivation import foreign_source_for, validate_location_name
from .models import MonitoringProfile
from .translation import (
    RenderError,
    render_foreign_source_definition,
    render_requisition,
)
from .validation import validate_foreign_source

PLUGIN_NAME = "netbox_opennms"


def enabled_profiles_for(foreign_source):
    """Enabled MonitoringProfiles whose derived Foreign Source matches the arg.

    Derivation is delegated to the single owner ``foreign_source_for`` (AD-14).
    v1 scans all enabled profiles and derives each one's Foreign Source from its
    *live* role+site. ``last_synced_foreign_source`` (Story 3.2) is the FS a node
    was last imported into — the *old* value used to detect a move — NOT a
    queryable substitute for this derived scan, so the full scan stays (reducing
    it is bulk/observability scope, not this story).
    """
    profiles = []
    for profile in MonitoringProfile.objects.filter(enabled=True).prefetch_related(
        "additional_ips", "services"
    ):
        target = profile.assigned_object
        if target is None:
            continue
        try:
            derived = foreign_source_for(target)
        except TypeError:
            # Non-Device/VM target (limit_choices_to is form-only, so an
            # ORM/REST/import-created profile can slip through). It belongs to no
            # syncable Foreign Source — skip it rather than crash every sync
            # (Story 2.4 rejects such profiles upstream).
            continue
        if derived == foreign_source:
            profiles.append(profile)
    return profiles


def enabled_foreign_sources():
    """The sorted set of distinct Foreign Sources across all enabled profiles.

    One scan, same target guard as ``enabled_profiles_for`` (skip ``None`` /
    non-Device-VM). Used by the bulk / "Sync all" actions to fan out one job per
    Foreign Source (AD-5: render-and-replace is per whole Foreign Source).
    """
    foreign_sources = set()
    for profile in MonitoringProfile.objects.filter(enabled=True):
        target = profile.assigned_object
        if target is None:
            continue
        try:
            foreign_sources.add(foreign_source_for(target))
        except TypeError:
            continue
    return sorted(foreign_sources)


def unknown_locations(client, profiles):
    """Explicit profile locations OpenNMS doesn't know (Story 4.1, FR-5).

    Pure given a ``client`` (no connection management, so it tests against a fake):
    returns the sorted distinct ``profile.location`` values that are non-empty and
    absent from ``client.list_locations()`` — each has no registered Minion, so the
    node is never polled. Skips the port call entirely when no profile sets an
    explicit location. Callers run this best-effort and swallow ``OpenNMSError``
    (AD-16).
    """
    wanted = {profile.location for profile in profiles if profile.location}
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
        pending check is best-effort dedup (there is a small check-then-enqueue
        window that the lock + render-and-replace idempotency absorb).

        The job is keyed by Foreign Source through its ``name`` (not a NetBox
        object link — NetBox only allows ``Job`` against job-registered models;
        per-object linkage/last-sync display is Story 4.2). Outcome lives on the
        ``Job`` itself (status + log).
        """
        # The "(remove)" marker keeps a Remove (allow_empty) from coalescing into
        # a pending Sync that would refuse the empty requisition (Story 3.1).
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
        profiles = enabled_profiles_for(foreign_source)

        # AD-10 move detection: a profile whose stored last-synced Foreign Source
        # differs from the FS it now derives to (role/site changed) has MOVED. Its
        # old FS must be re-rendered without it, or the node is orphaned there and
        # duplicated here. Collect those old FSs (a set — several nodes may share
        # one), sorted for deterministic, deadlock-free lock acquisition.
        old_foreign_sources = sorted(
            {
                profile.last_synced_foreign_source
                for profile in profiles
                if profile.last_synced_foreign_source
                and profile.last_synced_foreign_source != foreign_source
            }
        )

        # Validate config once for the whole job (it applies to every leg).
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

        # AD-6: serialize over the whole SET of affected Foreign Sources (the new
        # one plus any old ones a move touches), acquiring locks in sorted name
        # order so a move and a concurrent sync of a shared FS cannot deadlock.
        affected = sorted({foreign_source, *old_foreign_sources})
        with ExitStack() as locks:
            for fs in affected:
                locks.enter_context(advisory_lock(f"netbox_opennms:fs:{fs}"))

            # Old-FS legs FIRST (remove the moved node), each allowed to push an
            # empty requisition because the moved node may have been the last one
            # there — an intentional empty, like Remove (Story 3.1).
            for old_fs in old_foreign_sources:
                self._render_and_replace(
                    old_fs,
                    enabled_profiles_for(old_fs),
                    allow_empty=True,
                    default_location=default_location,
                    rescan=rescan,
                )

            # New/current FS leg (add the node). Honors the caller's allow_empty
            # so a plain Sync of an emptied FS still skips (Story 1.7), while a
            # Remove (allow_empty=True) pushes the intentional empty requisition.
            self._render_and_replace(
                foreign_source,
                profiles,
                allow_empty=allow_empty,
                default_location=default_location,
                rescan=rescan,
            )

    def _render_and_replace(
        self, foreign_source, profiles, allow_empty, default_location, rescan
    ):
        """Render-and-replace ONE Foreign Source (AD-5). Returns True if a push
        happened, False if skipped (no profiles and not allow_empty).

        Caller holds the advisory lock(s) and has already validated config; this
        validates the FS's own intent (AD-12 safety net), renders, and pushes in
        AD-11 order through the port.
        """
        if not profiles and not allow_empty:
            # A Sync must never mass-delete. An empty requisition would tell
            # OpenNMS to remove every node in the Foreign Source — that is the
            # deliberate Remove/Move path (allow_empty), not Sync. The trigger was
            # enabled at enqueue; reaching here means it was disabled/deleted in
            # the meantime — skip rather than wipe live monitoring.
            self.logger.info(
                f"nothing to sync for {foreign_source} (no enabled profiles) "
                "— skipped; use Remove to clear the Foreign Source."
            )
            return False

        # Re-validate intent as a safety net (FR-8): the view blocks on errors,
        # but non-UI triggers must fail cleanly too, not push bad intent (AD-12).
        validation = validate_foreign_source(foreign_source, profiles)
        if validation.errors:
            for error in validation.errors:
                self.logger.error(error)
            raise JobFailed()

        try:
            fs_xml = render_foreign_source_definition(foreign_source)
            requisition_xml = render_requisition(
                foreign_source, profiles, default_location=default_location
            )
        except RenderError as exc:
            self.logger.error(f"Cannot render {foreign_source}: {exc}")
            raise JobFailed() from exc

        try:
            with OpenNMSClient.from_config() as client:
                # Order matters (AD-11): definition first, then requisition, then
                # import.
                client.post_foreign_source(fs_xml)
                client.post_requisition(requisition_xml)
                client.import_requisition(foreign_source, rescan_existing=rescan)
                # Best-effort advisory (FR-5/AD-16): warn if a chosen location has
                # no Minion. Reuses the open client; a failure here must NEVER turn
                # a succeeded import into a failure, so swallow everything (a
                # malformed locations response raises ValueError, not OpenNMSError).
                try:
                    for location in unknown_locations(client, profiles):
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

        # AD-10: record where these nodes now live, ONLY after the import is
        # accepted. Every successful leg does this — the new-FS leg so a future
        # role/site change is detected as a move, AND the old-FS leg so a node
        # first imported there via a side-effect move-leg is also tracked (else
        # its own later move would go undetected and orphan it). A crash before
        # this leaves the prior value, so the next Sync re-runs the move
        # idempotently. Bulk update: job-owned bookkeeping, no signals.
        if profiles:
            MonitoringProfile.objects.filter(
                pk__in=[profile.pk for profile in profiles]
            ).update(last_synced_foreign_source=foreign_source)

        return True


def latest_sync_job(foreign_sources):
    """The most recent sync/remove ``Job`` across one or more Foreign Sources
    (Story 4.2, NFR-4).

    The Job is the audit record (user, timestamps, status, log, error). Matches
    both the sync and remove name forms via the shared ``job_name`` so the lookup
    can never drift from what ``enqueue_sync`` wrote. Accepts a single FS name or
    an iterable (a moved node spans its old + new FS). ``None`` if none found.
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


def sync_outcome(job, is_removal=False, enabled=True):
    """Map a sync ``Job`` to the honest outcome vocabulary (AD-12), or ``None``.

    Returns ``(label, color)``: ``submitted`` (pending/scheduled/running);
    ``succeeded-accepted`` (completed — accepted for import, never "provisioned");
    ``removed`` (a completed Remove, or a disabled profile — the node is excluded
    from the requisition and deleted on import); ``failed`` (errored/failed). The
    caller passes ``is_removal`` (the latest action was a Remove) and ``enabled``.
    """
    if job is None:
        return None
    if job.status in JobStatusChoices.ENQUEUED_STATE_CHOICES:
        return ("submitted", "cyan")
    if job.status == JobStatusChoices.STATUS_COMPLETED:
        if is_removal or not enabled:
            return ("removed", "gray")
        return ("succeeded-accepted", "green")
    return ("failed", "red")


def sync_status_for(target):
    """Last-sync state for a monitored Device/VM — the single source the profile
    detail and the Device/VM template extension both render (Story 4.2).

    Reflects the node's *actual* provisioned location: it looks up the latest Job
    across both where the node was last imported (``last_synced_foreign_source``)
    and where it now derives (a pending move targets the derived FS), so a node
    that moved role/site isn't mislabeled "Never synced" or shown a stale FS's
    job. ``move_pending`` is set when the derived FS differs from the last-synced
    one. Returns ``None`` for a missing or non-Device/VM target.
    """
    if target is None:
        return None
    try:
        derived = foreign_source_for(target)
    except TypeError:
        return None
    content_type = ContentType.objects.get_for_model(target)
    profile = MonitoringProfile.objects.filter(
        assigned_object_type=content_type, assigned_object_id=target.pk
    ).first()
    last_synced = profile.last_synced_foreign_source if profile else ""
    enabled = profile.enabled if profile else True
    # The node's relevant Foreign Sources, newest job wins. dict.fromkeys keeps
    # order and de-dups when last_synced == derived (the steady state).
    foreign_sources = [fs for fs in dict.fromkeys([last_synced, derived]) if fs]
    job = latest_sync_job(foreign_sources) if foreign_sources else None
    is_removal = bool(job) and job.name.endswith(" (remove)")
    return {
        "foreign_source": derived,
        "last_synced_foreign_source": last_synced,
        "move_pending": bool(last_synced and last_synced != derived),
        "job": job,
        "outcome": sync_outcome(job, is_removal=is_removal, enabled=enabled),
    }
