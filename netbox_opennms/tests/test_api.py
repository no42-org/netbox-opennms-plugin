# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API tests for MonitoringProfile."""

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

from netbox_opennms.models import MonitoredService, MonitoringProfile


class MonitoringProfileAPITest(APIViewTestCases.APIViewTestCase):
    model = MonitoringProfile
    # Plugin API lives under the ``plugins-api:`` namespace; `-api` is appended.
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "enabled", "id", "url"]
    # GraphQL is out of scope for this story (no schema yet).
    graphql_auto_filter_required = False

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Site 1", slug="site-1")
        manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model 1", slug="model-1"
        )
        role = DeviceRole.objects.create(name="Router", slug="router")
        devices = [
            Device.objects.create(
                name=f"device-{i}",
                device_type=device_type,
                role=role,
                site=site,
            )
            for i in range(6)
        ]

        MonitoringProfile.objects.create(assigned_object=devices[0])
        MonitoringProfile.objects.create(assigned_object=devices[1])
        MonitoringProfile.objects.create(assigned_object=devices[2])

        cls.create_data = [
            {
                "assigned_object_type": "dcim.device",
                "assigned_object_id": devices[3].pk,
                "enabled": True,
            },
            {
                "assigned_object_type": "dcim.device",
                "assigned_object_id": devices[4].pk,
                "enabled": False,
            },
            {
                "assigned_object_type": "dcim.device",
                "assigned_object_id": devices[5].pk,
                "enabled": True,
            },
        ]

    # GraphQL support is out of scope for Story 1.2 (no schema/types yet).
    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_get_object(self):
        pass

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_list_objects(self):
        pass

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_filter_objects(self):
        pass


class MonitoredServiceAPITest(APIViewTestCases.APIViewTestCase):
    model = MonitoredService
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]
    graphql_auto_filter_required = False

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Site 1", slug="site-1")
        manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model 1", slug="model-1"
        )
        role = DeviceRole.objects.create(name="Router", slug="router")
        device = Device.objects.create(
            name="device-1", device_type=device_type, role=role, site=site
        )
        interface = Interface.objects.create(device=device, name="eth0", type="virtual")
        ips = [
            IPAddress.objects.create(
                address=f"10.0.0.{i}/24", assigned_object=interface
            )
            for i in range(1, 7)
        ]
        profile = MonitoringProfile.objects.create(
            assigned_object=device, management_ip=ips[0]
        )
        profile.additional_ips.set(ips[1:])

        MonitoredService.objects.create(profile=profile, ip_address=ips[0], name="ICMP")
        MonitoredService.objects.create(profile=profile, ip_address=ips[0], name="SNMP")
        MonitoredService.objects.create(profile=profile, ip_address=ips[1], name="HTTP")

        cls.create_data = [
            {"profile": profile.pk, "ip_address": ips[2].pk, "name": "ICMP"},
            {"profile": profile.pk, "ip_address": ips[3].pk, "name": "SNMP"},
            {"profile": profile.pk, "ip_address": ips[4].pk, "name": "HTTP"},
        ]

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_get_object(self):
        pass

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_list_objects(self):
        pass

    @unittest.skip("GraphQL not implemented yet (deferred)")
    def test_graphql_filter_objects(self):
        pass
