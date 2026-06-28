# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for pre-push intent validation (FR-8)."""

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
from virtualization.models import (
    Cluster,
    ClusterType,
    VirtualMachine,
    VMInterface,
)

from netbox_opennms.models import MonitoredService, MonitoringProfile
from netbox_opennms.validation import validate_profile


class ValidateProfileTest(TestCase):
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
        cls.mgmt = IPAddress.objects.create(
            address="10.0.0.1/24", assigned_object=iface
        )
        cls.extra = IPAddress.objects.create(
            address="10.0.0.2/24", assigned_object=iface
        )
        cls.profile = MonitoringProfile.objects.create(
            assigned_object=cls.device, management_ip=cls.mgmt
        )
        cls.cluster = Cluster.objects.create(
            name="c1", type=ClusterType.objects.create(name="t1", slug="t1")
        )

    def test_valid_profile_has_no_errors_or_warnings(self):
        result = validate_profile(self.profile)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])
        self.assertTrue(result.ok)

    def test_missing_management_ip_is_error(self):
        self.profile.management_ip = None
        result = validate_profile(self.profile)
        self.assertFalse(result.ok)
        self.assertTrue(any("management IP" in e for e in result.errors))

    def test_invalid_location_is_error(self):
        self.profile.location = "bad name"
        result = validate_profile(self.profile)
        self.assertTrue(any("location" in e for e in result.errors))

    def test_off_object_management_ip_is_error(self):
        off = IPAddress.objects.create(address="10.9.9.7/24")  # on no interface
        self.profile.management_ip = off
        result = validate_profile(self.profile)
        self.assertTrue(
            any("management IP" in e and "not assigned" in e for e in result.errors)
        )

    def test_off_object_additional_ip_is_error(self):
        off = IPAddress.objects.create(address="10.9.9.9/24")  # on no interface
        self.profile.additional_ips.set([off])
        result = validate_profile(self.profile)
        self.assertTrue(any("not assigned to the object" in e for e in result.errors))

    def test_off_profile_service_ip_is_error(self):
        off = IPAddress.objects.create(address="10.9.9.8/24")
        MonitoredService.objects.create(
            profile=self.profile, ip_address=off, name="ICMP"
        )
        result = validate_profile(self.profile)
        self.assertTrue(any("monitored IP" in e for e in result.errors))

    def test_non_device_vm_target_is_error(self):
        site = Site.objects.get(slug="raleigh")
        self.profile.assigned_object = site
        result = validate_profile(self.profile)
        self.assertTrue(any("Device or VirtualMachine" in e for e in result.errors))

    def test_missing_site_and_role_is_warning(self):
        vm = VirtualMachine.objects.create(name="vm-1", cluster=self.cluster)
        vm_iface = VMInterface.objects.create(virtual_machine=vm, name="eth0")
        vm_ip = IPAddress.objects.create(
            address="10.0.0.20/24", assigned_object=vm_iface
        )
        profile = MonitoringProfile.objects.create(
            assigned_object=vm, management_ip=vm_ip
        )
        result = validate_profile(profile)
        self.assertTrue(result.ok)  # warnings don't block
        self.assertTrue(any("no-site" in w for w in result.warnings))
        self.assertTrue(any("no-role" in w for w in result.warnings))
