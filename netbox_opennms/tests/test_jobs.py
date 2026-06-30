# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
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

from netbox_opennms.client import OpenNMSError, OpenNMSHTTPError
from netbox_opennms.jobs import (
    ReconcileOrphansJob,
    SyncForeignSourceJob,
    enabled_foreign_sources,
    unknown_locations,
)
from netbox_opennms.membership import resolve
from netbox_opennms.models import (
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringProfile,
)
from netbox_opennms.translation import (
    render_foreign_source_definition,
    render_requisition,
)

FS = "netbox.raleigh.router"


class SyncForeignSourceJobTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Raleigh", slug="raleigh")
        cls.role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.profile = MonitoringProfile.objects.create(name="Network device")
        MonitoringDetector.objects.create(
            profile=cls.profile,
            name="ICMP",
            rule_class="org.opennms.netmgt.provision.detector.icmp.IcmpDetector",
        )
        cls.assignment = MonitoringAssignment.objects.create(
            profile=cls.profile, site=cls.site, role=cls.role
        )
        cls.device = cls._make_device("rtr-1", "10.0.0.1/24")

    @classmethod
    def _make_device(cls, name, ip, primary=True, role=None, site=None):
        device = Device.objects.create(
            name=name, device_type=cls.dt, role=role or cls.role, site=site or cls.site
        )
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        address = IPAddress.objects.create(address=ip, assigned_object=iface)
        if primary:
            device.primary_ip4 = address
            device.save()
        return device

    def _runner(self):
        return SyncForeignSourceJob(job=mock.Mock())

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_posts_fs_then_requisition_then_import(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)

        self._runner().run(foreign_source=FS)

        call_names = [c[0] for c in client.mock_calls]
        self.assertEqual(
            call_names[:3],
            ["post_foreign_source", "post_requisition", "import_requisition"],
        )
        self.assertEqual(
            client.post_foreign_source.call_args.args[0],
            render_foreign_source_definition(FS, self.profile),
        )
        resolution = resolve(FS)
        self.assertEqual(
            client.post_requisition.call_args.args[0],
            render_requisition(FS, resolution.nodes),
        )
        self.assertEqual(
            client.import_requisition.call_args.kwargs["rescan_existing"], "false"
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_render_error_marks_failed(self, mock_from_config, _lock):
        # A detector with no class makes the FS-definition render raise.
        MonitoringDetector.objects.create(
            profile=self.profile, name="bad", rule_class=""
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS)
        mock_from_config.assert_not_called()

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
        self.assertNotIn("provisioned", output)

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
    def test_ungoverned_foreign_source_skips_import(self, mock_from_config, _lock):
        # No assignment governs this FS → a Sync must not push anything.
        self._runner().run(foreign_source="netbox.durham.router")
        mock_from_config.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_no_monitorable_members_skips_import(self, mock_from_config, _lock):
        # Governed, but the only member has no management IP → nothing resolves,
        # and a Sync must not push an empty (mass-delete) requisition.
        Device.objects.filter(pk=self.device.pk).delete()
        self._make_device("rtr-x", "10.0.0.9/24", primary=False)
        self._runner().run(foreign_source=FS)
        mock_from_config.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_remove_pushes_empty_requisition(self, mock_from_config, _lock):
        # allow_empty + governed → push a node-less requisition (the Remove path).
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        Device.objects.filter(pk=self.device.pk).delete()
        self._runner().run(foreign_source=FS, allow_empty=True)
        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertNotIn(b"<node", requisition_xml)
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_remove_ungoverned_skips_definition(self, mock_from_config, _lock):
        # A bare Remove of an ungoverned FS has no profile, so no FS-definition is
        # pushed — only the empty requisition + import that clears the nodes.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(foreign_source="netbox.durham.router", allow_empty=True)
        client.post_foreign_source.assert_not_called()
        client.post_requisition.assert_called_once()
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_validation_error_marks_failed(self, mock_from_config, _lock):
        # An invalid location on the assignment (ORM-set, bypassing clean) fails
        # the job before any push (it would 400 on import).
        MonitoringAssignment.objects.filter(pk=self.assignment.pk).update(
            location="bad location"
        )
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

    def test_unknown_locations_helper(self):
        fake = mock.Mock()
        fake.list_locations.return_value = {"Default", "edge-2"}
        self.assertEqual(unknown_locations(fake, {"edge-1", "edge-2", ""}), ["edge-1"])

    def test_unknown_locations_skips_when_none(self):
        fake = mock.Mock()
        self.assertEqual(unknown_locations(fake, {""}), [])
        fake.list_locations.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_unknown_location_logs_warning(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.return_value = {"Default"}
        MonitoringAssignment.objects.filter(pk=self.assignment.pk).update(
            location="edge-1"
        )
        with self.assertLogs(
            "netbox.jobs.SyncForeignSourceJob", level="WARNING"
        ) as captured:
            self._runner().run(foreign_source=FS)
        self.assertIn("edge-1", "\n".join(captured.output))

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_location_check_failure_does_not_fail_sync(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.side_effect = OpenNMSHTTPError("boom", status_code=500)
        MonitoringAssignment.objects.filter(pk=self.assignment.pk).update(
            location="edge-1"
        )
        self._runner().run(foreign_source=FS)
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_ungoverned_remove_purges_shell(self, mock_from_config, _lock):
        # A Remove of an UNGOVERNED FS (reconciler/manual purge) clears the nodes
        # AND deletes the requisition + foreign-source shell so it can't recur.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(foreign_source="netbox.durham.router", allow_empty=True)
        client.delete_requisition.assert_called_once_with("netbox.durham.router")
        client.delete_foreign_source.assert_called_once_with("netbox.durham.router")

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_governed_remove_keeps_shell(self, mock_from_config, _lock):
        # A Remove of a still-GOVERNED FS (assignment exists, members emptied)
        # clears the nodes but keeps the shell — the scope is still intended.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        Device.objects.filter(pk=self.device.pk).delete()
        self._runner().run(foreign_source=FS, allow_empty=True)
        client.delete_requisition.assert_not_called()
        client.delete_foreign_source.assert_not_called()

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_reconcile_enqueues_remove_for_orphans(self, mock_from_config):
        client = mock_from_config.return_value.__enter__.return_value
        client.list_requisition_names.return_value = {
            FS,  # governed (assignment + member) → kept
            "netbox.durham.router",  # ours, ungoverned → orphan
            "external.thing",  # not our namespace → ignored
        }
        with mock.patch.object(SyncForeignSourceJob, "enqueue_sync") as enqueue:
            ReconcileOrphansJob(job=mock.Mock()).run()
        enqueue.assert_called_once_with("netbox.durham.router", allow_empty=True)

    @mock.patch("netbox_opennms.jobs.get_plugin_config")
    def test_reconcile_disabled_skips(self, mock_cfg):
        mock_cfg.return_value = "false"
        with mock.patch.object(SyncForeignSourceJob, "enqueue_sync") as enqueue:
            ReconcileOrphansJob(job=mock.Mock()).run()
        enqueue.assert_not_called()

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_reconcile_swallows_opennms_error(self, mock_from_config):
        mock_from_config.side_effect = OpenNMSError("down")
        ReconcileOrphansJob(job=mock.Mock()).run()  # must not raise

    def test_reconcile_registered_as_recurring_system_job(self):
        from netbox.registry import registry

        from netbox_opennms.jobs import RECONCILE_INTERVAL_MINUTES

        self.assertIn(ReconcileOrphansJob, registry["system_jobs"])
        self.assertEqual(
            registry["system_jobs"][ReconcileOrphansJob]["interval"],
            RECONCILE_INTERVAL_MINUTES,
        )

    def test_enabled_foreign_sources(self):
        # Distinct governed FSs with members; a different site is its own FS.
        durham = Site.objects.create(name="Durham", slug="durham")
        MonitoringAssignment.objects.create(
            profile=self.profile, site=durham, role=self.role
        )
        self._make_device("rtr-d", "10.0.1.1/24", site=durham)
        self.assertEqual(
            enabled_foreign_sources(),
            ["netbox.durham.router", "netbox.raleigh.router"],
        )
