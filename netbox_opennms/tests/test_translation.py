# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the pure render layer (Epic 5): requisition + foreign-source def."""

from django.test import SimpleTestCase, TestCase
from lxml import etree

from netbox_opennms.membership import InterfaceSpec, NodeSpec
from netbox_opennms.models import (
    MonitoringDetector,
    MonitoringPolicy,
    Requisition,
)
from netbox_opennms.translation import (
    RenderError,
    render_foreign_source_definition,
    render_requisition,
)

MI = "{http://xmlns.opennms.org/xsd/config/model-import}"
FS = "{http://xmlns.opennms.org/xsd/config/foreign-source}"
FSNAME = "netbox.raleigh.router"


class RenderRequisitionTest(SimpleTestCase):
    """render_requisition is pure — it reads NodeSpec objects, no DB."""

    def _node(self, **kw):
        kw.setdefault("node_label", "rtr-1")
        kw.setdefault("foreign_id", "device-1")
        kw.setdefault("location", "")
        kw.setdefault(
            "interfaces", [InterfaceSpec("10.0.0.1", "P", services=["ICMP"])]
        )
        return NodeSpec(**kw)

    def test_node_label_foreign_id_and_primary_interface(self):
        xml = render_requisition("netbox.raleigh.router", [self._node()])
        root = etree.fromstring(xml)
        self.assertEqual(root.get("foreign-source"), "netbox.raleigh.router")
        node = root.find(f"{MI}node")
        self.assertEqual(node.get("node-label"), "rtr-1")
        self.assertEqual(node.get("foreign-id"), "device-1")
        iface = node.find(f"{MI}interface")
        self.assertEqual(iface.get("ip-addr"), "10.0.0.1")
        self.assertEqual(iface.get("snmp-primary"), "P")
        service = iface.find(f"{MI}monitored-service")
        self.assertEqual(service.get("service-name"), "ICMP")

    def test_primary_first_then_additional_sorted_non_primary(self):
        node = self._node(
            interfaces=[
                InterfaceSpec("10.0.0.9", "N"),
                InterfaceSpec("10.0.0.1", "P"),
                InterfaceSpec("10.0.0.5", "N"),
            ]
        )
        xml = render_requisition("netbox.raleigh.router", [node])
        ifaces = etree.fromstring(xml).find(f"{MI}node").findall(f"{MI}interface")
        self.assertEqual(
            [(i.get("ip-addr"), i.get("snmp-primary")) for i in ifaces],
            [("10.0.0.1", "P"), ("10.0.0.5", "N"), ("10.0.0.9", "N")],
        )

    def test_location_uses_node_then_default(self):
        with_loc = render_requisition("fs", [self._node(location="edge-1")])
        self.assertEqual(
            etree.fromstring(with_loc).find(f"{MI}node").get("location"), "edge-1"
        )
        fallback = render_requisition(
            "fs", [self._node(location="")], default_location="core"
        )
        self.assertEqual(
            etree.fromstring(fallback).find(f"{MI}node").get("location"), "core"
        )

    def test_no_location_attribute_when_blank(self):
        xml = render_requisition("fs", [self._node(location="")])
        self.assertIsNone(etree.fromstring(xml).find(f"{MI}node").get("location"))

    def test_empty_nodes_is_node_less(self):
        xml = render_requisition("fs", [])
        self.assertNotIn(b"<node", xml)

    def test_services_sorted(self):
        node = self._node(
            interfaces=[InterfaceSpec("10.0.0.1", "P", services=["SSH", "ICMP"])]
        )
        xml = render_requisition("fs", [node])
        names = [
            s.get("service-name")
            for s in etree.fromstring(xml).iter(f"{MI}monitored-service")
        ]
        self.assertEqual(names, ["ICMP", "SSH"])

    def test_assets_and_scoped_metadata_render(self):
        node = self._node(
            interfaces=[InterfaceSpec("10.0.0.1", "P", services=["ICMP"])],
            assets=[("serialNumber", "SN-1")],
            node_metadata=[("requisition", "owner", "neteng")],
            interface_metadata=[("X-netbox", "vlan", "10")],
            service_metadata=[("requisition", "sla", "gold")],
        )
        n = etree.fromstring(render_requisition("fs", [node])).find(f"{MI}node")
        asset = n.find(f"{MI}asset")
        self.assertEqual(asset.get("name"), "serialNumber")
        self.assertEqual(asset.get("value"), "SN-1")
        node_md = n.findall(f"{MI}meta-data")[0]
        self.assertEqual(
            (node_md.get("context"), node_md.get("key"), node_md.get("value")),
            ("requisition", "owner", "neteng"),
        )
        iface = n.find(f"{MI}interface")
        imd = iface.find(f"{MI}meta-data")
        self.assertEqual((imd.get("context"), imd.get("key")), ("X-netbox", "vlan"))
        svc = iface.find(f"{MI}monitored-service")
        smd = svc.find(f"{MI}meta-data")
        self.assertEqual((smd.get("context"), smd.get("key")), ("requisition", "sla"))

    def test_no_enrichment_renders_no_asset_or_metadata(self):
        n = etree.fromstring(render_requisition("fs", [self._node()])).find(f"{MI}node")
        self.assertIsNone(n.find(f"{MI}asset"))
        self.assertIsNone(n.find(f"{MI}meta-data"))

    def test_render_error_no_label(self):
        with self.assertRaises(RenderError):
            render_requisition("fs", [self._node(node_label="")])

    def test_render_error_no_interface(self):
        with self.assertRaises(RenderError):
            render_requisition("fs", [self._node(interfaces=[])])


class RenderForeignSourceDefinitionTest(TestCase):
    """render_foreign_source_definition reads a Requisition's detectors/policies."""

    @classmethod
    def setUpTestData(cls):
        cls.requisition = Requisition.objects.create(
            name="netbox.raleigh.router",
            scan_interval="30m",
            filter_params={"site": ["raleigh"], "role": ["router"]},
        )
        MonitoringDetector.objects.create(
            requisition=cls.requisition,
            name="ICMP",
            rule_class="org.opennms.netmgt.provision.detector.icmp.IcmpDetector",
            parameters={"timeout": "2000", "retries": "1"},
        )
        MonitoringPolicy.objects.create(
            requisition=cls.requisition,
            name="Categorise",
            rule_class="org.opennms.netmgt.provision.persist.policies."
            "NodeCategorySettingPolicy",
            parameters={"category": "Routers"},
        )

    def test_scan_interval_and_name(self):
        root = etree.fromstring(
            render_foreign_source_definition(FSNAME, self.requisition)
        )
        self.assertEqual(root.get("name"), FSNAME)
        self.assertEqual(root.find(f"{FS}scan-interval").text, "30m")

    def test_detector_emits_class_and_sorted_parameters(self):
        root = etree.fromstring(
            render_foreign_source_definition(FSNAME, self.requisition)
        )
        detector = root.find(f"{FS}detectors/{FS}detector")
        self.assertEqual(detector.get("name"), "ICMP")
        self.assertEqual(
            detector.get("class"),
            "org.opennms.netmgt.provision.detector.icmp.IcmpDetector",
        )
        params = [
            (p.get("key"), p.get("value")) for p in detector.findall(f"{FS}parameter")
        ]
        self.assertEqual(params, [("retries", "1"), ("timeout", "2000")])

    def test_policy_emitted(self):
        root = etree.fromstring(
            render_foreign_source_definition(FSNAME, self.requisition)
        )
        policy = root.find(f"{FS}policies/{FS}policy")
        self.assertEqual(policy.get("name"), "Categorise")
        self.assertEqual(policy.find(f"{FS}parameter").get("key"), "category")

    def test_detectors_present_reverses_ad11(self):
        root = etree.fromstring(
            render_foreign_source_definition(FSNAME, self.requisition)
        )
        self.assertEqual(len(root.find(f"{FS}detectors")), 1)

    def test_render_error_detector_without_class(self):
        bare = Requisition.objects.create(
            name="bare", filter_params={"site": ["raleigh"]}
        )
        MonitoringDetector.objects.create(requisition=bare, name="x", rule_class="")
        with self.assertRaises(RenderError):
            render_foreign_source_definition(FSNAME, bare)
