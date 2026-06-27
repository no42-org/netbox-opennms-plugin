# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Sync UI action (mocked job enqueue, no network)."""

from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
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
        ip = IPAddress.objects.create(address="10.0.0.1/24")
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
