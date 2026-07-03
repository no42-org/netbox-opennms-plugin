# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI view (CRUD) tests for the plugin models."""

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
FILTER = {"site": ["site-1"], "role": ["router"]}


def _devices(count):
    site = Site.objects.create(name="Site 1", slug="site-1")
    mfr = Manufacturer.objects.create(name="Acme", slug="acme")
    dt = DeviceType.objects.create(manufacturer=mfr, model="Model 1", slug="model-1")
    role = DeviceRole.objects.create(name="Router", slug="router")
    return [
        Device.objects.create(name=f"device-{i}", device_type=dt, role=role, site=site)
        for i in range(count)
    ]


class RequisitionViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = Requisition
    # JSON / multi-value fields don't round-trip as plain equality in
    # assertInstanceEqual (dict vs. string, list vs. list-of-choices).
    validation_excluded_fields = ("filter_params", "services")

    def _get_base_url(self):
        return "plugins:netbox_opennms:requisition_{}"

    @classmethod
    def setUpTestData(cls):
        for name in ("req-1", "req-2", "req-3"):
            Requisition.objects.create(name=name, filter_params=FILTER)
        cls.form_data = {
            "name": "req-4",
            "object_types": "both",
            "filter_params": '{"site": ["site-1"]}',
            "scan_interval": "1d",
            "default_interfaces": "primary",
            "services": ["ICMP", "SNMP"],
            "location": "",
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
        req = Requisition.objects.create(name="req", filter_params=FILTER)
        for name in ("d1", "d2", "d3"):
            MonitoringDetector.objects.create(
                requisition=req, name=name, rule_class=DETECTOR_CLASS
            )
        cls.form_data = {
            "requisition": req.pk,
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
        req = Requisition.objects.create(name="req", filter_params=FILTER)
        for name in ("p1", "p2", "p3"):
            MonitoringPolicy.objects.create(
                requisition=req, name=name, rule_class=POLICY_CLASS
            )
        cls.form_data = {
            "requisition": req.pk,
            "name": "p4",
            "rule_class": POLICY_CLASS,
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
            "management_role": "P",
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
        for extra_ip in ips[1:]:
            MonitoredInterface.objects.create(override=override, ip_address=extra_ip)
        for ip, name in [(ips[0], "ICMP"), (ips[0], "SNMP"), (ips[1], "HTTP")]:
            MonitoredService.objects.create(override=override, ip_address=ip, name=name)
        cls.form_data = {
            "override": override.pk,
            "ip_address": ips[2].pk,
            "name": "DNS",
        }


class MonitoredInterfaceViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoredInterface

    def _get_base_url(self):
        return "plugins:netbox_opennms:monitoredinterface_{}"

    @classmethod
    def setUpTestData(cls):
        device = _devices(1)[0]
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        ips = [
            IPAddress.objects.create(address=f"10.9.0.{i}/24", assigned_object=iface)
            for i in range(1, 7)
        ]
        override = MonitoringOverride.objects.create(
            assigned_object=device, management_ip=ips[0]
        )
        for extra_ip in ips[1:4]:
            MonitoredInterface.objects.create(override=override, ip_address=extra_ip)
        cls.form_data = {
            "override": override.pk,
            "ip_address": ips[4].pk,
            "role": "N",
        }
