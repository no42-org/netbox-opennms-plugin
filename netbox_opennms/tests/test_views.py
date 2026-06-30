# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI view (CRUD) tests for the Epic 5 models."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from ipam.models import IPAddress
from utilities.testing import ViewTestCases

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


def _devices(count):
    site = Site.objects.create(name="Site 1", slug="site-1")
    mfr = Manufacturer.objects.create(name="Acme", slug="acme")
    dt = DeviceType.objects.create(manufacturer=mfr, model="Model 1", slug="model-1")
    role = DeviceRole.objects.create(name="Router", slug="router")
    return [
        Device.objects.create(name=f"device-{i}", device_type=dt, role=role, site=site)
        for i in range(count)
    ]


class MonitoringProfileViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoringProfile

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoringprofile_{}"

    @classmethod
    def setUpTestData(cls):
        for name in ("Profile 1", "Profile 2", "Profile 3"):
            MonitoringProfile.objects.create(name=name)
        cls.form_data = {
            "name": "Profile 4",
            "scan_interval": "1d",
            "default_interfaces": "primary",
        }


class MonitoringDetectorViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoringDetector

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoringdetector_{}"

    @classmethod
    def setUpTestData(cls):
        profile = MonitoringProfile.objects.create(name="P")
        for name in ("d1", "d2", "d3"):
            MonitoringDetector.objects.create(
                profile=profile, name=name, rule_class=DETECTOR_CLASS
            )
        cls.form_data = {
            "profile": profile.pk,
            "name": "d4",
            "rule_class": DETECTOR_CLASS,
        }


class MonitoringPolicyViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoringPolicy

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoringpolicy_{}"

    @classmethod
    def setUpTestData(cls):
        profile = MonitoringProfile.objects.create(name="P")
        for name in ("p1", "p2", "p3"):
            MonitoringPolicy.objects.create(
                profile=profile, name=name, rule_class=POLICY_CLASS
            )
        cls.form_data = {
            "profile": profile.pk,
            "name": "p4",
            "rule_class": POLICY_CLASS,
        }


class MonitoringAssignmentViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoringAssignment

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoringassignment_{}"

    @classmethod
    def setUpTestData(cls):
        profile = MonitoringProfile.objects.create(name="P")
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        roles = [
            DeviceRole.objects.create(name=f"Role {i}", slug=f"role-{i}")
            for i in range(4)
        ]
        for role in roles[:3]:
            MonitoringAssignment.objects.create(
                profile=profile, site=site, role=role
            )
        cls.form_data = {
            "profile": profile.pk,
            "site": site.pk,
            "role": roles[3].pk,
            "location": "",
        }


class MonitoringOverrideViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoringOverride

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoringoverride_{}"

    @classmethod
    def setUpTestData(cls):
        devices = _devices(6)
        for device in devices[:3]:
            MonitoringOverride.objects.create(assigned_object=device)
        cls.form_data = {
            "device": devices[3].pk,
            "exclude": True,
            "location": "",
        }


class MonitoredServiceViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoredService

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoredservice_{}"

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
        cls.form_data = {
            "override": override.pk,
            "ip_address": ips[2].pk,
            "name": "DNS",
        }
