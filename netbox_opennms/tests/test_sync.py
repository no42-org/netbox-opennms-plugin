# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
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
