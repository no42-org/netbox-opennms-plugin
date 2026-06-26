# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Model and form tests for MonitoringProfile."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import TestCase
from ipam.models import IPAddress
from virtualization.models import Cluster, ClusterType, VirtualMachine

from netbox_opennms.forms import MonitoringProfileForm
from netbox_opennms.models import MonitoringProfile


class MonitoringProfileTestData:
    @classmethod
    def _make_device(cls, name):
        return Device.objects.create(
            name=name,
            device_type=cls.device_type,
            role=cls.role,
            site=cls.site,
        )

    @classmethod
    def _assign_primary_ip(cls, device, address):
        interface = Interface.objects.create(
            device=device, name=f"eth-{address}", type="virtual"
        )
        ip = IPAddress.objects.create(address=address, assigned_object=interface)
        device.primary_ip4 = ip
        device.save()
        return ip

    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Site 1", slug="site-1")
        manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model 1", slug="model-1"
        )
        cls.role = DeviceRole.objects.create(name="Router", slug="router")
        cls.device = cls._make_device("device-1")

        cluster_type = ClusterType.objects.create(name="Type 1", slug="type-1")
        cls.cluster = Cluster.objects.create(name="Cluster 1", type=cluster_type)
        cls.vm = VirtualMachine.objects.create(name="vm-1", cluster=cls.cluster)


class MonitoringProfileModelTest(MonitoringProfileTestData, TestCase):
    def test_assign_to_device(self):
        profile = MonitoringProfile.objects.create(assigned_object=self.device)
        self.assertEqual(profile.assigned_object, self.device)
        self.assertTrue(profile.enabled)
        self.assertEqual(str(profile), str(self.device))

    def test_assign_to_vm(self):
        profile = MonitoringProfile.objects.create(assigned_object=self.vm)
        self.assertEqual(profile.assigned_object, self.vm)

    def test_one_profile_per_object(self):
        MonitoringProfile.objects.create(assigned_object=self.device)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MonitoringProfile.objects.create(assigned_object=self.device)

    def test_orphan_profile_cleaned_on_device_delete(self):
        device = self._make_device("device-orphan")
        profile = MonitoringProfile.objects.create(assigned_object=device)
        device.delete()
        self.assertFalse(MonitoringProfile.objects.filter(pk=profile.pk).exists())

    def test_str_when_object_deleted(self):
        device = self._make_device("device-str")
        profile = MonitoringProfile(assigned_object=device)
        profile.save()
        # Simulate a dangling reference (no cascade for raw GFK ids).
        MonitoringProfile.objects.filter(pk=profile.pk).update(
            assigned_object_id=999999
        )
        profile.refresh_from_db()
        self.assertEqual(str(profile), "Monitoring profile")

    def test_device_and_vm_do_not_collide(self):
        # Same PK across the two models must not be treated as the same object.
        d = self._make_device("device-pkcheck")
        vm = VirtualMachine.objects.create(name="vm-pkcheck", cluster=self.cluster)
        MonitoringProfile.objects.create(assigned_object=d)
        # No IntegrityError: distinct content types.
        MonitoringProfile.objects.create(assigned_object=vm)
        self.assertEqual(MonitoringProfile.objects.count(), 2)

    def test_disable_retains_profile(self):
        profile = MonitoringProfile.objects.create(assigned_object=self.device)
        profile.enabled = False
        profile.save()
        profile.refresh_from_db()
        self.assertFalse(profile.enabled)
        self.assertTrue(MonitoringProfile.objects.filter(pk=profile.pk).exists())


class MonitoringProfileFormTest(MonitoringProfileTestData, TestCase):
    def test_exactly_one_target_required(self):
        # Neither selected.
        form = MonitoringProfileForm(data={"enabled": True})
        self.assertFalse(form.is_valid())

        # Both selected.
        form = MonitoringProfileForm(
            data={
                "device": self.device.pk,
                "virtual_machine": self.vm.pk,
                "enabled": True,
            }
        )
        self.assertFalse(form.is_valid())

    def test_valid_single_target_saves(self):
        ip = IPAddress.objects.create(address="10.0.0.1/24")
        form = MonitoringProfileForm(
            data={"device": self.device.pk, "management_ip": ip.pk, "enabled": True}
        )
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()
        self.assertEqual(profile.assigned_object, self.device)
        self.assertEqual(profile.management_ip, ip)

    def test_management_ip_defaults_to_primary_ip(self):
        ip = self._assign_primary_ip(self.device, "10.0.0.10/24")
        form = MonitoringProfileForm(data={"device": self.device.pk, "enabled": True})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().management_ip, ip)

    def test_explicit_management_ip_overrides_primary(self):
        self._assign_primary_ip(self.device, "10.0.0.11/24")
        chosen = IPAddress.objects.create(address="10.0.0.12/24")
        form = MonitoringProfileForm(
            data={"device": self.device.pk, "management_ip": chosen.pk, "enabled": True}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().management_ip, chosen)

    def test_no_resolvable_management_ip_is_invalid(self):
        # self.device has no primary IP and no explicit management IP.
        form = MonitoringProfileForm(data={"device": self.device.pk, "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertIn("management_ip", form.errors)

    def test_clean_raises_without_target(self):
        form = MonitoringProfileForm(data={"enabled": True})
        self.assertFalse(form.is_valid())
        with self.assertRaises(ValidationError):
            form.clean()

    def test_duplicate_target_is_invalid(self):
        MonitoringProfile.objects.create(assigned_object=self.device)
        form = MonitoringProfileForm(data={"device": self.device.pk, "enabled": True})
        self.assertFalse(form.is_valid())
        self.assertEqual(MonitoringProfile.objects.count(), 1)
