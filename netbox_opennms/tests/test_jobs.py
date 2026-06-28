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

from netbox_opennms.client import OpenNMSHTTPError
from netbox_opennms.jobs import (
    SyncForeignSourceJob,
    enabled_foreign_sources,
    enabled_profiles_for,
    unknown_locations,
)
from netbox_opennms.models import MonitoringProfile
from netbox_opennms.translation import (
    render_foreign_source_definition,
    render_requisition,
)

FS = "netbox.raleigh.router"


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

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_move_renders_old_empty_and_new_with_node(
        self, mock_from_config, mock_lock
    ):
        # AD-10: role/site changed → derived FS differs from last_synced. The job
        # render-and-replaces the OLD FS (now empty) then the NEW FS (with node).
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        old_fs = "netbox.durham.router"
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source=old_fs
        )

        self._runner().run(foreign_source=FS)

        # Two imports, old FS first then new FS (AC2/AC3 ordering).
        imported = [c.args[0] for c in client.import_requisition.call_args_list]
        self.assertEqual(imported, [old_fs, FS])
        reqs = [c.args[0] for c in client.post_requisition.call_args_list]
        self.assertNotIn(b"<node", reqs[0])  # old FS emptied (moved node removed)
        self.assertIn(b"<node", reqs[1])  # new FS includes the node
        # last_synced advanced to the new FS only after the new import (AC3).
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_synced_foreign_source, FS)
        # Both FS locks acquired in sorted order (AD-6, deadlock-free).
        locked = [c.args[0] for c in mock_lock.call_args_list]
        self.assertEqual(locked, sorted(locked))
        self.assertEqual(
            set(locked),
            {f"netbox_opennms:fs:{old_fs}", f"netbox_opennms:fs:{FS}"},
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_move_old_fs_keeps_sibling(self, mock_from_config, _lock):
        # A non-last move: the old FS still has another device, so its leg
        # re-renders that sibling only (moved node absent).
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        old_fs = "netbox.durham.router"
        role = DeviceRole.objects.get(slug="router")
        dt = DeviceType.objects.get(slug="m1")
        durham = Site.objects.create(name="Durham", slug="durham")
        sibling = Device.objects.create(
            name="rtr-d", device_type=dt, role=role, site=durham
        )
        sface = Interface.objects.create(device=sibling, name="eth0", type="virtual")
        sip = IPAddress.objects.create(address="10.0.2.1/24", assigned_object=sface)
        MonitoringProfile.objects.create(
            assigned_object=sibling,
            management_ip=sip,
            last_synced_foreign_source=old_fs,
        )
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source=old_fs
        )

        self._runner().run(foreign_source=FS)

        reqs = [c.args[0] for c in client.post_requisition.call_args_list]
        self.assertEqual(reqs[0].count(b"<node"), 1)  # old FS keeps the sibling
        self.assertEqual(reqs[1].count(b"<node"), 1)  # new FS has the moved node

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_move_converges_two_old_foreign_sources(self, mock_from_config, mock_lock):
        # Two nodes move into the SAME new FS from two DIFFERENT old FSs: both old
        # legs run and all three FS locks are acquired in sorted order (AD-6).
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        role = DeviceRole.objects.get(slug="router")
        dt = DeviceType.objects.get(slug="m1")
        raleigh = Site.objects.get(slug="raleigh")
        rtr2 = Device.objects.create(
            name="rtr-2", device_type=dt, role=role, site=raleigh
        )
        iface2 = Interface.objects.create(device=rtr2, name="eth0", type="virtual")
        ip2 = IPAddress.objects.create(address="10.0.0.2/24", assigned_object=iface2)
        MonitoringProfile.objects.create(
            assigned_object=rtr2,
            management_ip=ip2,
            last_synced_foreign_source="netbox.durham.router",
        )
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source="netbox.cary.router"
        )

        self._runner().run(foreign_source=FS)

        imported = [c.args[0] for c in client.import_requisition.call_args_list]
        # Both old FSs reconciled (empty) before the new FS, names sorted.
        self.assertEqual(
            imported, ["netbox.cary.router", "netbox.durham.router", FS]
        )
        locked = [c.args[0] for c in mock_lock.call_args_list]
        self.assertEqual(locked, sorted(locked))
        self.assertEqual(len(locked), 3)
        # Both moved nodes now recorded in the new FS.
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_synced_foreign_source, FS)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_old_fs_leg_records_last_synced_for_its_nodes(
        self, mock_from_config, _lock
    ):
        # Regression (review HIGH): a node re-imported by a side-effect old-FS leg
        # must get last_synced recorded too, or its own later move goes undetected.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        role = DeviceRole.objects.get(slug="router")
        dt = DeviceType.objects.get(slug="m1")
        durham = Site.objects.create(name="Durham", slug="durham")
        # A never-directly-synced node sitting in the OLD fs (last_synced="").
        zed = Device.objects.create(
            name="rtr-z", device_type=dt, role=role, site=durham
        )
        zface = Interface.objects.create(device=zed, name="eth0", type="virtual")
        zip_ = IPAddress.objects.create(address="10.0.3.1/24", assigned_object=zface)
        zed_profile = MonitoringProfile.objects.create(
            assigned_object=zed, management_ip=zip_
        )
        # self.profile moves OUT of durham → triggers the durham old-FS leg, which
        # re-renders {zed} and must stamp zed.last_synced = durham.
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source="netbox.durham.router"
        )

        self._runner().run(foreign_source=FS)

        zed_profile.refresh_from_db()
        self.assertEqual(
            zed_profile.last_synced_foreign_source, "netbox.durham.router"
        )

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_move_crash_before_new_import_keeps_old_last_synced(
        self, mock_from_config, _lock
    ):
        # AC3: if the NEW-FS import fails after the OLD-FS leg succeeded,
        # last_synced is NOT advanced, so the next Sync retries the whole move.
        client = mock_from_config.return_value.__enter__.return_value
        old_fs = "netbox.durham.router"
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source=old_fs
        )
        client.import_requisition.side_effect = [
            mock.Mock(status_code=202),  # old-FS leg accepted
            OpenNMSHTTPError("boom", status_code=500),  # new-FS leg fails
        ]

        with self.assertRaises(JobFailed):
            self._runner().run(foreign_source=FS)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_synced_foreign_source, old_fs)  # unchanged

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_first_sync_records_foreign_source(self, mock_from_config, _lock):
        # AC5: a never-synced profile (last_synced="") records the FS it lands in,
        # with no old-FS leg.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)

        self._runner().run(foreign_source=FS)

        self.assertEqual(client.import_requisition.call_count, 1)  # single leg
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_synced_foreign_source, FS)

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_no_move_when_foreign_source_unchanged(self, mock_from_config, mock_lock):
        # last_synced == derived FS → a plain re-render, no extra leg, one lock.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(
            last_synced_foreign_source=FS
        )

        self._runner().run(foreign_source=FS)

        self.assertEqual(client.import_requisition.call_count, 1)
        locked = [c.args[0] for c in mock_lock.call_args_list]
        self.assertEqual(locked, [f"netbox_opennms:fs:{FS}"])

    def test_unknown_locations_helper(self):
        fake = mock.Mock()
        fake.list_locations.return_value = {"Default", "edge-2"}
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(location="edge-1")
        profiles = list(MonitoringProfile.objects.filter(pk=self.profile.pk))
        self.assertEqual(unknown_locations(fake, profiles), ["edge-1"])

    def test_unknown_locations_skips_when_no_explicit_location(self):
        fake = mock.Mock()
        profiles = list(MonitoringProfile.objects.filter(pk=self.profile.pk))
        self.assertEqual(unknown_locations(fake, profiles), [])  # location=""
        fake.list_locations.assert_not_called()  # no port call when none explicit

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_unknown_location_logs_warning(self, mock_from_config, _lock):
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.return_value = {"Default"}
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(location="edge-1")
        with self.assertLogs(
            "netbox.jobs.SyncForeignSourceJob", level="WARNING"
        ) as captured:
            self._runner().run(foreign_source=FS)
        self.assertIn("edge-1", "\n".join(captured.output))

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_location_check_failure_does_not_fail_sync(self, mock_from_config, _lock):
        # Best-effort (AD-16): a list_locations failure must not fail a sync whose
        # import already succeeded.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.side_effect = OpenNMSHTTPError("boom", status_code=500)
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(location="edge-1")
        self._runner().run(foreign_source=FS)  # must not raise
        client.import_requisition.assert_called_once()

    @mock.patch("netbox_opennms.jobs.advisory_lock")
    @mock.patch("netbox_opennms.jobs.OpenNMSClient.from_config")
    def test_location_parse_failure_does_not_fail_sync(self, mock_from_config, _lock):
        # A non-OpenNMSError from the location probe (e.g. a malformed-JSON
        # ValueError) must also be swallowed — the import already succeeded.
        client = mock_from_config.return_value.__enter__.return_value
        client.import_requisition.return_value = mock.Mock(status_code=202)
        client.list_locations.side_effect = ValueError("unparseable")
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(location="edge-1")
        self._runner().run(foreign_source=FS)  # must not raise
        client.import_requisition.assert_called_once()

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

    def test_enabled_foreign_sources_distinct_sorted(self):
        # Distinct sorted FSs across enabled profiles; same-FS profiles collapse,
        # disabled and non-Device/VM profiles are excluded.
        role = DeviceRole.objects.get(slug="router")
        dt = DeviceType.objects.get(slug="m1")
        raleigh = Site.objects.get(slug="raleigh")
        durham = Site.objects.create(name="Durham", slug="durham")
        # Second enabled device in the SAME FS as self.profile (collapses to one).
        twin = Device.objects.create(
            name="rtr-2", device_type=dt, role=role, site=raleigh
        )
        MonitoringProfile.objects.create(
            assigned_object=twin,
            management_ip=IPAddress.objects.create(address="10.0.0.2/24"),
        )
        # A device in a different FS.
        far = Device.objects.create(
            name="rtr-3", device_type=dt, role=role, site=durham
        )
        MonitoringProfile.objects.create(
            assigned_object=far,
            management_ip=IPAddress.objects.create(address="10.0.1.3/24"),
        )
        # Disabled (excluded) and a non-Device/VM target (skipped).
        off = Device.objects.create(
            name="rtr-4", device_type=dt, role=role, site=durham
        )
        MonitoringProfile.objects.create(
            assigned_object=off,
            enabled=False,
            management_ip=IPAddress.objects.create(address="10.0.1.4/24"),
        )
        MonitoringProfile.objects.create(
            assigned_object=raleigh,  # a Site, not a Device/VM
            management_ip=IPAddress.objects.create(address="10.9.9.9/24"),
        )

        self.assertEqual(
            enabled_foreign_sources(),
            ["netbox.durham.router", "netbox.raleigh.router"],
        )

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
