# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the Sync UI actions (mocked job enqueue, no network)."""

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

from netbox_opennms.models import (
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringProfile,
)

User = get_user_model()
FS = "netbox.raleigh.router"


class SyncViewTest(TestCase):
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
        device.primary_ip4 = ip
        device.save()
        cls.profile = MonitoringProfile.objects.create(name="Network device")
        MonitoringDetector.objects.create(
            profile=cls.profile, name="ICMP", rule_class="org.x.IcmpDetector"
        )
        cls.assignment = MonitoringAssignment.objects.create(
            profile=cls.profile, site=site, role=role
        )
        cls.superuser = User.objects.create_superuser(username="super", password="pw")
        cls.plain = User.objects.create_user(username="plain", password="pw")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_foreign_source_sync_submitted(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=11)
        self.client.force_login(self.superuser)
        url = reverse("plugins:netbox_opennms:foreign_source_sync")
        response = self.client.post(url, {"foreign_source": FS}, follow=True)
        self.assertContains(response, "Sync submitted")
        self.assertEqual(mock_enqueue.call_args.kwargs["allow_empty"], False)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_foreign_source_remove_submitted(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=12)
        self.client.force_login(self.superuser)
        url = reverse("plugins:netbox_opennms:foreign_source_sync")
        response = self.client.post(
            url, {"foreign_source": FS, "remove": "1"}, follow=True
        )
        self.assertContains(response, "Remove submitted")
        self.assertEqual(mock_enqueue.call_args.kwargs["allow_empty"], True)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_all_enqueues_governed_foreign_sources(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=13)
        self.client.force_login(self.superuser)
        url = reverse("plugins:netbox_opennms:sync_all")
        response = self.client.post(url, follow=True)
        self.assertContains(response, "Submitted 1 Foreign Source sync(s)")
        mock_enqueue.assert_called_once()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_assignment_sync_enqueues(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=14)
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:monitoringassignment_sync",
            args=[self.assignment.pk],
        )
        response = self.client.post(url, follow=True)
        self.assertContains(response, "Submitted 1 Foreign Source sync(s)")

    def test_preview_renders(self):
        self.client.force_login(self.superuser)
        url = reverse("plugins:netbox_opennms:sync_preview")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, FS)

    def test_sync_requires_permission(self):
        self.client.force_login(self.plain)
        url = reverse("plugins:netbox_opennms:sync_all")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
