# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for last-sync observability (Story 4.2) — Jobs surfaced as state."""

from datetime import timedelta
from unittest import mock

from core.choices import JobStatusChoices
from core.models import Job
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from ipam.models import IPAddress
from virtualization.models import (
    Cluster,
    ClusterType,
    VirtualMachine,
    VMInterface,
)

from netbox_opennms.derivation import foreign_source_for
from netbox_opennms.jobs import (
    SyncForeignSourceJob,
    latest_sync_job,
    sync_outcome,
    sync_status_for,
)
from netbox_opennms.models import MonitoringProfile
from netbox_opennms.template_content import (
    DeviceSyncStatusPanel,
    VirtualMachineSyncStatusPanel,
)

User = get_user_model()
FS = "netbox.raleigh.router"


class ObservabilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.device = Device.objects.create(
            name="rtr-1", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(device=cls.device, name="eth0", type="virtual")
        ip = IPAddress.objects.create(address="10.0.0.1/24", assigned_object=iface)
        cls.profile = MonitoringProfile.objects.create(
            assigned_object=cls.device, management_ip=ip
        )
        cls.bare_device = Device.objects.create(
            name="rtr-bare", device_type=dt, role=role, site=site
        )
        cls.user = User.objects.create_superuser(username="super", password="pw")

    def _job(self, status, allow_empty=False, error="", foreign_source=FS):
        # Create via the real enqueue path (sets all required Job fields), then
        # flip to a terminal/other status for the test.
        job = SyncForeignSourceJob.enqueue_sync(
            foreign_source, user=self.user, allow_empty=allow_empty
        )
        fields = {"status": status, "error": error}
        if status in JobStatusChoices.TERMINAL_STATE_CHOICES:
            fields["completed"] = timezone.now()
        Job.objects.filter(pk=job.pk).update(**fields)
        job.refresh_from_db()
        return job

    # --- helpers -----------------------------------------------------------

    def test_job_name_matches_enqueue(self):
        job = SyncForeignSourceJob.enqueue_sync(FS, user=self.user)
        self.assertEqual(job.name, SyncForeignSourceJob.job_name(FS))
        Job.objects.filter(pk=job.pk).update(status=JobStatusChoices.STATUS_COMPLETED)
        remove = SyncForeignSourceJob.enqueue_sync(
            FS, user=self.user, allow_empty=True
        )
        self.assertEqual(
            remove.name, SyncForeignSourceJob.job_name(FS, allow_empty=True)
        )
        self.assertTrue(remove.name.endswith(" (remove)"))

    def test_latest_sync_job_none_when_never_synced(self):
        self.assertIsNone(latest_sync_job(FS))

    def test_latest_sync_job_returns_newest_and_scopes_to_fs(self):
        old = self._job(JobStatusChoices.STATUS_FAILED)
        Job.objects.filter(pk=old.pk).update(
            created=timezone.now() - timedelta(hours=1)
        )
        new = self._job(JobStatusChoices.STATUS_COMPLETED)
        # A Job for a DIFFERENT Foreign Source must not match.
        SyncForeignSourceJob.enqueue_sync("netbox.durham.router", user=self.user)
        self.assertEqual(latest_sync_job(FS).pk, new.pk)

    def test_sync_outcome_mapping(self):
        self.assertIsNone(sync_outcome(None))
        for status in (
            JobStatusChoices.STATUS_PENDING,
            JobStatusChoices.STATUS_SCHEDULED,
            JobStatusChoices.STATUS_RUNNING,
        ):
            self.assertEqual(sync_outcome(mock.Mock(status=status))[0], "submitted")
        self.assertEqual(
            sync_outcome(mock.Mock(status=JobStatusChoices.STATUS_COMPLETED))[0],
            "succeeded-accepted",
        )
        for status in (
            JobStatusChoices.STATUS_ERRORED,
            JobStatusChoices.STATUS_FAILED,
        ):
            self.assertEqual(sync_outcome(mock.Mock(status=status))[0], "failed")
        # A completed Remove, or a disabled profile, reads as "removed" (the node
        # is excluded from the requisition), not green "succeeded-accepted".
        completed = mock.Mock(status=JobStatusChoices.STATUS_COMPLETED)
        self.assertEqual(sync_outcome(completed, is_removal=True)[0], "removed")
        self.assertEqual(sync_outcome(completed, enabled=False)[0], "removed")

    def test_sync_status_for_non_device_vm_is_none(self):
        site = Site.objects.get(slug="raleigh")
        self.assertIsNone(sync_status_for(site))
        self.assertIsNone(sync_status_for(None))

    # --- profile detail page ----------------------------------------------

    def _profile_url(self):
        return reverse(
            "plugins:netbox_opennms:monitoringprofile", args=[self.profile.pk]
        )

    def test_profile_detail_shows_succeeded(self):
        self._job(JobStatusChoices.STATUS_COMPLETED)
        self.client.force_login(self.user)
        response = self.client.get(self._profile_url())
        self.assertContains(response, "OpenNMS Sync Status")
        self.assertContains(response, "succeeded-accepted")

    def test_profile_detail_shows_failure_with_error(self):
        self._job(JobStatusChoices.STATUS_FAILED, error="boom detail")
        self.client.force_login(self.user)
        response = self.client.get(self._profile_url())
        self.assertContains(response, "failed")
        self.assertContains(response, "boom detail")

    def test_profile_detail_never_synced(self):
        self.client.force_login(self.user)
        response = self.client.get(self._profile_url())
        self.assertContains(response, "Never synced")

    def test_profile_detail_shows_triggering_user(self):
        self._job(JobStatusChoices.STATUS_COMPLETED)
        self.client.force_login(self.user)  # superuser → has core.view_job
        response = self.client.get(self._profile_url())
        self.assertContains(response, self.user.username)

    # --- move / remove / disabled correctness (review patches) -------------

    def test_status_reflects_old_fs_and_flags_move_pending(self):
        # Node was synced under an OLD Foreign Source; its role/site now derives a
        # different FS. The panel must show the OLD FS's outcome (where the node
        # actually lives) + a move-pending flag — not "Never synced".
        old_fs = "netbox.durham.router"
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source=old_fs
        )
        job = self._job(
            JobStatusChoices.STATUS_COMPLETED, foreign_source=old_fs
        )
        status = sync_status_for(self.device)
        self.assertTrue(status["move_pending"])
        self.assertEqual(status["job"].pk, job.pk)
        self.assertEqual(status["outcome"][0], "succeeded-accepted")

    def test_completed_remove_shows_removed(self):
        self._job(JobStatusChoices.STATUS_COMPLETED, allow_empty=True)
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)
        self.assertEqual(sync_status_for(self.device)["outcome"][0], "removed")

    def test_disabled_profile_shows_removed(self):
        self._job(JobStatusChoices.STATUS_COMPLETED)
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)
        self.assertEqual(sync_status_for(self.device)["outcome"][0], "removed")

    # --- Device / VM template extension -----------------------------------

    def test_device_detail_shows_panel(self):
        self._job(JobStatusChoices.STATUS_COMPLETED)
        self.client.force_login(self.user)
        response = self.client.get(self.device.get_absolute_url())
        self.assertContains(response, "OpenNMS Sync Status")
        self.assertContains(response, "succeeded-accepted")

    def test_device_without_profile_renders_no_panel(self):
        self.client.force_login(self.user)
        response = self.client.get(self.bare_device.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "OpenNMS Sync Status")

    def test_extension_right_page_for_monitored_object(self):
        self._job(JobStatusChoices.STATUS_COMPLETED)
        html = DeviceSyncStatusPanel({"object": self.device}).right_page()
        self.assertIn("succeeded-accepted", html)

    def test_extension_right_page_empty_when_unmonitored(self):
        self.assertEqual(
            DeviceSyncStatusPanel({"object": self.bare_device}).right_page(), ""
        )

    def test_vm_extension_class_targets_virtualmachine(self):
        self.assertEqual(
            VirtualMachineSyncStatusPanel.models, ["virtualization.virtualmachine"]
        )

    def test_vm_extension_renders_panel(self):
        # Exercise the VM path end-to-end: foreign_source_for(vm) + the content-type
        # lookup + the shared partial render.
        role = DeviceRole.objects.get(slug="router")
        ctype = ClusterType.objects.create(name="CT", slug="ct")
        cluster = Cluster.objects.create(name="C1", type=ctype)
        vm = VirtualMachine.objects.create(name="vm-1", cluster=cluster, role=role)
        iface = VMInterface.objects.create(virtual_machine=vm, name="eth0")
        ip = IPAddress.objects.create(address="10.1.0.1/24", assigned_object=iface)
        MonitoringProfile.objects.create(assigned_object=vm, management_ip=ip)
        self._job(
            JobStatusChoices.STATUS_COMPLETED,
            foreign_source=foreign_source_for(vm),
        )

        html = VirtualMachineSyncStatusPanel({"object": vm}).right_page()
        self.assertIn("succeeded-accepted", html)

    def test_vm_extension_empty_when_unmonitored(self):
        role = DeviceRole.objects.get(slug="router")
        ctype = ClusterType.objects.create(name="CT2", slug="ct2")
        cluster = Cluster.objects.create(name="C2", type=ctype)
        vm = VirtualMachine.objects.create(name="vm-2", cluster=cluster, role=role)
        self.assertEqual(
            VirtualMachineSyncStatusPanel({"object": vm}).right_page(), ""
        )
