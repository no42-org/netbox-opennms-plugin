# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
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
from virtualization.models import (
    Cluster,
    ClusterType,
    VirtualMachine,
    VMInterface,
)

from netbox_opennms.api.serializers import (
    MonitoredServiceSerializer,
    MonitoringProfileSerializer,
)
from netbox_opennms.choices import ServiceChoices
from netbox_opennms.forms import MonitoredServiceForm, MonitoringProfileForm
from netbox_opennms.models import MonitoredService, MonitoringProfile


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
    def _add_ip(cls, device, address):
        interface = Interface.objects.create(
            device=device, name=f"extra-{address}", type="virtual"
        )
        return IPAddress.objects.create(address=address, assigned_object=interface)

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

    def test_additional_ip_on_object_is_valid(self):
        self._assign_primary_ip(self.device, "10.0.0.20/24")
        extra = self._add_ip(self.device, "10.0.0.21/24")
        form = MonitoringProfileForm(
            data={
                "device": self.device.pk,
                "additional_ips": [extra.pk],
                "enabled": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(extra, form.save().additional_ips.all())

    def test_additional_ip_not_on_object_is_invalid(self):
        self._assign_primary_ip(self.device, "10.0.0.22/24")
        foreign = IPAddress.objects.create(address="10.0.0.99/24")  # on no interface
        form = MonitoringProfileForm(
            data={
                "device": self.device.pk,
                "additional_ips": [foreign.pk],
                "enabled": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("additional_ips", form.errors)

    def test_management_ip_dropped_from_additional(self):
        mgmt = self._assign_primary_ip(self.device, "10.0.0.23/24")
        form = MonitoringProfileForm(
            data={
                "device": self.device.pk,
                "management_ip": mgmt.pk,
                "additional_ips": [mgmt.pk],
                "enabled": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn(mgmt, form.save().additional_ips.all())

    def test_additional_ip_on_vm_target_is_valid(self):
        # the membership chain (target.interfaces -> ip_addresses) for a VM target
        iface = VMInterface.objects.create(virtual_machine=self.vm, name="eth0")
        mgmt = IPAddress.objects.create(address="10.1.0.1/24", assigned_object=iface)
        extra = IPAddress.objects.create(address="10.1.0.2/24", assigned_object=iface)
        form = MonitoringProfileForm(
            data={
                "virtual_machine": self.vm.pk,
                "management_ip": mgmt.pk,
                "additional_ips": [extra.pk],
                "enabled": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(extra, form.save().additional_ips.all())

    def test_serializer_rejects_off_object_additional_ip(self):
        off = IPAddress.objects.create(address="10.0.0.99/24")  # on no interface
        serializer = MonitoringProfileSerializer(
            data={
                "assigned_object_type": "dcim.device",
                "assigned_object_id": self.device.pk,
                "additional_ips": [off.pk],
                "enabled": True,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("additional_ips", serializer.errors)

    def test_serializer_accepts_on_object_additional_ip(self):
        on = self._add_ip(self.device, "10.0.0.30/24")
        serializer = MonitoringProfileSerializer(
            data={
                "assigned_object_type": "dcim.device",
                "assigned_object_id": self.device.pk,
                "additional_ips": [on.pk],
                "enabled": True,
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_invalid_location_rejected(self):
        ip = IPAddress.objects.create(address="10.0.0.50/24")
        form = MonitoringProfileForm(
            data={
                "device": self.device.pk,
                "management_ip": ip.pk,
                "location": "bad name",
                "enabled": True,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("location", form.errors)

    def test_valid_location_saves(self):
        ip = IPAddress.objects.create(address="10.0.0.51/24")
        form = MonitoringProfileForm(
            data={
                "device": self.device.pk,
                "management_ip": ip.pk,
                "location": "RDU.1",
                "enabled": True,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().location, "RDU.1")

    def test_serializer_rejects_invalid_location(self):
        serializer = MonitoringProfileSerializer(
            data={
                "assigned_object_type": "dcim.device",
                "assigned_object_id": self.device.pk,
                "location": "bad name",
                "enabled": True,
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("location", serializer.errors)


class MonitoredServiceTest(MonitoringProfileTestData, TestCase):
    def _profile_with_ips(self):
        mgmt = self._assign_primary_ip(self.device, "10.0.0.40/24")
        extra = self._add_ip(self.device, "10.0.0.41/24")
        profile = MonitoringProfile.objects.create(
            assigned_object=self.device, management_ip=mgmt
        )
        profile.additional_ips.set([extra])
        return profile, mgmt, extra

    def test_choiceset_key_enables_field_choices(self):
        # key drives the FIELD_CHOICES extension point (AC4). Admin extension
        # resolves at config/import time (ChoiceSet freezes choices when the
        # class is defined), so it can't be exercised via override_settings here.
        self.assertEqual(ServiceChoices.key, "MonitoredService.name")

    def test_name_validated_against_choiceset(self):
        # proves the ChoiceSet is wired as the field's choices (a default value
        # passes, an unknown one is rejected)
        profile, mgmt, _extra = self._profile_with_ips()
        MonitoredService(profile=profile, ip_address=mgmt, name="SNMP").full_clean()
        with self.assertRaises(ValidationError):
            MonitoredService(profile=profile, ip_address=mgmt, name="NOPE").full_clean()

    def test_services_pruned_when_additional_ip_removed(self):
        profile, _mgmt, extra = self._profile_with_ips()
        svc = MonitoredService.objects.create(
            profile=profile, ip_address=extra, name="HTTP"
        )
        profile.additional_ips.remove(extra)
        self.assertFalse(MonitoredService.objects.filter(pk=svc.pk).exists())

    def test_services_pruned_when_management_ip_changed(self):
        profile, mgmt, _extra = self._profile_with_ips()
        svc = MonitoredService.objects.create(
            profile=profile, ip_address=mgmt, name="ICMP"
        )
        profile.management_ip = self._add_ip(self.device, "10.0.0.42/24")
        profile.save()
        self.assertFalse(MonitoredService.objects.filter(pk=svc.pk).exists())

    def test_service_on_management_ip_is_valid(self):
        profile, mgmt, _extra = self._profile_with_ips()
        MonitoredService(profile=profile, ip_address=mgmt, name="ICMP").full_clean()

    def test_service_on_additional_ip_is_valid(self):
        profile, _mgmt, extra = self._profile_with_ips()
        MonitoredService(profile=profile, ip_address=extra, name="HTTP").full_clean()

    def test_service_on_foreign_ip_is_invalid(self):
        profile, _mgmt, _extra = self._profile_with_ips()
        off = IPAddress.objects.create(address="10.0.0.99/24")
        with self.assertRaises(ValidationError):
            MonitoredService(profile=profile, ip_address=off, name="ICMP").full_clean()

    def test_unique_service_per_profile_ip_name(self):
        profile, mgmt, _extra = self._profile_with_ips()
        MonitoredService.objects.create(profile=profile, ip_address=mgmt, name="ICMP")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MonitoredService.objects.create(
                profile=profile, ip_address=mgmt, name="ICMP"
            )

    def test_form_rejects_foreign_ip(self):
        profile, _mgmt, _extra = self._profile_with_ips()
        off = IPAddress.objects.create(address="10.0.0.98/24")
        form = MonitoredServiceForm(
            data={"profile": profile.pk, "ip_address": off.pk, "name": "ICMP"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("ip_address", form.errors)

    def test_serializer_rejects_foreign_ip(self):
        profile, _mgmt, _extra = self._profile_with_ips()
        off = IPAddress.objects.create(address="10.0.0.97/24")
        serializer = MonitoredServiceSerializer(
            data={"profile": profile.pk, "ip_address": off.pk, "name": "ICMP"}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("ip_address", serializer.errors)
