# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the Foreign Source derivation (AD-14)."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    Region,
    Site,
)
from django.test import SimpleTestCase, TestCase
from virtualization.models import Cluster, ClusterType, VirtualMachine

from netbox_opennms.derivation import (
    foreign_source_for,
    validate_foreign_source_name,
    validate_location_name,
)


class ForeignSourceDerivationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Raleigh", slug="raleigh")
        cls.other_site = Site.objects.create(name="Durham", slug="durham")
        cls.role = DeviceRole.objects.create(name="Core Router", slug="core-router")
        manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model 1", slug="model-1"
        )
        cls.cluster_type = ClusterType.objects.create(name="Type 1", slug="type-1")

    def test_device_uses_site_and_role(self):
        device = Device.objects.create(
            name="rtr-1",
            device_type=self.device_type,
            role=self.role,
            site=self.site,
        )
        self.assertEqual(foreign_source_for(device), "netbox.raleigh.core-router")

    def test_vm_uses_its_own_site(self):
        # cluster scoped to durham, but the VM's own site (raleigh) wins.
        cluster = Cluster.objects.create(
            name="c1", type=self.cluster_type, scope=self.other_site
        )
        vm = VirtualMachine.objects.create(
            name="vm-1", cluster=cluster, site=self.site, role=self.role
        )
        self.assertEqual(foreign_source_for(vm), "netbox.raleigh.core-router")

    def test_vm_falls_back_to_cluster_site(self):
        cluster = Cluster.objects.create(
            name="c2", type=self.cluster_type, scope=self.site
        )
        vm = VirtualMachine.objects.create(name="vm-2", cluster=cluster, role=self.role)
        self.assertEqual(foreign_source_for(vm), "netbox.raleigh.core-router")

    def test_missing_site_token(self):
        cluster = Cluster.objects.create(name="c3", type=self.cluster_type)
        vm = VirtualMachine.objects.create(name="vm-3", cluster=cluster, role=self.role)
        self.assertEqual(foreign_source_for(vm), "netbox.no-site.core-router")

    def test_missing_role_token(self):
        cluster = Cluster.objects.create(
            name="c4", type=self.cluster_type, scope=self.site
        )
        vm = VirtualMachine.objects.create(name="vm-4", cluster=cluster)
        self.assertEqual(foreign_source_for(vm), "netbox.raleigh.no-role")

    def test_non_site_cluster_scope_yields_no_site(self):
        # A cluster scoped to a Region (not a Site) does not resolve a site.
        region = Region.objects.create(name="East", slug="east")
        cluster = Cluster.objects.create(
            name="c5", type=self.cluster_type, scope=region
        )
        vm = VirtualMachine.objects.create(name="vm-5", cluster=cluster, role=self.role)
        self.assertEqual(foreign_source_for(vm), "netbox.no-site.core-router")

    def test_non_device_or_vm_raises(self):
        with self.assertRaises(TypeError):
            foreign_source_for(self.site)
        with self.assertRaises(TypeError):
            foreign_source_for(None)


class ForeignSourceNameValidationTest(SimpleTestCase):
    def test_valid_name_passes(self):
        self.assertEqual(
            validate_foreign_source_name("netbox.raleigh.core-router"),
            "netbox.raleigh.core-router",
        )

    def test_forbidden_characters_rejected(self):
        # ':' is forbidden by OpenNMS (Horizon 35 400s on import) — Story 4.4.
        for bad in ["a/b", "a\\b", "a?b", "a*b", "a'b", 'a"b', "a:b"]:
            with self.assertRaises(ValueError):
                validate_foreign_source_name(bad)


class LocationNameValidationTest(SimpleTestCase):
    def test_valid_names_pass(self):
        for ok in ["", "Default", "RDU.1-edge", "loc-01"]:
            self.assertEqual(validate_location_name(ok), ok)

    def test_invalid_names_rejected(self):
        for bad in ["bad name", "a/b", "héllo", "a_b", "a:b"]:
            with self.assertRaises(ValueError):
                validate_location_name(bad)
