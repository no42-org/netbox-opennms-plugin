# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for asset/metadata enrichment (RD-2/RD-3): resolver, models, resolution."""

from types import SimpleNamespace

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from ipam.models import IPAddress

from netbox_opennms.enrichment import resolve_source
from netbox_opennms.membership import resolve_node
from netbox_opennms.models import AssetMapping, MetadataEntry, Requisition


class ResolveSourceTest(SimpleTestCase):
    def _obj(self, **kw):
        kw.setdefault("custom_field_data", {})
        return SimpleNamespace(**kw)

    def test_curated_attribute(self):
        self.assertEqual(resolve_source(self._obj(serial="SN-9"), "serial"), "SN-9")

    def test_related_name(self):
        obj = self._obj(site=SimpleNamespace(name="Raleigh"))
        self.assertEqual(resolve_source(obj, "site"), "Raleigh")

    def test_absent_attribute_is_none(self):
        self.assertIsNone(resolve_source(self._obj(), "serial"))

    def test_empty_string_is_none(self):
        self.assertIsNone(resolve_source(self._obj(description=""), "description"))

    def test_custom_field(self):
        obj = self._obj(custom_field_data={"owner": "neteng"})
        self.assertEqual(resolve_source(obj, "cf_owner"), "neteng")

    def test_unknown_source_is_none(self):
        self.assertIsNone(resolve_source(self._obj(), "bogus"))


class AssetMappingValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.req = Requisition.objects.create(
            name="asset-req", filter_params={"role": ["x"]}
        )

    def test_known_asset_field_ok(self):
        AssetMapping(
            requisition=self.req, netbox_source="serial", asset_field="serialNumber"
        ).clean()  # no raise

    def test_unknown_asset_field_rejected(self):
        mapping = AssetMapping(
            requisition=self.req, netbox_source="serial", asset_field="bogusField"
        )
        with self.assertRaises(ValidationError):
            mapping.clean()


class MetadataEntryValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.req = Requisition.objects.create(
            name="meta-req", filter_params={"role": ["x"]}
        )

    def _entry(self, **kw):
        kw.setdefault("requisition", self.req)
        kw.setdefault("scope", "node")
        kw.setdefault("key", "k")
        return MetadataEntry(**kw)

    def test_requisition_context_with_literal_ok(self):
        self._entry(context="requisition", literal_value="v").clean()

    def test_custom_context_must_be_x_prefixed(self):
        with self.assertRaises(ValidationError):
            self._entry(context="custom", literal_value="v").clean()

    def test_x_prefixed_context_ok(self):
        self._entry(context="X-netbox", literal_value="v").clean()

    def test_requires_a_value(self):
        with self.assertRaises(ValidationError):
            self._entry(context="requisition").clean()

    def test_value_source_and_literal_are_exclusive(self):
        with self.assertRaises(ValidationError):
            self._entry(
                context="requisition", value_source="name", literal_value="v"
            ).clean()


class EnrichmentResolveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="R", slug="r")
        role = DeviceRole.objects.create(name="Rtr", slug="rtr")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.device = Device.objects.create(
            name="rtr-1", device_type=dt, role=role, site=site, serial="SN-42"
        )
        iface = Interface.objects.create(device=cls.device, name="eth0", type="virtual")
        ip = IPAddress.objects.create(address="10.0.0.1/24", assigned_object=iface)
        cls.device.primary_ip4 = ip
        cls.device.save()
        cls.req = Requisition.objects.create(
            name="enrich", filter_params={"role": ["rtr"]}, services=["ICMP"]
        )
        AssetMapping.objects.create(
            requisition=cls.req, netbox_source="serial", asset_field="serialNumber"
        )
        MetadataEntry.objects.create(
            requisition=cls.req, scope="node", context="requisition",
            key="owner", literal_value="neteng",
        )
        MetadataEntry.objects.create(
            requisition=cls.req, scope="interface", context="X-netbox",
            key="src", value_source="name",
        )

    def test_resolve_node_attaches_enrichment(self):
        node, _ = resolve_node(self.device, self.req, None)
        self.assertIn(("serialNumber", "SN-42"), node.assets)
        self.assertIn(("requisition", "owner", "neteng"), node.node_metadata)
        self.assertIn(("X-netbox", "src", "rtr-1"), node.interface_metadata)

    def test_unresolved_source_is_omitted(self):
        # A mapping whose source doesn't resolve for this member yields no <asset>.
        AssetMapping.objects.create(
            requisition=self.req, netbox_source="asset_tag", asset_field="assetNumber"
        )
        node, _ = resolve_node(self.device, self.req, None)
        self.assertNotIn(
            "assetNumber", [name for name, _ in node.assets]
        )
