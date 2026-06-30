# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for last-sync observability (Epic 5) — Jobs + membership as state."""

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
from django.test import RequestFactory, TestCase
from ipam.models import IPAddress

from netbox_opennms.jobs import (
    SyncForeignSourceJob,
    sync_outcome,
    sync_status_for,
)
from netbox_opennms.models import (
    MonitoringAssignment,
    MonitoringOverride,
    MonitoringProfile,
)
from netbox_opennms.template_content import (
    DeviceSyncStatusPanel,
    VirtualMachineSyncStatusPanel,
)

User = get_user_model()
FS = "netbox.raleigh.router"


class ObservabilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Raleigh", slug="raleigh")
        cls.role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.profile = MonitoringProfile.objects.create(name="Network device")
        cls.assignment = MonitoringAssignment.objects.create(
            profile=cls.profile, site=cls.site, role=cls.role
        )
        cls.device = cls._device("rtr-1", "10.0.0.1/24")
        cls.user = User.objects.create_superuser(username="super", password="pw")

    @classmethod
    def _device(cls, name, ip, role=None, site=None):
        device = Device.objects.create(
            name=name, device_type=cls.dt, role=role or cls.role, site=site or cls.site
        )
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        address = IPAddress.objects.create(address=ip, assigned_object=iface)
        device.primary_ip4 = address
        device.save()
        return device

    def _completed_job(self, foreign_source=FS, allow_empty=False):
        job = SyncForeignSourceJob.enqueue_sync(foreign_source, allow_empty=allow_empty)
        Job.objects.filter(pk=job.pk).update(status=JobStatusChoices.STATUS_COMPLETED)
        return Job.objects.get(pk=job.pk)

    # --- sync_status_for ----------------------------------------------------

    def test_governed_device(self):
        status = sync_status_for(self.device)
        self.assertEqual(status["foreign_source"], FS)
        self.assertTrue(status["governed"])
        self.assertEqual(status["assignment"], self.assignment)
        self.assertIsNone(status["job"])

    def test_excluded_override(self):
        MonitoringOverride.objects.create(assigned_object=self.device, exclude=True)
        status = sync_status_for(self.device)
        self.assertTrue(status["excluded"])
        self.assertFalse(status["governed"])

    def test_ungoverned_device(self):
        other = self._device(
            "srv-1",
            "10.0.0.2/24",
            role=DeviceRole.objects.create(name="Server", slug="server"),
        )
        status = sync_status_for(other)
        self.assertFalse(status["governed"])
        self.assertIsNone(status["assignment"])

    def test_completed_job_outcome(self):
        self._completed_job()
        status = sync_status_for(self.device)
        self.assertEqual(status["outcome"], ("succeeded-accepted", "green"))

    def test_none_target(self):
        self.assertIsNone(sync_status_for(None))

    # --- sync_outcome -------------------------------------------------------

    def test_sync_outcome_states(self):
        self.assertIsNone(sync_outcome(None))
        from unittest import mock

        submitted = mock.Mock(status=JobStatusChoices.STATUS_PENDING)
        self.assertEqual(sync_outcome(submitted)[0], "submitted")
        done = mock.Mock(status=JobStatusChoices.STATUS_COMPLETED)
        self.assertEqual(sync_outcome(done)[0], "succeeded-accepted")
        self.assertEqual(sync_outcome(done, governed=False)[0], "removed")
        self.assertEqual(sync_outcome(done, is_removal=True)[0], "removed")
        failed = mock.Mock(status=JobStatusChoices.STATUS_ERRORED)
        self.assertEqual(sync_outcome(failed)[0], "failed")

    # --- template extension panel ------------------------------------------

    def _panel_html(self, panel_cls, obj):
        request = RequestFactory().get("/")
        request.user = self.user
        panel = panel_cls({"object": obj, "request": request})
        return panel.right_page()

    def test_panel_renders_for_governed(self):
        html = self._panel_html(DeviceSyncStatusPanel, self.device)
        self.assertIn("OpenNMS Sync Status", html)
        self.assertIn(FS, html)

    def test_panel_empty_for_ungoverned(self):
        other = self._device(
            "srv-2",
            "10.0.0.3/24",
            role=DeviceRole.objects.create(name="Server", slug="server"),
        )
        self.assertEqual(self._panel_html(DeviceSyncStatusPanel, other), "")

    def test_panel_model_targets(self):
        self.assertEqual(DeviceSyncStatusPanel.models, ["dcim.device"])
        self.assertEqual(
            VirtualMachineSyncStatusPanel.models, ["virtualization.virtualmachine"]
        )
