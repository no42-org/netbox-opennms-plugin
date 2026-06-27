# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
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

from core.choices import JobStatusChoices
from core.exceptions import JobFailed
from core.models import Job
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
    v1 scans all enabled profiles (no stored Foreign Source column — that arrives
    with ``last_synced_foreign_source`` in Story 3.2).
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


class SyncForeignSourceJob(JobRunner):
    """Render-and-replace one Foreign Source against OpenNMS, serialized per FS."""

    class Meta:
        name = "OpenNMS sync"

    @classmethod
    def enqueue_sync(cls, foreign_source, user=None):
        """Enqueue a sync, coalescing a redundant pending sync for the same FS.

        The advisory lock in ``run`` is the hard race guard (AD-6); this skip-if-
        pending check is best-effort dedup (there is a small check-then-enqueue
        window that the lock + render-and-replace idempotency absorb).

        The job is keyed by Foreign Source through its ``name`` (not a NetBox
        object link — NetBox only allows ``Job`` against job-registered models;
        per-object linkage/last-sync display is Story 4.2). Outcome lives on the
        ``Job`` itself (status + log).
        """
        # Job.name is max_length=200; cap so two max-length slugs can't overflow.
        job_name = f"{cls.name}: {foreign_source}"[:200]
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
        )

    def run(self, foreign_source, **kwargs):
        # AD-6: serialize all work for this Foreign Source. wait=True blocks until
        # acquired; the lock auto-releases at block exit.
        with advisory_lock(f"netbox_opennms:fs:{foreign_source}"):
            profiles = enabled_profiles_for(foreign_source)

            if not profiles:
                # A Sync must never mass-delete. An empty requisition would tell
                # OpenNMS to remove every node in the Foreign Source — that is the
                # deliberate Remove path (Story 3.1), not Sync. The trigger was
                # enabled at enqueue; reaching here means it was disabled/deleted
                # in the meantime — skip rather than wipe live monitoring.
                self.logger.info(
                    f"nothing to sync for {foreign_source} (no enabled profiles) "
                    "— skipped; use Remove to clear the Foreign Source."
                )
                return

            # Re-validate intent as a safety net (FR-8): the view blocks on
            # errors, but non-UI triggers must fail cleanly too, not push bad
            # intent (AD-12).
            validation = validate_foreign_source(foreign_source, profiles)
            if validation.errors:
                for error in validation.errors:
                    self.logger.error(error)
                raise JobFailed()

            default_location = get_plugin_config(PLUGIN_NAME, "default_location")
            if default_location:
                try:
                    validate_location_name(default_location)
                except ValueError as exc:
                    self.logger.error(f"Configured default_location is invalid: {exc}")
                    raise JobFailed() from exc
            try:
                fs_xml = render_foreign_source_definition(foreign_source)
                requisition_xml = render_requisition(
                    foreign_source, profiles, default_location=default_location
                )
            except RenderError as exc:
                self.logger.error(f"Cannot render {foreign_source}: {exc}")
                raise JobFailed() from exc

            rescan = str(get_plugin_config(PLUGIN_NAME, "import_mode")).strip().lower()
            if rescan not in ("true", "false", "dbonly"):
                self.logger.error(
                    f"Invalid import_mode {rescan!r} (expected true/false/dbonly)."
                )
                raise JobFailed()

            try:
                with OpenNMSClient.from_config() as client:
                    # Order matters (AD-11): definition first, then requisition,
                    # then import.
                    client.post_foreign_source(fs_xml)
                    client.post_requisition(requisition_xml)
                    client.import_requisition(foreign_source, rescan_existing=rescan)
            except OpenNMSError as exc:
                self.logger.error(f"OpenNMS sync of {foreign_source} failed: {exc}")
                raise JobFailed() from exc

            self.logger.info(
                f"succeeded-accepted: import of {foreign_source} accepted by "
                "OpenNMS (HTTP 2xx/202 — submitted for import, not verified)."
            )
