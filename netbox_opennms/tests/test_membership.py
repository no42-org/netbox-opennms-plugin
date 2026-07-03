# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the membership/resolution layer (Requisition redesign)."""

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
    filter_errors,
    matching_requisitions,
    monitored_foreign_sources,
    requisition_conflicts,
    resolve,
    resolve_all,
    resolve_node,
)
from netbox_opennms.models import (
    MonitoredInterface,
    MonitoredService,
    MonitoringOverride,
    Requisition,
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

    def _requisition(self, name=FS, filter_params=None, **kw):
        if filter_params is None:
            filter_params = {"site": ["raleigh"], "role": ["router"]}
        return Requisition.objects.create(name=name, filter_params=filter_params, **kw)

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
            name=name, cluster=cluster, site=self.site, role=role or self.router
        )
        iface = VMInterface.objects.create(virtual_machine=vm, name="eth0")
        address = IPAddress.objects.create(address=ip, assigned_object=iface)
        vm.primary_ip4 = address
        vm.save()
        return vm, address

    # --- filter_errors (the guard) -----------------------------------------

    def test_valid_filter_has_no_errors(self):
        req = self._requisition()
        self.assertEqual(filter_errors(req), [])

    def test_unknown_key_rejected(self):
        req = self._requisition(filter_params={"nonsense_key": ["x"]})
        self.assertTrue(any("not recognized" in e for e in filter_errors(req)))

    def test_empty_filter_rejected(self):
        req = self._requisition(filter_params={})
        self.assertTrue(any("no effective constraint" in e for e in filter_errors(req)))

    def test_empty_value_filter_rejected(self):
        # A known key with an empty value is a no-op catch-all — must be rejected.
        req = self._requisition(filter_params={"role": []})
        self.assertTrue(any("no effective constraint" in e for e in filter_errors(req)))

    def test_stale_filter_value_matches_nothing_not_everything(self):
        # A recognized key with an unresolvable value must match NOTHING (not the
        # whole pool) and warn — the catch-all guard the review caught.
        self._device("rtr-1")
        self._requisition(filter_params={"role": ["does-not-exist"]})
        resolution = resolve(FS)
        self.assertEqual(resolution.nodes, [])
        self.assertTrue(any("invalid" in w for w in resolution.warnings))

    # --- membership ---------------------------------------------------------

    def test_filter_selects_devices_by_site_and_role(self):
        device, _ = self._device("rtr-1")
        self._device("srv-1", role=self.server)  # different role, excluded
        self._requisition()
        resolution = resolve(FS)
        self.assertEqual(
            [n.foreign_id for n in resolution.nodes], [f"device-{device.pk}"]
        )

    def test_membership_includes_vm_by_site_and_role(self):
        device, _ = self._device("rtr-1")
        vm, _ = self._vm("vm-1")
        self._requisition()
        resolution = resolve(FS)
        self.assertEqual(
            {n.foreign_id for n in resolution.nodes},
            {f"device-{device.pk}", f"vm-{vm.pk}"},
        )

    def test_vm_matched_by_site_via_cluster_scope(self):
        # A VM sited ONLY via its cluster's scope (no direct site) is matched by a
        # {"site": ...} filter (review #6 / AD-14).
        ct = ClusterType.objects.create(name="vmw", slug="vmw")
        cluster = Cluster.objects.create(name="c-scoped", type=ct, scope=self.site)
        vm = VirtualMachine.objects.create(
            name="vm-scoped", cluster=cluster, role=self.router
        )
        iface = VMInterface.objects.create(virtual_machine=vm, name="eth0")
        ip = IPAddress.objects.create(address="10.2.0.1/24", assigned_object=iface)
        vm.primary_ip4 = ip
        vm.save()
        self._requisition()
        self.assertIn(f"vm-{vm.pk}", [n.foreign_id for n in resolve(FS).nodes])

    def test_cross_type_key_requires_each_type_constrained(self):
        # object_types='both' + a Device-only key would claim every VM — rejected (#3).
        req = self._requisition(filter_params={"manufacturer": ["acme"]})
        self.assertTrue(
            any(
                "does not constrain virtual machines" in e
                for e in filter_errors(req)
            )
        )

    def test_custom_field_filter_key_is_accepted(self):
        # cf_* filters are added per-instance (not in base_filters), so the guard
        # must recognize a custom-field filter key (review #2).
        from core.models import ObjectType
        from extras.models import CustomField

        cf = CustomField.objects.create(name="datacenter", type="text")
        cf.object_types.set([ObjectType.objects.get_for_model(Device)])
        req = self._requisition(
            filter_params={"cf_datacenter": ["dc1"]}, object_types="device"
        )
        self.assertEqual(filter_errors(req), [])

    # --- conflicts (C1/C3/C4) ------------------------------------------------

    def test_overlap_is_a_conflict_on_every_involved_requisition(self):
        device, _ = self._device("rtr-1")
        self._requisition(name="a", filter_params={"role": ["router"]})
        self._requisition(name="b", filter_params={"site": ["raleigh"]})
        by_name = {r.foreign_source: r for r in resolve_all()}
        for name in ("a", "b"):
            resolution = by_name[name]
            self.assertEqual(resolution.nodes, [])  # conflicted → rendered nowhere
            self.assertEqual(len(resolution.conflicts), 1)
            conflict = resolution.conflicts[0]
            self.assertEqual(conflict.foreign_id, f"device-{device.pk}")
            self.assertEqual(conflict.requisition_names, ["a", "b"])

    def test_matching_requisitions_single_then_conflicted(self):
        device, _ = self._device("rtr-1")
        a = self._requisition(name="a", filter_params={"role": ["router"]})
        self.assertEqual(matching_requisitions(device), [a])
        b = self._requisition(name="b", filter_params={"site": ["raleigh"]})
        self.assertEqual(set(matching_requisitions(device)), {a, b})

    def test_exclusion_resolves_a_conflict(self):
        # C3: an excluded object is monitored nowhere — no ambiguity, no conflict.
        device, _ = self._device("rtr-1")
        self._requisition(name="a", filter_params={"role": ["router"]})
        self._requisition(name="b", filter_params={"site": ["raleigh"]})
        MonitoringOverride.objects.create(assigned_object=device, exclude=True)
        for resolution in resolve_all():
            self.assertEqual(resolution.conflicts, [])
            self.assertEqual(resolution.nodes, [])

    def test_resolution_is_order_independent(self):
        # C4/3.6: disjoint filters resolve identically regardless of name/creation
        # order — nothing claims, nothing is order-dependent.
        d1, _ = self._device("rtr-1")
        d2, _ = self._device("srv-1", role=self.server, ip="10.0.0.2/24")
        self._requisition(name="zz-routers", filter_params={"role": ["router"]})
        self._requisition(name="aa-servers", filter_params={"role": ["server"]})
        by_name = {r.foreign_source: r for r in resolve_all()}
        self.assertEqual(
            [n.foreign_id for n in by_name["zz-routers"].nodes],
            [f"device-{d1.pk}"],
        )
        self.assertEqual(
            [n.foreign_id for n in by_name["aa-servers"].nodes],
            [f"device-{d2.pk}"],
        )
        self.assertEqual(by_name["zz-routers"].conflicts, [])
        self.assertEqual(by_name["aa-servers"].conflicts, [])

    def test_negated_filter_resolves_layering(self):
        # C7/6.2: the layering escape hatch — tag__n makes overlapping intents
        # disjoint ("critical here, the remaining routers there").
        from extras.models import Tag

        tag = Tag.objects.create(name="critical", slug="critical")
        tagged, _ = self._device("rtr-critical")
        tagged.tags.add(tag)
        plain, _ = self._device("rtr-plain", ip="10.0.0.2/24")
        self._requisition(name="critical", filter_params={"tag": ["critical"]})
        self._requisition(
            name="routers",
            filter_params={"role": ["router"], "tag__n": ["critical"]},
        )
        by_name = {r.foreign_source: r for r in resolve_all()}
        self.assertEqual(by_name["critical"].conflicts, [])
        self.assertEqual(by_name["routers"].conflicts, [])
        self.assertEqual(
            [n.foreign_id for n in by_name["critical"].nodes],
            [f"device-{tagged.pk}"],
        )
        self.assertEqual(
            [n.foreign_id for n in by_name["routers"].nodes],
            [f"device-{plain.pk}"],
        )

    def test_frozen_requisition_counts_as_monitored(self):
        # C5: frozen ≠ orphan — a conflicted requisition must never drop out of
        # the monitored set, or the drift reconciler would tear it down.
        self._device("rtr-1")
        self._requisition(name="a", filter_params={"role": ["router"]})
        self._requisition(name="b", filter_params={"site": ["raleigh"]})
        self.assertEqual(monitored_foreign_sources(), ["a", "b"])

    def test_rejected_filter_populates_rejected_not_warnings(self):
        # Round-2: rejection lives on Resolution.rejected (blocking), not in
        # warnings — so the same text isn't reported twice.
        self._requisition(filter_params={"bogus": ["x"]})
        resolution = resolve(FS)
        self.assertTrue(resolution.rejected)
        self.assertEqual(resolution.warnings, [])
        self.assertEqual(resolution.nodes, [])

    def test_rejected_filter_keeps_foreign_source_monitored(self):
        # Round-2: a rejected filter is blocked from syncing, so it must be
        # equally blocked from reconciler teardown.
        self._device("rtr-1")
        self._requisition(filter_params={"bogus": ["x"]})
        self.assertEqual(monitored_foreign_sources(), [FS])

    def test_warning_state_requisition_counts_as_monitored(self):
        # Review #1: a requisition whose filter value went stale (warning, no
        # nodes, no conflicts) must NOT drop out of the monitored set — the
        # reconciler would delete its live OpenNMS nodes on a NetBox rename.
        self._device("rtr-1")
        self._requisition(filter_params={"role": ["renamed-away"]})
        self.assertEqual(monitored_foreign_sources(), [FS])

    def test_mixed_frozen_resolution_keeps_unconflicted_nodes(self):
        # Review #14: a frozen requisition still resolves its unconflicted
        # members into nodes (shown in the preview) while the conflicted one
        # becomes a Conflict — and the whole requisition stays sync-blocked.
        shared, _ = self._device("rtr-shared")
        only_a, _ = self._device("srv-only", role=self.server, ip="10.0.0.2/24")
        self._requisition(name="a", filter_params={"site": ["raleigh"]})
        self._requisition(name="b", filter_params={"role": ["router"]})
        by_name = {r.foreign_source: r for r in resolve_all()}
        a = by_name["a"]
        self.assertEqual(
            [c.foreign_id for c in a.conflicts], [f"device-{shared.pk}"]
        )
        self.assertEqual(
            [n.foreign_id for n in a.nodes], [f"device-{only_a.pk}"]
        )
        b = by_name["b"]
        self.assertEqual(len(b.conflicts), 1)
        self.assertEqual(b.nodes, [])

    def test_requisition_conflicts_without_fleet_pass(self):
        # Review #12: the detail-page banner's narrow check matches resolve_all.
        device, _ = self._device("rtr-1")
        a = self._requisition(name="a", filter_params={"role": ["router"]})
        self.assertEqual(requisition_conflicts(a), [])
        self._requisition(name="b", filter_params={"site": ["raleigh"]})
        conflicts = requisition_conflicts(a)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].foreign_id, f"device-{device.pk}")
        self.assertEqual(conflicts[0].requisition_names, ["a", "b"])

    # --- resolve_node -------------------------------------------------------

    def test_resolve_node_primary_interface(self):
        device, _ = self._device("rtr-1")
        req = self._requisition()
        node, warning = resolve_node(device, req, None)
        self.assertIsNone(warning)
        self.assertEqual(node.node_label, "rtr-1")
        self.assertEqual(node.foreign_id, f"device-{device.pk}")
        self.assertEqual(node.interfaces[0].role, "P")
        self.assertEqual(node.interfaces[0].ip, "10.0.0.1")

    def test_declared_services_on_interfaces(self):
        device, _ = self._device("rtr-1")
        req = self._requisition(services=["ICMP", "SNMP"])
        node, _ = resolve_node(device, req, None)
        self.assertEqual(node.interfaces[0].services, ["ICMP", "SNMP"])

    def test_override_suppresses_a_declared_service(self):
        device, _ = self._device("rtr-1")
        req = self._requisition(services=["ICMP", "SNMP"])
        override = MonitoringOverride.objects.create(
            assigned_object=device, suppressed_services=["SNMP"]
        )
        node, _ = resolve_node(device, req, override)
        self.assertEqual(node.interfaces[0].services, ["ICMP"])

    def test_override_adds_a_service_on_an_extra_interface(self):
        device, primary = self._device("rtr-1")
        iface = Interface.objects.create(device=device, name="eth1", type="virtual")
        extra = IPAddress.objects.create(address="10.0.0.9/24", assigned_object=iface)
        req = self._requisition(services=["ICMP"])
        override = MonitoringOverride.objects.create(assigned_object=device)
        MonitoredInterface.objects.create(override=override, ip_address=extra)
        MonitoredService.objects.create(
            override=override, ip_address=extra, name="HTTP"
        )
        node, _ = resolve_node(device, req, override)
        by_ip = {i.ip: i.services for i in node.interfaces}
        self.assertEqual(by_ip["10.0.0.1"], ["ICMP"])
        self.assertEqual(by_ip["10.0.0.9"], ["HTTP", "ICMP"])

    def test_resolve_node_no_primary_ip_is_skipped_warning(self):
        device, _ = self._device("rtr-x", primary=False)
        node, warning = resolve_node(device, self._requisition(), None)
        self.assertIsNone(node)
        self.assertIn("no management IP", warning)

    def test_resolve_node_exclude_is_monitored_nowhere(self):
        device, _ = self._device("rtr-1")
        req = self._requisition()
        override = MonitoringOverride.objects.create(
            assigned_object=device, exclude=True
        )
        node, warning = resolve_node(device, req, override)
        self.assertIsNone(node)
        self.assertIsNone(warning)

    def test_resolve_node_management_ip_override(self):
        device, _ = self._device("rtr-1")
        iface = Interface.objects.create(device=device, name="eth1", type="virtual")
        alt = IPAddress.objects.create(address="10.0.0.250/24", assigned_object=iface)
        override = MonitoringOverride.objects.create(
            assigned_object=device, management_ip=alt
        )
        node, _ = resolve_node(device, self._requisition(), override)
        primary = [i for i in node.interfaces if i.role == "P"][0]
        self.assertEqual(primary.ip, "10.0.0.250")

    def test_resolve_node_interface_roles(self):
        # Promote an additional interface to Primary and demote the management
        # interface to Secondary (RD-5): the rendered roles follow the config.
        device, _ = self._device("rtr-1")
        iface = Interface.objects.create(device=device, name="eth1", type="virtual")
        extra = IPAddress.objects.create(address="10.0.0.9/24", assigned_object=iface)
        override = MonitoringOverride.objects.create(
            assigned_object=device, management_role="S"
        )
        MonitoredInterface.objects.create(
            override=override, ip_address=extra, role="P"
        )
        node, _ = resolve_node(device, self._requisition(), override)
        by_ip = {i.ip: i.role for i in node.interfaces}
        self.assertEqual(by_ip["10.0.0.1"], "S")
        self.assertEqual(by_ip["10.0.0.9"], "P")

    def test_resolve_node_all_interfaces_scope(self):
        device, _ = self._device("rtr-1")
        iface = Interface.objects.create(device=device, name="eth1", type="virtual")
        IPAddress.objects.create(address="10.0.0.2/24", assigned_object=iface)
        req = self._requisition(default_interfaces=InterfaceScopeChoices.ALL)
        node, _ = resolve_node(device, req, None)
        ips = sorted(i.ip for i in node.interfaces)
        self.assertEqual(ips, ["10.0.0.1", "10.0.0.2"])

    def test_resolve_node_location_override_then_requisition(self):
        device, _ = self._device("rtr-1")
        req = self._requisition(location="core")
        override = MonitoringOverride.objects.create(
            assigned_object=device, location="edge"
        )
        self.assertEqual(resolve_node(device, req, override)[0].location, "edge")
        device2, _ = self._device("rtr-2", ip="10.0.0.3/24")
        self.assertEqual(resolve_node(device2, req, None)[0].location, "core")

    # --- resolve / monitored_foreign_sources -------------------------------

    def test_resolve_none_when_no_requisition(self):
        self._device("rtr-1")
        self.assertIsNone(resolve(FS))

    def test_resolve_collects_nodes_and_warnings_sorted(self):
        self._requisition()
        d1, _ = self._device("rtr-1")
        d2, _ = self._device("rtr-2", ip="10.0.0.2/24")
        self._device("rtr-no-ip", ip="10.0.0.9/24", primary=False)  # warns, skipped
        resolution = resolve(FS)
        self.assertEqual(
            [n.foreign_id for n in resolution.nodes],
            sorted([f"device-{d1.pk}", f"device-{d2.pk}"]),
        )
        self.assertEqual(len(resolution.warnings), 1)

    def test_monitored_foreign_sources_only_with_members(self):
        self._requisition()
        self._device("rtr-1")
        # An empty requisition (no matching members) is omitted.
        self._requisition(name="empty", filter_params={"role": ["server"]})
        self.assertEqual(monitored_foreign_sources(), [FS])
