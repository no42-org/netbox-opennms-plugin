# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the OpenNMS sync background job (mocked port, no network)."""

from unittest import mock

from core.exceptions import JobFailed
from core.models import Job
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.test import TestCase
from ipam.models import IPAddress

from netbox_opennms.client import OpenNMSHTTPError
from netbox_opennms.jobs import SyncForeignSourceJob, enabled_profiles_for
from netbox_opennms.models import MonitoringProfile
from netbox_opennms.translation import (
    render_foreign_source_definition,
    render_requisition,
)

FS = "netbox:raleigh:router"


class SyncForeignSourceJobTest(TestCase):
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

    def _runner(self):
        return SyncForeignSourceJob(job=mock.Mock())

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_posts_fs_then_requisition_then_import(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)

        self._runner().run(foreign_source=FS)

        # The three writes happen exactly once, in the AD-11 order.
        call_names = [c[0] for c in client.mock_calls]
        self.assertEqual(
            call_names,
            ["post_foreign_source", "post_requisition", "import_requisition"],
        )
        # The port receives exactly the rendered XML (job → port wiring).
        self.assertEqual(
            client.post_foreign_source.call_args.args[0],
            render_foreign_source_definition(FS),
        )
        self.assertEqual(
            client.post_requisition.call_args.args[0],
            render_requisition(FS, [self.profile]),
        )
        # rescanExisting comes from import_mode config (default "false").
        self.assertEqual(
            client.import_requisition.call_args.kwargs["rescan_existing"], "false"
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_render_error_marks_failed(self, mock_from_config, _lock):
        # Drop the management IP so the renderer raises RenderError.
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(management_ip=None)
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS)
        mock_from_config.assert_not_called()  # never reached the port

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_opennms_error_marks_failed(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.post_requisition.side_effect = OpenNMSHTTPError("boom", status_code=500)
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_202_is_accepted_not_provisioned(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        with self.assertLogs(
            "netbox.jobs.SyncForeignSourceJob", level="INFO"
        ) as captured:
            self._runner().run(foreign_source=FS)
        output = "\n".join(captured.output)
        self.assertIn("succeeded-accepted", output)
        self.assertIn("not verified", output)
        self.assertNotIn("provisioned", output)  # AD-12: never claim provisioned

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_serializes_per_foreign_source(self, mock_from_config, mock_lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(foreign_source=FS)
        mock_lock.assert_called_once_with(f"netbox_opennms:fs:{FS}")

    def test_enqueue_sync_coalesces_pending(self):
        job1 = SyncForeignSourceJob.enqueue_sync(FS)
        job2 = SyncForeignSourceJob.enqueue_sync(FS)
        self.assertEqual(job1.pk, job2.pk)
        self.assertEqual(Job.objects.filter(name=f"OpenNMS sync: {FS}").count(), 1)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_empty_profiles_skips_import(self, mock_from_config, _lock):
        # If the trigger was disabled/deleted in the enqueue→run window, the FS
        # has no enabled profiles. A Sync must NOT push an empty requisition
        # (which would mass-delete the FS) — it skips the OpenNMS push entirely.
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)
        self._runner().run(foreign_source=FS)
        mock_from_config.assert_not_called()

    def test_enabled_profiles_for_skips_non_device_vm_target(self):
        # A profile pointing at a non-Device/VM (reachable via ORM since
        # limit_choices_to is form-only) must be skipped, not crash every sync.
        site = Site.objects.get(slug="raleigh")
        MonitoringProfile.objects.create(
            assigned_object=site,
            management_ip=IPAddress.objects.create(address="10.9.9.9/24"),
        )
        result = enabled_profiles_for(FS)  # must not raise
        self.assertEqual([p.pk for p in result], [self.profile.pk])

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_validation_error_marks_failed(self, mock_from_config, _lock):
        # No management IP → pre-flight validation fails the job before any push.
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(management_ip=None)
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS)
        mock_from_config.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    @mock.patch("netbox_opennms.jobs.get_plugin_config")
    def test_invalid_import_mode_marks_failed(self, mock_cfg, mock_from_config, _lock):
        mock_cfg.side_effect = lambda _plugin, key: (
            "bogus" if key == "import_mode" else ""
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS)
        mock_from_config.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_remove_pushes_empty_requisition(self, mock_from_config, _lock):
        # allow_empty + no enabled profiles → push a node-less requisition that
        # deletes the FS's nodes (the intentional Remove path, Story 3.1).
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)

        self._runner().run(foreign_source=FS, allow_empty=True)

        client.post_requisition.assert_called_once()
        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertNotIn(b"<node", requisition_xml)  # node-less
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_remove_renders_remaining_when_not_last(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        role = DeviceRole.objects.get(slug="router")
        dt = DeviceType.objects.get(slug="m1")
        site = Site.objects.get(slug="raleigh")
        other = Device.objects.create(
            name="rtr-9", device_type=dt, role=role, site=site
        )
        oface = Interface.objects.create(device=other, name="eth0", type="virtual")
        oip = IPAddress.objects.create(address="10.0.0.9/24", assigned_object=oface)
        MonitoringProfile.objects.create(assigned_object=other, management_ip=oip)
        # remove self.profile; the other profile remains in the FS
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)

        self._runner().run(foreign_source=FS, allow_empty=True)

        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertEqual(requisition_xml.count(b"<node"), 1)  # only the remaining

    def test_remove_then_restore_is_idempotent(self):
        # AC4: disable (remove) then re-enable (restore) yields the IDENTICAL
        # requisition — same pk-derived foreign-id (AD-8), so re-syncing the
        # restored intent produces zero duplicate nodes.
        before = render_requisition(FS, enabled_profiles_for(FS))
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)
        self.assertEqual(enabled_profiles_for(FS), [])  # dropped from the render
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=True)
        after = render_requisition(FS, enabled_profiles_for(FS))
        self.assertEqual(before, after)

    def test_enabled_profiles_for_filters_by_fs_and_enabled(self):
        role = DeviceRole.objects.get(slug="router")
        dt = DeviceType.objects.get(slug="m1")
        raleigh = Site.objects.get(slug="raleigh")

        # Different Foreign Source (different site) — excluded.
        other_site = Site.objects.create(name="Durham", slug="durham")
        other = Device.objects.create(
            name="rtr-2", device_type=dt, role=role, site=other_site
        )
        MonitoringProfile.objects.create(
            assigned_object=other,
            management_ip=IPAddress.objects.create(address="10.0.1.1/24"),
        )
        # Same Foreign Source but disabled — excluded.
        disabled = Device.objects.create(
            name="rtr-3", device_type=dt, role=role, site=raleigh
        )
        MonitoringProfile.objects.create(
            assigned_object=disabled,
            management_ip=IPAddress.objects.create(address="10.0.0.9/24"),
            enabled=False,
        )

        result = enabled_profiles_for(FS)
        self.assertEqual([p.pk for p in result], [self.profile.pk])
