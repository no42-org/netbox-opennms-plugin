# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the pure requisition/foreign-source XML renderers (AD-3)."""

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.test import TestCase
from ipam.models import IPAddress
from lxml import etree
from virtualization.models import Cluster, ClusterType, VirtualMachine

from netbox_opennms.derivation import foreign_id_for
from netbox_opennms.models import MonitoredService, MonitoringProfile
from netbox_opennms.translation import (
    RenderError,
    render_foreign_source_definition,
    render_requisition,
)

MODEL_IMPORT_NS = "http://xmlns.opennms.org/xsd/config/model-import"
FOREIGN_SOURCE_NS = "http://xmlns.opennms.org/xsd/config/foreign-source"


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


class RenderRequisitionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        role = DeviceRole.objects.create(name="Router", slug="router")
        manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model 1", slug="model-1"
        )
        cls.device = Device.objects.create(
            name="rtr-1", device_type=device_type, role=role, site=site
        )
        ip = IPAddress.objects.create(address="10.0.0.1/24")
        cls.profile = MonitoringProfile.objects.create(
            assigned_object=cls.device, management_ip=ip
        )

        cluster = Cluster.objects.create(
            name="c1", type=ClusterType.objects.create(name="t1", slug="t1")
        )
        cls.vm = VirtualMachine.objects.create(name="vm-1", cluster=cluster, role=role)
        vm_ip = IPAddress.objects.create(address="10.0.0.2/24")
        cls.vm_profile = MonitoringProfile.objects.create(
            assigned_object=cls.vm, management_ip=vm_ip
        )

    def test_single_device_requisition(self):
        xml = render_requisition("netbox:raleigh:router", [self.profile])
        root = etree.fromstring(xml)
        self.assertEqual(root.tag, _q(MODEL_IMPORT_NS, "model-import"))
        self.assertEqual(root.get("foreign-source"), "netbox:raleigh:router")
        nodes = root.findall(_q(MODEL_IMPORT_NS, "node"))
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].get("node-label"), "rtr-1")
        self.assertEqual(nodes[0].get("foreign-id"), f"device-{self.device.pk}")
        iface = nodes[0].find(_q(MODEL_IMPORT_NS, "interface"))
        # IP only — no CIDR mask
        self.assertEqual(iface.get("ip-addr"), "10.0.0.1")
        self.assertEqual(iface.get("snmp-primary"), "P")

    def _interfaces(self, xml):
        root = etree.fromstring(xml)
        node = root.find(_q(MODEL_IMPORT_NS, "node"))
        return node.findall(_q(MODEL_IMPORT_NS, "interface"))

    def test_additional_ips_render_as_secondary_interfaces(self):
        self.profile.additional_ips.set(
            [
                IPAddress.objects.create(address="10.0.0.5/24"),
                IPAddress.objects.create(address="10.0.0.6/24"),
            ]
        )
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        primary = [i for i in ifaces if i.get("snmp-primary") == "P"]
        secondary = [i for i in ifaces if i.get("snmp-primary") == "N"]
        self.assertEqual([i.get("ip-addr") for i in primary], ["10.0.0.1"])
        self.assertEqual(
            {i.get("ip-addr") for i in secondary}, {"10.0.0.5", "10.0.0.6"}
        )

    def test_management_ip_never_renders_as_additional(self):
        # AD-15: the management IP added to additional_ips must not become an "N".
        self.profile.additional_ips.set([self.profile.management_ip])
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        self.assertEqual(len(ifaces), 1)
        self.assertEqual(ifaces[0].get("snmp-primary"), "P")

    def test_additional_ips_deduped_by_address(self):
        self.profile.additional_ips.set(
            [
                IPAddress.objects.create(address="10.0.0.5/24"),
                IPAddress.objects.create(address="10.0.0.5/24"),
            ]
        )
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        secondary = [i for i in ifaces if i.get("snmp-primary") == "N"]
        self.assertEqual([i.get("ip-addr") for i in secondary], ["10.0.0.5"])

    def test_no_additional_ips_single_primary(self):
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        self.assertEqual(len(ifaces), 1)
        self.assertEqual(ifaces[0].get("snmp-primary"), "P")

    def _services(self, interface):
        return [
            s.get("service-name")
            for s in interface.findall(_q(MODEL_IMPORT_NS, "monitored-service"))
        ]

    def test_services_render_under_correct_interface_sorted(self):
        extra_ip = IPAddress.objects.create(address="10.0.0.5/24")
        self.profile.additional_ips.set([extra_ip])
        # created out of order to prove deterministic sorting
        MonitoredService.objects.create(
            profile=self.profile, ip_address=self.profile.management_ip, name="SNMP"
        )
        MonitoredService.objects.create(
            profile=self.profile, ip_address=self.profile.management_ip, name="ICMP"
        )
        MonitoredService.objects.create(
            profile=self.profile, ip_address=extra_ip, name="HTTP"
        )
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        primary = next(i for i in ifaces if i.get("snmp-primary") == "P")
        secondary = next(i for i in ifaces if i.get("snmp-primary") == "N")
        self.assertEqual(self._services(primary), ["ICMP", "SNMP"])
        self.assertEqual(self._services(secondary), ["HTTP"])

    def test_no_services_bare_interface(self):
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        self.assertEqual(self._services(ifaces[0]), [])

    def test_duplicate_address_merges_services_onto_one_interface(self):
        # A second IPAddress with the management IP's address (re-listed as
        # additional) must merge onto the single interface, carrying its
        # services — not emit a second interface or drop the services.
        dup = IPAddress.objects.create(address="10.0.0.1/24")
        self.profile.additional_ips.set([dup])
        MonitoredService.objects.create(
            profile=self.profile, ip_address=dup, name="HTTP"
        )
        ifaces = self._interfaces(render_requisition("netbox:x:y", [self.profile]))
        self.assertEqual(len(ifaces), 1)
        self.assertEqual(ifaces[0].get("snmp-primary"), "P")
        self.assertEqual(self._services(ifaces[0]), ["HTTP"])

    def test_location_from_profile(self):
        self.profile.location = "RDU.1-edge"
        root = etree.fromstring(render_requisition("netbox:x:y", [self.profile]))
        node = root.find(_q(MODEL_IMPORT_NS, "node"))
        self.assertEqual(node.get("location"), "RDU.1-edge")

    def test_location_falls_back_to_default(self):
        root = etree.fromstring(
            render_requisition("netbox:x:y", [self.profile], default_location="HQ")
        )
        node = root.find(_q(MODEL_IMPORT_NS, "node"))
        self.assertEqual(node.get("location"), "HQ")

    def test_no_location_attribute_when_unset(self):
        root = etree.fromstring(render_requisition("netbox:x:y", [self.profile]))
        node = root.find(_q(MODEL_IMPORT_NS, "node"))
        self.assertIsNone(node.get("location"))

    def test_profile_location_overrides_default(self):
        self.profile.location = "PROFILE"
        root = etree.fromstring(
            render_requisition("netbox:x:y", [self.profile], default_location="DEFAULT")
        )
        node = root.find(_q(MODEL_IMPORT_NS, "node"))
        self.assertEqual(node.get("location"), "PROFILE")

    def test_date_stamp_optional(self):
        without = etree.fromstring(render_requisition("netbox:x:y", [self.profile]))
        self.assertIsNone(without.get("date-stamp"))
        withd = etree.fromstring(
            render_requisition(
                "netbox:x:y", [self.profile], date_stamp="2026-06-26T10:00:00"
            )
        )
        self.assertEqual(withd.get("date-stamp"), "2026-06-26T10:00:00")

    def test_device_and_vm_no_foreign_id_collision(self):
        xml = render_requisition(
            "netbox:raleigh:router", [self.profile, self.vm_profile]
        )
        root = etree.fromstring(xml)
        fids = {n.get("foreign-id") for n in root.findall(_q(MODEL_IMPORT_NS, "node"))}
        self.assertEqual(len(fids), 2)
        self.assertIn(f"device-{self.device.pk}", fids)
        self.assertIn(f"vm-{self.vm.pk}", fids)

    def test_foreign_id_distinct_when_pks_equal(self):
        # AD-8: Device and VM PKs come from separate sequences, so a Device and a
        # VM can legitimately share a PK. The type prefix — not the PK — is what
        # keeps their node identity distinct. Force the exact collision case.
        self.vm.pk = self.device.pk
        self.assertEqual(self.device.pk, self.vm.pk)
        self.assertNotEqual(foreign_id_for(self.device), foreign_id_for(self.vm))
        self.assertEqual(foreign_id_for(self.device), f"device-{self.device.pk}")
        self.assertEqual(foreign_id_for(self.vm), f"vm-{self.vm.pk}")

    def test_foreign_id_type_qualified(self):
        self.assertEqual(foreign_id_for(self.device), f"device-{self.device.pk}")
        self.assertEqual(foreign_id_for(self.vm), f"vm-{self.vm.pk}")
        with self.assertRaises(TypeError):
            foreign_id_for(object())

    def test_missing_management_ip_raises_render_error(self):
        self.profile.management_ip = None
        with self.assertRaises(RenderError):
            render_requisition("netbox:x:y", [self.profile])

    def test_unnamed_target_raises_render_error(self):
        # pin the GFK to the device instance we mutate (setUpTestData isolation
        # hands each test its own deepcopy, so the cached target is a different
        # object than self.device unless we reassign it here)
        self.profile.assigned_object = self.device
        self.device.name = None
        with self.assertRaises(RenderError):
            render_requisition("netbox:x:y", [self.profile])

    def test_missing_assigned_object_raises_render_error(self):
        self.profile.assigned_object = None
        with self.assertRaises(RenderError):
            render_requisition("netbox:x:y", [self.profile])

    def test_non_device_vm_target_raises_render_error(self):
        # limit_choices_to is form-only, so a profile can point at a non-Device/VM
        # via ORM/REST/import. The renderer must fail cleanly with RenderError, not
        # leak the TypeError from foreign_id_for (which the 1.7 sync won't catch).
        self.profile.assigned_object = Site.objects.create(name="Other", slug="other")
        with self.assertRaises(RenderError):
            render_requisition("netbox:x:y", [self.profile])


class RenderForeignSourceDefinitionTest(TestCase):
    def test_auto_detection_disabled(self):
        xml = render_foreign_source_definition("netbox:raleigh:router")
        root = etree.fromstring(xml)
        self.assertEqual(root.tag, _q(FOREIGN_SOURCE_NS, "foreign-source"))
        self.assertEqual(root.get("name"), "netbox:raleigh:router")
        scan = root.find(_q(FOREIGN_SOURCE_NS, "scan-interval"))
        # explicit unit — a bare "0" can fail OpenNMS's duration parser
        self.assertEqual(scan.text, "0s")
        detectors = root.find(_q(FOREIGN_SOURCE_NS, "detectors"))
        self.assertEqual(len(detectors), 0)
