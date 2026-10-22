# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the OpenNMS sync background job (mocked port, no network)."""

import uuid
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
    latest_sync_job,
    orphaned_foreign_sources,
    unknown_locations,
)
from netbox_opennms.membership import monitored_foreign_sources, resolve
from netbox_opennms.models import (
    DeployedForeignSource,
    MonitoringDetector,
    MonitoringOverride,
    Requisition,
)
from netbox_opennms.tests.servers import make_server
from netbox_opennms.translation import (
    render_foreign_source_definition,
    render_requisition,
)

FS = "netbox.raleigh.router"


class SyncForeignSourceJobTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.server = make_server()
        cls.site = Site.objects.create(name="Raleigh", slug="raleigh")
        cls.role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.requisition = Requisition.objects.create(
            server=cls.server,
            name=FS, filter_params={"site": ["raleigh"], "role": ["router"]}
        )
        MonitoringDetector.objects.create(
            requisition=cls.requisition,
            name="ICMP",
            rule_class="org.opennms.netmgt.provision.detector.icmp.IcmpDetector",
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
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_posts_fs_then_requisition_then_import(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)

        self._runner().run(foreign_source=FS, server_id=self.server.pk)

        call_names = [c[0] for c in client.mock_calls]
        self.assertEqual(
            call_names[:3],
            ["post_foreign_source", "post_requisition", "import_requisition"],
        )
        self.assertEqual(
            client.post_foreign_source.call_args.args[0],
            render_foreign_source_definition(FS, self.requisition),
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
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_render_error_marks_failed(self, mock_for_server, _lock):
        MonitoringDetector.objects.create(
            requisition=self.requisition, name="bad", rule_class=""
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_opennms_error_marks_failed(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.post_requisition.side_effect = OpenNMSHTTPError("boom", status_code=500)
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS, server_id=self.server.pk)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_202_is_accepted_not_provisioned(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        with self.assertLogs(
            "netbox.jobs.SyncForeignSourceJob", level="INFO"
        ) as captured:
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        output = "\n".join(captured.output)
        self.assertIn("succeeded-accepted", output)
        self.assertIn("not verified", output)
        self.assertNotIn("provisioned", output)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_serializes_per_foreign_source(self, mock_for_server, mock_lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_lock.assert_called_once_with(f"netbox_opennms:fs:{FS}")

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_conflict_freezes_sync(self, mock_for_server, _lock):
        # C1: an overlap blocks Sync of the involved Requisition — JobFailed with
        # the conflict error, and NOTHING is pushed to OpenNMS.
        Requisition.objects.create(
            server=self.server,
            name="overlap", filter_params={"site": ["raleigh"]}
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_reconcile_ignores_frozen_requisition(self, mock_for_server):
        # C5: a frozen (conflicted) Requisition counts as monitored — the drift
        # reconciler must never tear down the state the freeze protects.
        DeployedForeignSource.objects.create(server=self.server, name=FS)
        Requisition.objects.create(
            server=self.server,
            name="overlap", filter_params={"site": ["raleigh"]}
        )
        client = mock_for_server.return_value.__enter__.return_value
        client.list_requisition_names.return_value = {FS}
        with mock.patch.object(SyncForeignSourceJob, "enqueue_sync") as enqueue:
            ReconcileOrphansJob(job=mock.Mock()).run()
        enqueue.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_sync_records_ownership(self, mock_for_server, _lock):
        # A successful sync records the FS name so the reconciler owns it (review #4).
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(foreign_source=FS, server_id=self.server.pk)
        self.assertTrue(
            DeployedForeignSource.objects.filter(server=self.server, name=FS).exists()
        )

    def test_enqueue_sync_coalesces_pending(self):
        job1 = SyncForeignSourceJob.enqueue_sync(FS, self.server.pk)
        job2 = SyncForeignSourceJob.enqueue_sync(FS, self.server.pk)
        self.assertEqual(job1.pk, job2.pk)
        self.assertEqual(
            Job.objects.filter(name=f"OpenNMS sync: {FS} @{self.server.pk}").count(),
            1,
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_ungoverned_foreign_source_skips_import(self, mock_for_server, _lock):
        # No Requisition is named this → a Sync must not push anything.
        self._runner().run(
            foreign_source="netbox.durham.router", server_id=self.server.pk
        )
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_no_monitorable_members_skips_import(self, mock_for_server, _lock):
        # Genuinely empty (the sole member is EXCLUDED → monitored nowhere): a Sync
        # must not push an empty (mass-delete) requisition. A no-management-IP member
        # is NOT empty — it provisions as an interface-less node (RD-6/h).
        MonitoringOverride.objects.create(assigned_object=self.device, exclude=True)
        self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_pushes_empty_requisition(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        Device.objects.filter(pk=self.device.pk).delete()
        self._runner().run(
            foreign_source=FS, server_id=self.server.pk, allow_empty=True
        )
        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertNotIn(b"<node", requisition_xml)
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_of_excluded_last_member_pushes_empty(self, mock_for_server, _lock):
        # #133: the panel Remove excludes the object, then removes its Foreign
        # Source. The Requisition still exists but resolves to zero nodes.
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        MonitoringOverride.objects.create(assigned_object=self.device, exclude=True)
        self._runner().run(
            foreign_source=FS, server_id=self.server.pk, allow_empty=True
        )
        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertNotIn(b"<node", requisition_xml)
        client.import_requisition.assert_called_once()

    def test_unexclude_restores_same_node_identity(self):
        # #133: Remove is reversible. Clearing exclude renders the same node.
        before = [node.foreign_id for node in resolve(FS).nodes]
        override = MonitoringOverride.objects.create(
            assigned_object=self.device, exclude=True
        )
        self.assertEqual(resolve(FS).nodes, [])
        override.exclude = False
        override.save()
        self.assertEqual([node.foreign_id for node in resolve(FS).nodes], before)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_ungoverned_skips_definition(self, mock_for_server, _lock):
        # A bare Remove of a name with no Requisition has no definition to push —
        # only the empty requisition + import that clears the nodes.
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(
            foreign_source="netbox.durham.router",
            server_id=self.server.pk,
            allow_empty=True,
        )
        client.post_foreign_source.assert_not_called()
        client.post_requisition.assert_called_once()
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_validation_error_marks_failed(self, mock_for_server, _lock):
        # An invalid location on the Requisition (ORM-set, bypassing clean) fails
        # the job before any push (it would 400 on import).
        Requisition.objects.filter(pk=self.requisition.pk).update(
            location="bad location"
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    @mock.patch("netbox_opennms.jobs.get_plugin_config")
    def test_invalid_import_mode_marks_failed(self, mock_cfg, mock_for_server, _lock):
        mock_cfg.side_effect = lambda _plugin, key: (
            "bogus" if key == "import_mode" else ""
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_for_server.assert_not_called()

    def test_unknown_locations_helper(self):
        fake = mock.Mock()
        fake.list_locations.return_value = {"Default", "edge-2"}
        self.assertEqual(unknown_locations(fake, {"edge-1", "edge-2", ""}), ["edge-1"])

    def test_unknown_locations_skips_when_none(self):
        fake = mock.Mock()
        self.assertEqual(unknown_locations(fake, {""}), [])
        fake.list_locations.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_unknown_location_logs_warning(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.return_value = {"Default"}
        Requisition.objects.filter(pk=self.requisition.pk).update(location="edge-1")
        with self.assertLogs(
            "netbox.jobs.SyncForeignSourceJob", level="WARNING"
        ) as captured:
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        self.assertIn("edge-1", "\n".join(captured.output))

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_location_check_failure_does_not_fail_sync(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.side_effect = OpenNMSHTTPError("boom", status_code=500)
        Requisition.objects.filter(pk=self.requisition.pk).update(location="edge-1")
        self._runner().run(foreign_source=FS, server_id=self.server.pk)
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_ungoverned_remove_purges_shell(self, mock_for_server, _lock):
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(
            foreign_source="netbox.durham.router",
            server_id=self.server.pk,
            allow_empty=True,
        )
        client.delete_requisition.assert_called_once_with("netbox.durham.router")
        client.delete_foreign_source.assert_called_once_with("netbox.durham.router")

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_of_empty_requisition_tears_down_shell(
        self, mock_for_server, _lock
    ):
        # A Remove that resolves to ZERO nodes tears down the shell + the ownership
        # record, so the reconciler can't re-Remove it every interval (review #3).
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        DeployedForeignSource.objects.create(server=self.server, name=FS)
        Device.objects.filter(pk=self.device.pk).delete()
        self._runner().run(
            foreign_source=FS, server_id=self.server.pk, allow_empty=True
        )
        client.delete_requisition.assert_called_once_with(FS)
        client.delete_foreign_source.assert_called_once_with(FS)
        self.assertFalse(
            DeployedForeignSource.objects.filter(server=self.server, name=FS).exists()
        )

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_reconcile_enqueues_remove_for_orphans(self, mock_for_server):
        # Ownership is tracked by DeployedForeignSource, not a name prefix — so a
        # USER-named orphan (no netbox. prefix) is detected (review #4).
        DeployedForeignSource.objects.create(server=self.server, name=FS)
        DeployedForeignSource.objects.create(server=self.server, name="core-switches")
        client = mock_for_server.return_value.__enter__.return_value
        client.list_requisition_names.return_value = {
            FS,  # managed + monitored (requisition + member) → kept
            "core-switches",  # managed, no requisition → orphan
            "external.thing",  # not managed → ignored
        }
        with mock.patch.object(SyncForeignSourceJob, "enqueue_sync") as enqueue:
            ReconcileOrphansJob(job=mock.Mock()).run()
        enqueue.assert_called_once_with(
            "core-switches", server_id=self.server.pk, allow_empty=True
        )

    @mock.patch("netbox_opennms.jobs.get_plugin_config")
    def test_reconcile_disabled_skips(self, mock_cfg):
        mock_cfg.return_value = "false"
        with mock.patch.object(SyncForeignSourceJob, "enqueue_sync") as enqueue:
            ReconcileOrphansJob(job=mock.Mock()).run()
        enqueue.assert_not_called()

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_reconcile_swallows_opennms_error(self, mock_for_server):
        mock_for_server.side_effect = OpenNMSError("down")
        ReconcileOrphansJob(job=mock.Mock()).run()  # must not raise

    def test_reconcile_registered_as_recurring_system_job(self):
        from netbox.registry import registry

        from netbox_opennms.jobs import RECONCILE_INTERVAL_MINUTES

        self.assertIn(ReconcileOrphansJob, registry["system_jobs"])
        self.assertEqual(
            registry["system_jobs"][ReconcileOrphansJob]["interval"],
            RECONCILE_INTERVAL_MINUTES,
        )

    def test_monitored_foreign_sources_lists_syncable_requisitions(self):
        durham = Site.objects.create(name="Durham", slug="durham")
        Requisition.objects.create(
            server=self.server,
            name="netbox.durham.router",
            filter_params={"site": ["durham"], "role": ["router"]},
        )
        self._make_device("rtr-d", "10.0.1.1/24", site=durham)
        self.assertEqual(
            monitored_foreign_sources(),
            {
                (self.server.pk, "netbox.durham.router"),
                (self.server.pk, "netbox.raleigh.router"),
            },
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_with_members_pushes_full_requisition(
        self, mock_for_server, _lock
    ):
        # A Remove of a still-populated FS pushes the FULL requisition (the
        # membership is intact) and must NOT tear down the shell (review #6).
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        self._runner().run(
            foreign_source=FS, server_id=self.server.pk, allow_empty=True
        )
        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertIn(b"<node", requisition_xml)
        client.delete_requisition.assert_not_called()
        client.delete_foreign_source.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_allowed_for_rejected_filter(self, mock_for_server, _lock):
        # Review #3 (round 2): a rejected filter must NOT block a deliberate
        # Remove — that is the teardown escape hatch for a broken-filter FS.
        client = mock_for_server.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        Requisition.objects.filter(pk=self.requisition.pk).update(
            filter_params={"bogus_key": ["x"]}
        )
        self._runner().run(
            foreign_source=FS, server_id=self.server.pk, allow_empty=True
        )
        requisition_xml = client.post_requisition.call_args.args[0]
        self.assertNotIn(b"<node", requisition_xml)  # rejected → no members
        client.import_requisition.assert_called_once()
        client.delete_requisition.assert_called_once_with(FS)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_rejected_filter_blocks_sync(self, mock_for_server, _lock):
        # Review #8: an unknown-key filter fails the job loudly (JobFailed),
        # never a quiet skip with a green COMPLETED job.
        Requisition.objects.filter(pk=self.requisition.pk).update(
            filter_params={"bogus_key": ["x"]}
        )
        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS, server_id=self.server.pk)
        mock_for_server.assert_not_called()


class MultiServerJobTest(TestCase):
    """Each Foreign Source syncs to, and is reconciled on, its own server."""

    @classmethod
    def setUpTestData(cls):
        cls.a = make_server("a")
        cls.b = make_server("b", url="https://b.example.org/opennms")
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        for name, slug, ip in (
            ("rtr-1", "router", "10.0.0.1/24"),
            ("sw-1", "switch", "10.0.0.2/24"),
        ):
            role = DeviceRole.objects.create(name=slug.title(), slug=slug)
            device = Device.objects.create(
                name=name, device_type=dt, role=role, site=site
            )
            iface = Interface.objects.create(
                device=device, name="eth0", type="virtual"
            )
            device.primary_ip4 = IPAddress.objects.create(
                address=ip, assigned_object=iface
            )
            device.save()
        cls.r_a = Requisition.objects.create(
            server=cls.a, name="a", filter_params={"role": ["router"]}
        )
        cls.r_b = Requisition.objects.create(
            server=cls.b, name="b", filter_params={"role": ["switch"]}
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_sync_uses_requisition_server(self, mock_for_server, _lock):
        SyncForeignSourceJob(job=mock.Mock()).run(
            foreign_source="a", server_id=self.a.pk
        )
        mock_for_server.assert_called_once_with(self.a)
        self.assertTrue(
            DeployedForeignSource.objects.filter(server=self.a, name="a").exists()
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_default_location_comes_from_server(self, mock_for_server, _lock):
        self.a.default_location = "Lab"
        self.a.save()
        client = mock_for_server.return_value.__enter__.return_value
        SyncForeignSourceJob(job=mock.Mock()).run(
            foreign_source="a", server_id=self.a.pk
        )
        self.assertIn(b'location="Lab"', client.post_requisition.call_args[0][0])

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_missing_server_fails_job(self, mock_for_server):
        with self.assertRaises(JobFailed):
            SyncForeignSourceJob(job=mock.Mock()).run(
                foreign_source="a", server_id=self.b.pk + 1000
            )
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.SyncForeignSourceJob.enqueue")
    def test_enqueue_captures_server_id(self, mock_enqueue):
        SyncForeignSourceJob.enqueue_sync("a", self.a.pk)
        self.assertEqual(mock_enqueue.call_args.kwargs["server_id"], self.a.pk)

    def test_pending_sync_does_not_swallow_other_server(self):
        # After a move, a Sync to the new server must not coalesce into the
        # pending Sync to the old one.
        first = SyncForeignSourceJob.enqueue_sync("a", self.a.pk)
        second = SyncForeignSourceJob.enqueue_sync("a", self.b.pk)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Job.objects.count(), 2)
        # The same server still coalesces.
        self.assertEqual(SyncForeignSourceJob.enqueue_sync("a", self.a.pk), first)

    def test_job_name_carries_server(self):
        self.assertEqual(
            SyncForeignSourceJob.job_name("a", self.a.pk),
            f"OpenNMS sync: a @{self.a.pk}",
        )
        removal = SyncForeignSourceJob.job_name("x" * 300, self.a.pk, allow_empty=True)
        self.assertTrue(removal.endswith(" (remove)"))
        self.assertLessEqual(len(removal), 200)

    def test_orphans_are_per_server(self):
        DeployedForeignSource.objects.create(server=self.a, name="gone")
        DeployedForeignSource.objects.create(server=self.b, name="gone")
        client = mock.Mock(list_requisition_names=lambda: ["gone"])
        self.assertEqual(orphaned_foreign_sources(client, self.a), ["gone"])

    def test_orphans_ignore_other_servers_rows(self):
        DeployedForeignSource.objects.create(server=self.b, name="gone")
        client = mock.Mock(list_requisition_names=lambda: ["gone"])
        self.assertEqual(orphaned_foreign_sources(client, self.a), [])

    def test_moved_requisition_orphans_old_copy(self):
        DeployedForeignSource.objects.create(server=self.a, name="a")
        self.r_a.server = self.b
        self.r_a.save()
        client = mock.Mock(list_requisition_names=lambda: ["a"])
        self.assertEqual(orphaned_foreign_sources(client, self.a), ["a"])

    def test_move_between_rows_for_same_opennms_is_not_an_orphan(self):
        # C1: Foreign Source names are global on one OpenNMS. A row "a2" that
        # points at the same instance as "a" (other spelling of the URL) holds
        # the moved Requisition's live copy; "a" must not tear it down.
        a2 = make_server("a2", url="HTTPS://ONMS.EXAMPLE.ORG/opennms/")
        DeployedForeignSource.objects.create(server=self.a, name="a")
        self.r_a.server = a2
        self.r_a.save()
        client = mock.Mock(list_requisition_names=lambda: ["a"])
        self.assertEqual(orphaned_foreign_sources(client, self.a), [])
        # The stale ownership row is left in place; the guard keeps it inert.
        self.assertTrue(
            DeployedForeignSource.objects.filter(server=self.a, name="a").exists()
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_on_old_row_for_same_opennms_is_skipped(
        self, mock_for_server, _lock
    ):
        a2 = make_server("a2", url="https://onms.example.org/opennms/")
        self.r_a.server = a2
        self.r_a.save()
        SyncForeignSourceJob(job=mock.Mock()).run(
            foreign_source="a", server_id=self.a.pk, allow_empty=True
        )
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_job_without_server_id_uses_requisition_server(
        self, mock_for_server, _lock
    ):
        # M2: an RQ job queued before the upgrade carries no server_id.
        SyncForeignSourceJob(job=mock.Mock()).run(foreign_source="b")
        mock_for_server.assert_called_once_with(self.b)

    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_job_without_server_id_or_requisition_fails(self, mock_for_server):
        runner = SyncForeignSourceJob(job=mock.Mock())
        with self.assertLogs("netbox.jobs.SyncForeignSourceJob", level="ERROR"):
            with self.assertRaises(JobFailed):
                runner.run(foreign_source="nope", allow_empty=True)
        mock_for_server.assert_not_called()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_remove_on_old_server_tears_down_moved_copy(
        self, mock_for_server, _lock
    ):
        # The reconciler's Remove for a moved Requisition's old copy must push
        # an empty requisition to the old server, not re-push the live one.
        DeployedForeignSource.objects.create(server=self.a, name="a")
        DeployedForeignSource.objects.create(server=self.b, name="a")
        self.r_a.server = self.b
        self.r_a.save()
        client = mock_for_server.return_value.__enter__.return_value
        SyncForeignSourceJob(job=mock.Mock()).run(
            foreign_source="a", server_id=self.a.pk, allow_empty=True
        )
        mock_for_server.assert_called_once_with(self.a)
        self.assertNotIn(b"<node", client.post_requisition.call_args[0][0])
        client.delete_requisition.assert_called_once_with("a")
        self.assertFalse(
            DeployedForeignSource.objects.filter(server=self.a, name="a").exists()
        )
        self.assertTrue(
            DeployedForeignSource.objects.filter(server=self.b, name="a").exists()
        )

    @mock.patch("netbox_opennms.jobs.SyncForeignSourceJob.enqueue_sync")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_reconcile_continues_after_one_server_fails(
        self, mock_for_server, mock_enqueue
    ):
        healthy = mock.MagicMock()
        healthy.__enter__.return_value.list_requisition_names.return_value = []
        mock_for_server.side_effect = [OpenNMSError("down"), healthy]
        ReconcileOrphansJob(job=mock.Mock()).run()
        self.assertEqual(mock_for_server.call_count, 2)
        mock_for_server.assert_called_with(self.b)

    @mock.patch("netbox_opennms.jobs.SyncForeignSourceJob.enqueue_sync")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_reconcile_enqueues_orphan_with_its_server(
        self, mock_for_server, mock_enqueue
    ):
        DeployedForeignSource.objects.create(server=self.b, name="gone")
        client = mock_for_server.return_value.__enter__.return_value
        client.list_requisition_names.return_value = ["gone"]
        ReconcileOrphansJob(job=mock.Mock()).run()
        mock_enqueue.assert_called_once_with(
            "gone", server_id=self.b.pk, allow_empty=True
        )

    @mock.patch("netbox_opennms.jobs.SyncForeignSourceJob.enqueue_sync")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.for_server")
    def test_reconcile_resolves_requisitions_once(self, mock_for_server, _enqueue):
        client = mock_for_server.return_value.__enter__.return_value
        client.list_requisition_names.return_value = []
        with mock.patch(
            "netbox_opennms.membership.resolve_all", return_value=[]
        ) as resolve_all:
            ReconcileOrphansJob(job=mock.Mock()).run()
        self.assertEqual(mock_for_server.call_count, 2)
        resolve_all.assert_called_once_with()

    def test_latest_sync_job_finds_pre_upgrade_job(self):
        # Jobs enqueued before the upgrade are named without "@<server id>".
        legacy = Job.objects.create(name="OpenNMS sync: a", job_id=uuid.uuid4())
        self.assertEqual(latest_sync_job([("a", self.a.pk)]), legacy)
        removal = Job.objects.create(
            name="OpenNMS sync: a (remove)", job_id=uuid.uuid4()
        )
        self.assertEqual(latest_sync_job([("a", self.a.pk)]), removal)
        current = Job.objects.create(
            name=SyncForeignSourceJob.job_name("a", self.a.pk), job_id=uuid.uuid4()
        )
        self.assertEqual(latest_sync_job([("a", self.a.pk)]), current)
