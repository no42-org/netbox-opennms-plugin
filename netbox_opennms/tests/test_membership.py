# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the membership/resolution layer (Epic 5)."""

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

from netbox_opennms.choices import InterfaceScopeChoices
from netbox_opennms.membership import (
    governing_assignment,
    members,
    monitored_foreign_sources,
    parse_foreign_source,
    resolve,
    resolve_node,
)
from netbox_opennms.models import (
    MonitoringAssignment,
    MonitoringOverride,
    MonitoringProfile,
)

FS = "netbox.raleigh.router"


class MembershipTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(name="Raleigh", slug="raleigh")
        cls.router = DeviceRole.objects.create(name="Router", slug="router")
        cls.server = DeviceRole.objects.create(name="Server", slug="server")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        cls.dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.profile = MonitoringProfile.objects.create(name="Network device")

    def _device(self, name, role=None, site=None, ip="10.0.0.1/24", primary=True):
        device = Device.objects.create(
            name=name,
            device_type=self.dt,
            role=role or self.router,
            site=site or self.site,
        )
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        address = IPAddress.objects.create(address=ip, assigned_object=iface)
        if primary:
            device.primary_ip4 = address
            device.save()
        return device, address

    def _vm(self, name, role=None, ip="10.1.0.1/24"):
        ct = ClusterType.objects.create(name="vmware", slug="vmware")
        cluster = Cluster.objects.create(name=f"c-{name}", type=ct, scope=self.site)
        vm = VirtualMachine.objects.create(
            name=name, cluster=cluster, role=role or self.router
        )
        iface = VMInterface.objects.create(virtual_machine=vm, name="eth0")
        address = IPAddress.objects.create(address=ip, assigned_object=iface)
        vm.primary_ip4 = address
        vm.save()
        return vm, address

    # --- parse_foreign_source ----------------------------------------------

    def test_parse_foreign_source_roundtrip(self):
        self.assertEqual(parse_foreign_source(FS), ("raleigh", "router"))
        self.assertEqual(
            parse_foreign_source("netbox.no-site.no-role"), (None, None)
        )

    def test_parse_foreign_source_rejects_foreign_name(self):
        with self.assertRaises(ValueError):
            parse_foreign_source("something.else")

    # --- governing_assignment ----------------------------------------------

    def test_role_assignment_beats_site_level(self):
        site_level = MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=None
        )
        role_level = MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.router
        )
        self.assertEqual(governing_assignment(FS), role_level)
        # A role with no specific assignment falls back to the site-level one.
        self.assertEqual(
            governing_assignment("netbox.raleigh.server"), site_level
        )

    def test_no_assignment_returns_none(self):
        self.assertIsNone(governing_assignment(FS))

    def test_no_site_foreign_source_never_governed(self):
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=None
        )
        self.assertIsNone(governing_assignment("netbox.no-site.no-role"))

    # --- members ------------------------------------------------------------

    def test_members_devices_by_site_and_role(self):
        device, _ = self._device("rtr-1")
        self._device("srv-1", role=self.server)  # different role, excluded
        self.assertEqual([m.pk for m in members(FS)], [device.pk])

    def test_members_includes_vm_via_cluster_scope(self):
        device, _ = self._device("rtr-1")
        vm, _ = self._vm("vm-1")
        self.assertEqual(
            {(type(m).__name__, m.pk) for m in members(FS)},
            {("Device", device.pk), ("VirtualMachine", vm.pk)},
        )

    # --- resolve_node -------------------------------------------------------

    def test_resolve_node_primary_interface(self):
        device, address = self._device("rtr-1")
        assignment = MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.router
        )
        node, warning = resolve_node(device, assignment, None)
        self.assertIsNone(warning)
        self.assertEqual(node.node_label, "rtr-1")
        self.assertEqual(node.foreign_id, f"device-{device.pk}")
        self.assertEqual(len(node.interfaces), 1)
        self.assertTrue(node.interfaces[0].primary)
        self.assertEqual(node.interfaces[0].ip, "10.0.0.1")

    def test_resolve_node_no_primary_ip_is_skipped_warning(self):
        device, _ = self._device("rtr-x", primary=False)
        assignment = MonitoringAssignment(profile=self.profile, site=self.site)
        node, warning = resolve_node(device, assignment, None)
        self.assertIsNone(node)
        self.assertIn("no management IP", warning)

    def test_resolve_node_exclude_override(self):
        device, _ = self._device("rtr-1")
        assignment = MonitoringAssignment(profile=self.profile, site=self.site)
        override = MonitoringOverride.objects.create(
            assigned_object=device, exclude=True
        )
        node, warning = resolve_node(device, assignment, override)
        self.assertIsNone(node)
        self.assertIsNone(warning)

    def test_resolve_node_management_ip_override(self):
        device, _ = self._device("rtr-1")
        iface = Interface.objects.create(device=device, name="eth1", type="virtual")
        alt = IPAddress.objects.create(address="10.0.0.250/24", assigned_object=iface)
        assignment = MonitoringAssignment(profile=self.profile, site=self.site)
        override = MonitoringOverride.objects.create(
            assigned_object=device, management_ip=alt
        )
        node, _ = resolve_node(device, assignment, override)
        primary = [i for i in node.interfaces if i.primary][0]
        self.assertEqual(primary.ip, "10.0.0.250")

    def test_resolve_node_all_interfaces_scope(self):
        profile = MonitoringProfile.objects.create(
            name="All IPs", default_interfaces=InterfaceScopeChoices.ALL
        )
        device, _ = self._device("rtr-1")
        iface = Interface.objects.create(device=device, name="eth1", type="virtual")
        IPAddress.objects.create(address="10.0.0.2/24", assigned_object=iface)
        assignment = MonitoringAssignment(profile=profile, site=self.site)
        node, _ = resolve_node(device, assignment, None)
        ips = sorted(i.ip for i in node.interfaces)
        self.assertEqual(ips, ["10.0.0.1", "10.0.0.2"])

    def test_resolve_node_location_override_then_assignment(self):
        device, _ = self._device("rtr-1")
        assignment = MonitoringAssignment(
            profile=self.profile, site=self.site, location="core"
        )
        # Override location wins.
        override = MonitoringOverride.objects.create(
            assigned_object=device, location="edge"
        )
        node, _ = resolve_node(device, assignment, override)
        self.assertEqual(node.location, "edge")
        # With no override location, the assignment's is used.
        device2, _ = self._device("rtr-2", ip="10.0.0.3/24")
        node2, _ = resolve_node(device2, assignment, None)
        self.assertEqual(node2.location, "core")

    # --- resolve ------------------------------------------------------------

    def test_resolve_none_when_not_governed(self):
        self._device("rtr-1")
        self.assertIsNone(resolve(FS))

    def test_resolve_collects_nodes_and_warnings_sorted(self):
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.router
        )
        d1, _ = self._device("rtr-1")
        d2, _ = self._device("rtr-2", ip="10.0.0.2/24")
        self._device("rtr-no-ip", ip="10.0.0.9/24", primary=False)  # warns, skipped
        resolution = resolve(FS)
        self.assertEqual(
            [n.foreign_id for n in resolution.nodes],
            sorted([f"device-{d1.pk}", f"device-{d2.pk}"]),
        )
        self.assertEqual(len(resolution.warnings), 1)

    # --- monitored_foreign_sources -----------------------------------------

    def test_monitored_foreign_sources_role_assignment(self):
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.router
        )
        self._device("rtr-1")
        self.assertEqual(monitored_foreign_sources(), [FS])

    def test_monitored_foreign_sources_site_level_fans_out(self):
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=None
        )
        self._device("rtr-1", role=self.router)
        self._device("srv-1", role=self.server)
        self.assertEqual(
            monitored_foreign_sources(),
            ["netbox.raleigh.router", "netbox.raleigh.server"],
        )

    def test_monitored_foreign_sources_specific_wins_no_double(self):
        # A site-level + a role-level assignment: the router FS is attributed to
        # the role assignment, server to the site-level — each FS appears once.
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=None
        )
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.router
        )
        self._device("rtr-1", role=self.router)
        self._device("srv-1", role=self.server)
        self.assertEqual(
            monitored_foreign_sources(),
            ["netbox.raleigh.router", "netbox.raleigh.server"],
        )
