# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the Sync UI action (mocked job enqueue, no network)."""

from unittest import mock

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
from ipam.models import IPAddress

from netbox_opennms.models import MonitoringProfile

User = get_user_model()


class MonitoringProfileSyncViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        device = Device.objects.create(
            name="rtr-1", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        ip = IPAddress.objects.create(address="10.0.0.1/24", assigned_object=iface)
        cls.profile = MonitoringProfile.objects.create(
            assigned_object=device, management_ip=ip
        )
        cls.superuser = User.objects.create_superuser(username="super", password="pw")
        cls.plain = User.objects.create_user(username="plain", password="pw")

    def _url(self):
        return reverse(
            "plugins:netbox_opennms:monitoringprofile_sync", args=[self.profile.pk]
        )

    def _remove_url(self):
        return reverse(
            "plugins:netbox_opennms:monitoringprofile_remove", args=[self.profile.pk]
        )

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_disables_and_enqueues_allow_empty(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=11)
        self.client.force_login(self.superuser)
        response = self.client.post(self._remove_url(), follow=True)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.enabled)
        self.assertContains(response, "Remove submitted")
        mock_enqueue.assert_called_once()
        self.assertTrue(mock_enqueue.call_args.kwargs.get("allow_empty"))

    def test_remove_denied_without_permission(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.post(self._remove_url()).status_code, 403)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_non_device_vm_fails_cleanly(self, mock_enqueue):
        site = Site.objects.get(slug="raleigh")
        bad = MonitoringProfile.objects.create(
            assigned_object=site,
            management_ip=IPAddress.objects.create(address="10.9.9.8/24"),
        )
        url = reverse("plugins:netbox_opennms:monitoringprofile_remove", args=[bad.pk])
        self.client.force_login(self.superuser)
        response = self.client.post(url, follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "not a Device or VirtualMachine")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_enqueues_and_messages(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=42)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        self.assertContains(response, "Sync submitted for")
        mock_enqueue.assert_called_once()

    def test_sync_denied_without_permission(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.post(self._url()).status_code, 403)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_disabled_profile_is_not_synced(self, mock_enqueue):
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(enabled=False)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "disabled")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_non_device_vm_target_is_not_synced(self, mock_enqueue):
        # A profile pointing at a non-Device/VM must fail cleanly, not 500.
        site = Site.objects.get(slug="raleigh")
        bad = MonitoringProfile.objects.create(
            assigned_object=site,
            management_ip=IPAddress.objects.create(address="10.9.9.9/24"),
        )
        url = reverse("plugins:netbox_opennms:monitoringprofile_sync", args=[bad.pk])
        self.client.force_login(self.superuser)
        response = self.client.post(url, follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "not a Device or VirtualMachine")

    @mock.patch("netbox_opennms.views.any_workers_for_queue", return_value=False)
    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_warns_when_no_worker(self, mock_enqueue, _no_worker):
        # AD-16: warn but do NOT block — the job is still enqueued.
        mock_enqueue.return_value = mock.Mock(pk=7)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_called_once()
        self.assertContains(response, "No background worker is running")

    @mock.patch("netbox_opennms.views.any_workers_for_queue", return_value=True)
    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_no_warning_when_worker_present(self, mock_enqueue, _worker):
        mock_enqueue.return_value = mock.Mock(pk=7)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        self.assertContains(response, "Sync submitted for")
        self.assertNotContains(response, "No background worker is running")

    @mock.patch("netbox_opennms.views.any_workers_for_queue", return_value=False)
    def test_detail_shows_worker_warning(self, _no_worker):
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:monitoringprofile", args=[self.profile.pk]
        )
        self.assertContains(self.client.get(url), "No background worker is running")

    @mock.patch("netbox_opennms.views.any_workers_for_queue", return_value=True)
    def test_detail_no_warning_when_worker_present(self, _worker):
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:monitoringprofile", args=[self.profile.pk]
        )
        self.assertNotContains(self.client.get(url), "No background worker is running")

    @mock.patch("netbox_opennms.views.any_workers_for_queue", return_value=True)
    def test_detail_conveys_group_semantics(self, _worker):
        # AC4 (Story 3.3): the detail page tells the operator a per-device Sync
        # republishes the whole Foreign Source group.
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:monitoringprofile", args=[self.profile.pk]
        )
        self.assertContains(
            self.client.get(url), "republishes the entire Foreign Source"
        )

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_blocked_on_validation_error(self, mock_enqueue):
        # No resolvable management IP → validation error → not enqueued (FR-8).
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(management_ip=None)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "no resolvable management IP")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    @mock.patch("netbox_opennms.views.validate_foreign_source")
    def test_sync_warns_but_proceeds(self, mock_validate, mock_enqueue):
        from netbox_opennms.validation import ValidationResult

        mock_validate.return_value = ValidationResult(errors=[], warnings=["heads up"])
        mock_enqueue.return_value = mock.Mock(pk=5)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_called_once()
        self.assertContains(response, "heads up")

    @mock.patch(
        "netbox_opennms.views.any_workers_for_queue",
        side_effect=Exception("broker down"),
    )
    def test_detail_warns_and_renders_when_probe_errors(self, _boom):
        # AD-16: a failed probe must not break the page; the new policy warns on
        # uncertainty rather than hiding the warning.
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:monitoringprofile", args=[self.profile.pk]
        )
        self.assertContains(self.client.get(url), "No background worker is running")

    @mock.patch(
        "netbox_opennms.views.any_workers_for_queue",
        side_effect=Exception("broker down"),
    )
    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_still_enqueues_when_probe_errors(self, mock_enqueue, _boom):
        # AD-16: never block — a probe error doesn't stop the enqueue.
        mock_enqueue.return_value = mock.Mock(pk=9)
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_called_once()
        self.assertEqual(response.status_code, 200)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    @mock.patch("netbox_opennms.views.OpenNMSClient.from_config")
    def test_sync_warns_unknown_location(self, mock_from_config, mock_enqueue):
        # FR-5: a chosen location OpenNMS doesn't know → advisory warning, but the
        # sync still proceeds (AD-16).
        mock_enqueue.return_value = mock.Mock(pk=3)
        client = mock_from_config.return_value.__enter__.return_value
        client.list_locations.return_value = {"Default"}
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(location="edge-1")
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_called_once()
        self.assertContains(response, "no Minion will poll it")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    @mock.patch(
        "netbox_opennms.views.OpenNMSClient.from_config",
        side_effect=Exception("OpenNMS down"),
    )
    def test_sync_location_check_degrades(self, mock_from_config, mock_enqueue):
        # AD-16: if the location probe can't run, degrade silently — still enqueue.
        mock_enqueue.return_value = mock.Mock(pk=3)
        MonitoringProfile.objects.filter(pk=self.profile.pk).update(location="edge-1")
        self.client.force_login(self.superuser)
        response = self.client.post(self._url(), follow=True)
        mock_enqueue.assert_called_once()
        self.assertNotContains(response, "no Minion will poll it")


class MonitoringProfileBulkSyncViewTest(TestCase):
    """Sync-all + Sync-selected bulk actions (Story 3.3)."""

    @classmethod
    def setUpTestData(cls):
        raleigh = Site.objects.create(name="Raleigh", slug="raleigh")
        durham = Site.objects.create(name="Durham", slug="durham")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")

        def _profile(name, site, addr):
            dev = Device.objects.create(
                name=name, device_type=dt, role=role, site=site
            )
            iface = Interface.objects.create(device=dev, name="eth0", type="virtual")
            ip = IPAddress.objects.create(address=addr, assigned_object=iface)
            return MonitoringProfile.objects.create(
                assigned_object=dev, management_ip=ip
            )

        cls.p_ral1 = _profile("rtr-1", raleigh, "10.0.0.1/24")
        cls.p_ral2 = _profile("rtr-2", raleigh, "10.0.0.2/24")  # same FS as p_ral1
        cls.p_dur = _profile("rtr-3", durham, "10.0.1.3/24")  # different FS
        cls.superuser = User.objects.create_superuser(username="super", password="pw")
        cls.plain = User.objects.create_user(username="plain", password="pw")

    def _all_url(self):
        return reverse("plugins:netbox_opennms:monitoringprofile_sync_all")

    def _bulk_url(self):
        return reverse("plugins:netbox_opennms:monitoringprofile_bulk_sync")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_all_enqueues_one_per_foreign_source(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=1)
        self.client.force_login(self.superuser)
        response = self.client.post(self._all_url(), follow=True)
        self.assertEqual(mock_enqueue.call_count, 2)  # raleigh + durham, deduped
        self.assertEqual(
            {c.args[0] for c in mock_enqueue.call_args_list},
            {"netbox.raleigh.router", "netbox.durham.router"},
        )
        self.assertContains(response, "Submitted 2 Foreign Source sync")

    def test_sync_all_denied_without_permission(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.post(self._all_url()).status_code, 403)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_all_nothing_enabled(self, mock_enqueue):
        MonitoringProfile.objects.update(enabled=False)
        self.client.force_login(self.superuser)
        response = self.client.post(self._all_url(), follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "Nothing enabled to sync")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_dedups_selected_by_foreign_source(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=1)
        self.client.force_login(self.superuser)
        response = self.client.post(
            self._bulk_url(),
            {"pk": [self.p_ral1.pk, self.p_ral2.pk, self.p_dur.pk]},
            follow=True,
        )
        self.assertEqual(mock_enqueue.call_count, 2)  # 3 profiles → 2 distinct FSs
        self.assertEqual(
            {c.args[0] for c in mock_enqueue.call_args_list},
            {"netbox.raleigh.router", "netbox.durham.router"},
        )
        self.assertContains(
            response, "Submitted 2 Foreign Source sync(s) for 3 profile(s)"
        )

    def test_bulk_sync_denied_without_permission(self):
        self.client.force_login(self.plain)
        response = self.client.post(self._bulk_url(), {"pk": [self.p_ral1.pk]})
        self.assertEqual(response.status_code, 403)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_skips_non_device_vm(self, mock_enqueue):
        site = Site.objects.get(slug="raleigh")
        bad = MonitoringProfile.objects.create(
            assigned_object=site,
            management_ip=IPAddress.objects.create(address="10.9.9.9/24"),
        )
        self.client.force_login(self.superuser)
        response = self.client.post(self._bulk_url(), {"pk": [bad.pk]}, follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "No syncable, enabled Device/VM profiles")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_all_matching_query_syncs_every_enabled_fs(self, mock_enqueue):
        # NetBox's "select all N matching" posts _all (often with no pk) — act on
        # every enabled FS rather than silently syncing nothing.
        self.client.force_login(self.superuser)
        self.client.post(self._bulk_url(), {"_all": "on"}, follow=True)
        self.assertEqual(mock_enqueue.call_count, 2)  # raleigh + durham
        self.assertEqual(
            {c.args[0] for c in mock_enqueue.call_args_list},
            {"netbox.raleigh.router", "netbox.durham.router"},
        )

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_excludes_disabled(self, mock_enqueue):
        MonitoringProfile.objects.filter(pk=self.p_dur.pk).update(enabled=False)
        self.client.force_login(self.superuser)
        response = self.client.post(
            self._bulk_url(), {"pk": [self.p_dur.pk]}, follow=True
        )
        mock_enqueue.assert_not_called()  # disabled selection is not syncable
        self.assertContains(response, "No syncable, enabled Device/VM profiles")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_ignores_non_integer_pk(self, mock_enqueue):
        # A crafted non-numeric pk must not 500.
        self.client.force_login(self.superuser)
        response = self.client.post(
            self._bulk_url(), {"pk": ["abc"]}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        mock_enqueue.assert_not_called()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_honors_safe_return_url(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=1)
        self.client.force_login(self.superuser)
        target = reverse("plugins:netbox_opennms:monitoringprofile_list") + "?q=x"
        response = self.client.post(
            self._bulk_url(), {"pk": [self.p_ral1.pk], "return_url": target}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, target)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_bulk_sync_rejects_unsafe_return_url(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=1)
        self.client.force_login(self.superuser)
        response = self.client.post(
            self._bulk_url(),
            {"pk": [self.p_ral1.pk], "return_url": "https://evil.example/x"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("plugins:netbox_opennms:monitoringprofile_list"),
        )
