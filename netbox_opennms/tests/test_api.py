# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API tests for the plugin models."""

import unittest

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from ipam.models import IPAddress
from utilities.testing import APIViewTestCases

from netbox_opennms.models import (
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)

DETECTOR_CLASS = "org.opennms.netmgt.provision.detector.icmp.IcmpDetector"
POLICY_CLASS = (
    "org.opennms.netmgt.provision.persist.policies.NodeCategorySettingPolicy"
)
FILTER = {"site": ["raleigh"], "role": ["router"]}


class _NoGraphQL:
    """Mixin: GraphQL is out of scope (no schema)."""

    graphql_auto_filter_required = False

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_get_object(self):
        pass

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_list_objects(self):
        pass

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_filter_objects(self):
        pass


def _devices(count):
    site = Site.objects.create(name="Site 1", slug="site-1")
    mfr = Manufacturer.objects.create(name="Acme", slug="acme")
    dt = DeviceType.objects.create(manufacturer=mfr, model="Model 1", slug="model-1")
    role = DeviceRole.objects.create(name="Router", slug="router")
    return [
        Device.objects.create(name=f"device-{i}", device_type=dt, role=role, site=site)
        for i in range(count)
    ]


class RequisitionAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = Requisition
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        for name in ("r1", "r2", "r3"):
            Requisition.objects.create(name=name, filter_params=FILTER)
        cls.create_data = [
            {"name": "r4", "object_types": "both", "filter_params": {"site": ["rdu"]}},
            {"name": "r5", "filter_params": {"role": ["router"]}},
            {"name": "r6", "filter_params": {"site": ["raleigh"]}},
        ]


class MonitoringDetectorAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringDetector
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        req = Requisition.objects.create(name="req", filter_params=FILTER)
        for name in ("d1", "d2", "d3"):
            MonitoringDetector.objects.create(
                requisition=req, name=name, rule_class=DETECTOR_CLASS
            )
        cls.create_data = [
            {"requisition": req.pk, "name": "d4", "rule_class": DETECTOR_CLASS},
            {"requisition": req.pk, "name": "d5", "rule_class": DETECTOR_CLASS},
            {"requisition": req.pk, "name": "d6", "rule_class": DETECTOR_CLASS},
        ]


class MonitoringPolicyAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringPolicy
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        req = Requisition.objects.create(name="req", filter_params=FILTER)
        for name in ("p1", "p2", "p3"):
            MonitoringPolicy.objects.create(
                requisition=req, name=name, rule_class=POLICY_CLASS
            )
        cls.create_data = [
            {"requisition": req.pk, "name": "p4", "rule_class": POLICY_CLASS},
            {"requisition": req.pk, "name": "p5", "rule_class": POLICY_CLASS},
            {"requisition": req.pk, "name": "p6", "rule_class": POLICY_CLASS},
        ]


class MonitoringOverrideAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringOverride
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "exclude", "id", "url"]

    @classmethod
    def setUpTestData(cls):
        devices = _devices(6)
        for device in devices[:3]:
            MonitoringOverride.objects.create(assigned_object=device)
        cls.create_data = [
            {"assigned_object_type": "dcim.device", "assigned_object_id": d.pk}
            for d in devices[3:6]
        ]


class MonitoredServiceAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoredService
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        device = _devices(1)[0]
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        ips = [
            IPAddress.objects.create(address=f"10.0.0.{i}/24", assigned_object=iface)
            for i in range(1, 7)
        ]
        override = MonitoringOverride.objects.create(
            assigned_object=device, management_ip=ips[0]
        )
        for extra_ip in ips[1:]:
            MonitoredInterface.objects.create(override=override, ip_address=extra_ip)
        for ip, name in [(ips[0], "ICMP"), (ips[0], "SNMP"), (ips[1], "HTTP")]:
            MonitoredService.objects.create(override=override, ip_address=ip, name=name)
        cls.create_data = [
            {"override": override.pk, "ip_address": ips[2].pk, "name": "ICMP"},
            {"override": override.pk, "ip_address": ips[3].pk, "name": "SNMP"},
            {"override": override.pk, "ip_address": ips[4].pk, "name": "HTTP"},
        ]


class MonitoredInterfaceAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoredInterface
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "role", "url"]

    @classmethod
    def setUpTestData(cls):
        device = _devices(1)[0]
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        ips = [
            IPAddress.objects.create(address=f"10.9.0.{i}/24", assigned_object=iface)
            for i in range(1, 8)
        ]
        override = MonitoringOverride.objects.create(
            assigned_object=device, management_ip=ips[0]
        )
        for extra_ip in ips[1:4]:
            MonitoredInterface.objects.create(override=override, ip_address=extra_ip)
        cls.create_data = [
            {"override": override.pk, "ip_address": ips[4].pk, "role": "N"},
            {"override": override.pk, "ip_address": ips[5].pk, "role": "S"},
            {"override": override.pk, "ip_address": ips[6].pk, "role": "N"},
        ]


class AssetMappingAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = AssetMapping
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["asset_field", "display", "id", "url"]

    @classmethod
    def setUpTestData(cls):
        req = Requisition.objects.create(
            name="am-api", filter_params={"role": ["switch"]}
        )
        for source, field in [
            ("serial", "serialNumber"),
            ("name", "displayCategory"),
            ("description", "description"),
        ]:
            AssetMapping.objects.create(
                requisition=req, netbox_source=source, asset_field=field
            )
        cls.create_data = [
            {"requisition": req.pk, "netbox_source": "role", "asset_field": "category"},
            {"requisition": req.pk, "netbox_source": "site", "asset_field": "building"},
            {"requisition": req.pk, "netbox_source": "rack", "asset_field": "rack"},
        ]


class MetadataEntryAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MetadataEntry
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "key", "url"]

    @classmethod
    def setUpTestData(cls):
        req = Requisition.objects.create(
            name="me-api", filter_params={"role": ["switch"]}
        )
        for key in ["k1", "k2", "k3"]:
            MetadataEntry.objects.create(
                requisition=req, scope="node", context="requisition",
                key=key, literal_value="v",
            )
        cls.create_data = [
            {"requisition": req.pk, "scope": "node", "context": "requisition",
             "key": "a", "literal_value": "1"},
            {"requisition": req.pk, "scope": "node", "context": "requisition",
             "key": "b", "literal_value": "2"},
            {"requisition": req.pk, "scope": "service", "context": "X-netbox",
             "key": "c", "value_source": "name"},
        ]
