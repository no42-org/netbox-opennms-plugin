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

from netbox_opennms.models import MonitoringDetector, Requisition

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
        cls.requisition = Requisition.objects.create(
            name=FS, filter_params={"site": ["raleigh"], "role": ["router"]}
        )
        MonitoringDetector.objects.create(
            requisition=cls.requisition, name="ICMP", rule_class="org.x.IcmpDetector"
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
    def test_sync_all_skips_frozen_requisitions(self, mock_enqueue):
        # Review #2: Sync-all must not enqueue a guaranteed-failed job for a
        # frozen requisition — it skips it with a warning.
        Requisition.objects.create(
            name="overlap", filter_params={"site": ["raleigh"]}
        )
        self.client.force_login(self.superuser)
        url = reverse("plugins:netbox_opennms:sync_all")
        response = self.client.post(url, follow=True)
        mock_enqueue.assert_not_called()
        self.assertContains(response, "frozen")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_all_blocks_invalid_location(self, mock_enqueue):
        # Round-2 review #1: Sync-all uses the canonical validation gate — a
        # nodes-bearing requisition with a bad location is skipped with a
        # warning, not enqueued into a guaranteed-failed job.
        Requisition.objects.filter(pk=self.requisition.pk).update(
            location="bad location"
        )
        self.client.force_login(self.superuser)
        response = self.client.post(
            reverse("plugins:netbox_opennms:sync_all"), follow=True
        )
        mock_enqueue.assert_not_called()
        self.assertContains(response, "Skipped 1 requisition")

    def test_duplicate_of_populated_requisition_warns_frozen(self):
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:requisition_duplicate",
            args=[self.requisition.pk],
        )
        response = self.client.post(url, follow=True)
        self.assertContains(response, "frozen")

    def test_duplicate_of_empty_requisition_does_not_warn_frozen(self):
        # Round-2 review #4: a zero-member source duplicates harmlessly — no
        # false freeze alarm.
        empty = Requisition.objects.create(
            name="empty", filter_params={"site": ["raleigh"], "role": ["unused"]}
        )
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:requisition_duplicate", args=[empty.pk]
        )
        response = self.client.post(url, follow=True)
        self.assertNotContains(response, "frozen")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_sync_all_enqueues_governed_foreign_sources(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=13)
        self.client.force_login(self.superuser)
        url = reverse("plugins:netbox_opennms:sync_all")
        response = self.client.post(url, follow=True)
        self.assertContains(response, "Submitted 1 Foreign Source sync(s)")
        mock_enqueue.assert_called_once()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_requisition_sync_enqueues(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=14)
        self.client.force_login(self.superuser)
        url = reverse(
            "plugins:netbox_opennms:requisition_sync", args=[self.requisition.pk]
        )
        response = self.client.post(url, follow=True)
        self.assertContains(response, "Sync submitted")

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
