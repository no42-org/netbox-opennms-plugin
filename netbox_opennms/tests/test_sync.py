# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the Sync UI actions (mocked job enqueue, no network)."""

from unittest import mock

from core.models import ObjectType
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from ipam.models import IPAddress
from users.models import ObjectPermission
from virtualization.models import VirtualMachine

from netbox_opennms.jobs import sync_status_for
from netbox_opennms.models import MonitoringDetector, MonitoringOverride, Requisition

User = get_user_model()
FS = "netbox.raleigh.router"


class SyncViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.device = Device.objects.create(
            name="rtr-1", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(
            device=cls.device, name="eth0", type="virtual"
        )
        ip = IPAddress.objects.create(address="10.0.0.1/24", assigned_object=iface)
        cls.device.primary_ip4 = ip
        cls.device.save()
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

    # --- node-level Remove (#133) -------------------------------------------

    def _remove(self, target, user=None, object_type=None):
        self.client.force_login(user or self.superuser)
        return self.client.post(
            reverse("plugins:netbox_opennms:object_remove"),
            {
                "object_type": object_type or target._meta.label_lower,
                "object_id": target.pk,
                "return_url": target.get_absolute_url(),
            },
            follow=True,
        )

    def _override(self, target):
        return MonitoringOverride.objects.filter(
            assigned_object_type=ContentType.objects.get_for_model(target),
            assigned_object_id=target.pk,
        ).first()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_excludes_and_enqueues(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=21)
        response = self._remove(self.device)
        self.assertTrue(self._override(self.device).exclude)
        mock_enqueue.assert_called_once_with(
            FS, user=self.superuser, allow_empty=True
        )
        self.assertContains(response, "Remove submitted")
        self.assertEqual(response.redirect_chain[-1][0], self.device.get_absolute_url())

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_keeps_existing_override(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=22)
        MonitoringOverride.objects.create(
            assigned_object=self.device,
            management_ip=self.device.primary_ip4,
            location="Durham",
        )
        self._remove(self.device)
        override = self._override(self.device)
        self.assertTrue(override.exclude)
        self.assertEqual(override.management_ip, self.device.primary_ip4)
        self.assertEqual(override.location, "Durham")
        self.assertEqual(MonitoringOverride.objects.count(), 1)

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_conflicted_object_removes_from_every_match(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=23)
        Requisition.objects.create(name="overlap", filter_params={"site": ["raleigh"]})
        self._remove(self.device)
        self.assertTrue(self._override(self.device).exclude)
        enqueued = sorted(c.args[0] for c in mock_enqueue.call_args_list)
        self.assertEqual(enqueued, [FS, "overlap"])
        self.assertEqual(sync_status_for(self.device)["conflicts"], [])

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_virtual_machine(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=24)
        vm = VirtualMachine.objects.create(
            name="vm-1",
            site=Site.objects.get(slug="raleigh"),
            role=DeviceRole.objects.get(slug="router"),
        )
        self._remove(vm)
        self.assertTrue(self._override(vm).exclude)
        # rtr-1 is still a member, so the VM leaves through a plain Sync.
        mock_enqueue.assert_called_once_with(
            FS, user=self.superuser, allow_empty=False
        )

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_requires_override_permission(self, mock_enqueue):
        user = User.objects.create_user(username="syncer", password="pw")
        perm = ObjectPermission.objects.create(name="sync only", actions=["change"])
        perm.object_types.set([ObjectType.objects.get_for_model(Requisition)])
        perm.users.add(user)
        self.client.force_login(user)
        response = self.client.post(
            reverse("plugins:netbox_opennms:object_remove"),
            {"object_type": "dcim.device", "object_id": self.device.pk},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._override(self.device))
        mock_enqueue.assert_not_called()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_one_of_many_is_a_plain_sync(self, mock_enqueue):
        # Review: a Remove job skips the mass-delete guard and re-resolves at run
        # time, so a node that leaves members behind gets a plain Sync.
        mock_enqueue.return_value = mock.Mock(pk=25)
        Device.objects.create(
            name="rtr-2",
            device_type=self.device.device_type,
            role=self.device.role,
            site=self.device.site,
        )
        response = self._remove(self.device)
        self.assertTrue(self._override(self.device).exclude)
        mock_enqueue.assert_called_once_with(
            FS, user=self.superuser, allow_empty=False
        )
        self.assertContains(response, "Sync submitted")

    def _constrained_user(self, **constraints):
        # Every permission the view needs; `constraints` narrows one model.
        user = User.objects.create_user(username="scoped", password="pw")
        for model, actions in (
            (Device, ["view"]),
            (VirtualMachine, ["view"]),
            (Requisition, ["change"]),
            (MonitoringOverride, ["add", "change"]),
        ):
            perm = ObjectPermission.objects.create(
                name=f"scoped {model.__name__}",
                actions=actions,
                constraints=constraints.get(model.__name__),
            )
            perm.object_types.set([ObjectType.objects.get_for_model(model)])
            perm.users.add(user)
        return user

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_honours_device_constraint(self, mock_enqueue):
        user = self._constrained_user(Device={"name": "other"})
        response = self._remove(self.device, user=user)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self._override(self.device))
        mock_enqueue.assert_not_called()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_honours_override_constraint(self, mock_enqueue):
        user = self._constrained_user(MonitoringOverride={"location": "Durham"})
        response = self._remove(self.device, user=user)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self._override(self.device))
        mock_enqueue.assert_not_called()

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_skips_unpermitted_requisition(self, mock_enqueue):
        user = self._constrained_user(Requisition={"name": "other"})
        response = self._remove(self.device, user=user)
        self.assertTrue(self._override(self.device).exclude)
        mock_enqueue.assert_not_called()
        self.assertContains(response, f"Not permitted to sync {FS}")

    @mock.patch("netbox_opennms.views.SyncForeignSourceJob.enqueue_sync")
    def test_remove_object_rejects_unsupported_type(self, mock_enqueue):
        site = Site.objects.get(slug="raleigh")
        response = self._remove(site, object_type="dcim.site")
        self.assertContains(
            response, "Remove supports only Devices and Virtual Machines"
        )
        self.assertFalse(MonitoringOverride.objects.exists())
        mock_enqueue.assert_not_called()

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

    def test_preview_requires_view_permission(self):
        self.client.force_login(self.plain)
        url = reverse("plugins:netbox_opennms:sync_preview")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_menu_hides_permission_gated_pages(self):
        # The nav menu renders on every page; home is a cheap one to fetch.
        links = [
            reverse("plugins:netbox_opennms:sync_preview"),
            reverse("plugins:netbox_opennms:connection_test"),
        ]
        self.client.force_login(self.plain)
        response = self.client.get(reverse("home"))
        for link in links:
            self.assertNotContains(response, f'href="{link}"')
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("home"))
        for link in links:
            self.assertContains(response, f'href="{link}"')

    def test_sync_requires_permission(self):
        self.client.force_login(self.plain)
        url = reverse("plugins:netbox_opennms:sync_all")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
