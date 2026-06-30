# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API tests for the Epic 5 models."""

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
    MonitoredService,
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
)

DETECTOR_CLASS = "org.opennms.netmgt.provision.detector.icmp.IcmpDetector"
POLICY_CLASS = (
    "org.opennms.netmgt.provision.persist.policies.NodeCategorySettingPolicy"
)


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


class MonitoringProfileAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringProfile
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        for name in ("P1", "P2", "P3"):
            MonitoringProfile.objects.create(name=name)
        cls.create_data = [
            {"name": "P4", "scan_interval": "1d"},
            {"name": "P5", "scan_interval": "30m"},
            {"name": "P6"},
        ]


class MonitoringDetectorAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringDetector
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        profile = MonitoringProfile.objects.create(name="P")
        for name in ("d1", "d2", "d3"):
            MonitoringDetector.objects.create(
                profile=profile, name=name, rule_class=DETECTOR_CLASS
            )
        cls.create_data = [
            {"profile": profile.pk, "name": "d4", "rule_class": DETECTOR_CLASS},
            {"profile": profile.pk, "name": "d5", "rule_class": DETECTOR_CLASS},
            {"profile": profile.pk, "name": "d6", "rule_class": DETECTOR_CLASS},
        ]


class MonitoringPolicyAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringPolicy
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        profile = MonitoringProfile.objects.create(name="P")
        for name in ("p1", "p2", "p3"):
            MonitoringPolicy.objects.create(
                profile=profile, name=name, rule_class=POLICY_CLASS
            )
        cls.create_data = [
            {"profile": profile.pk, "name": "p4", "rule_class": POLICY_CLASS},
            {"profile": profile.pk, "name": "p5", "rule_class": POLICY_CLASS},
            {"profile": profile.pk, "name": "p6", "rule_class": POLICY_CLASS},
        ]


class MonitoringAssignmentAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringAssignment
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "profile", "role", "site", "url"]

    @classmethod
    def setUpTestData(cls):
        profile = MonitoringProfile.objects.create(name="P")
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        roles = [
            DeviceRole.objects.create(name=f"Role {i}", slug=f"role-{i}")
            for i in range(6)
        ]
        for role in roles[:3]:
            MonitoringAssignment.objects.create(
                profile=profile, site=site, role=role
            )
        cls.create_data = [
            {"profile": profile.pk, "site": site.pk, "role": roles[3].pk},
            {"profile": profile.pk, "site": site.pk, "role": roles[4].pk},
            {"profile": profile.pk, "site": site.pk, "role": roles[5].pk},
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
        override.additional_ips.set(ips[1:])
        for ip, name in [(ips[0], "ICMP"), (ips[0], "SNMP"), (ips[1], "HTTP")]:
            MonitoredService.objects.create(override=override, ip_address=ip, name=name)
        cls.create_data = [
            {"override": override.pk, "ip_address": ips[2].pk, "name": "ICMP"},
            {"override": override.pk, "ip_address": ips[3].pk, "name": "SNMP"},
            {"override": override.pk, "ip_address": ips[4].pk, "name": "HTTP"},
        ]
