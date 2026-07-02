# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the data model: clean() rules, constraints, helpers."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from ipam.models import IPAddress

from netbox_opennms.models import (
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
    object_ip_pks,
    override_ip_pks,
)
from netbox_opennms.presets import resolve_policy

FILTER = {"site": ["raleigh"], "role": ["router"]}


class RequisitionAndRuleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.req = Requisition.objects.create(
            name="netbox.raleigh.router", filter_params=FILTER
        )

    def test_requisition_str(self):
        self.assertEqual(str(self.req), "netbox.raleigh.router")

    def test_detector_preset_fills_class_and_params(self):
        detector = MonitoringDetector(requisition=self.req, name="ICMP", preset="icmp")
        detector.clean()
        self.assertTrue(detector.rule_class.endswith("IcmpDetector"))
        self.assertIn("timeout", detector.parameters)

    def test_detector_user_params_win_over_preset_defaults(self):
        detector = MonitoringDetector(
            requisition=self.req, name="ICMP", preset="icmp",
            parameters={"timeout": "9000"},
        )
        detector.clean()
        self.assertEqual(detector.parameters["timeout"], "9000")

    def test_detector_save_persists_preset_class(self):
        detector = MonitoringDetector.objects.create(
            requisition=self.req, name="ICMP", preset="icmp"
        )
        detector.refresh_from_db()
        self.assertTrue(detector.rule_class.endswith("IcmpDetector"))
        self.assertIn("timeout", detector.parameters)

    def test_detector_without_preset_or_class_is_invalid(self):
        detector = MonitoringDetector(requisition=self.req, name="x")
        with self.assertRaises(ValidationError):
            detector.clean()

    def test_policy_preset_fills_class(self):
        policy = MonitoringPolicy(
            requisition=self.req, name="cat", preset="set-node-category",
            parameters={"category": "Routers"},
        )
        policy.clean()
        self.assertTrue(policy.rule_class.endswith("NodeCategorySettingPolicy"))

    def test_preset_owns_rule_class(self):
        # A preset always (re)derives the class — a user-supplied rule_class can't
        # override it (hard association).
        detector = MonitoringDetector(
            requisition=self.req, name="ICMP", preset="icmp",
            rule_class="org.example.NotThis",
        )
        detector.clean()
        self.assertTrue(detector.rule_class.endswith("IcmpDetector"))

    def test_unknown_preset_does_not_blank_existing_class(self):
        # An admin-extended preset with no registry entry must not wipe the class
        # (review #1) — an existing freeform class is preserved.
        detector = MonitoringDetector(
            requisition=self.req, name="x", preset="not-a-registered-preset",
            rule_class="org.example.Custom",
        )
        detector.clean()
        self.assertEqual(detector.rule_class, "org.example.Custom")

    def test_preset_default_not_resurrected_after_deletion(self):
        # Deleting a seeded default and saving must not re-add it (review #4).
        detector = MonitoringDetector.objects.create(
            requisition=self.req, name="i", preset="icmp"
        )
        self.assertIn("retries", detector.parameters)
        detector.parameters = {"timeout": "2000"}
        detector.save()
        detector.refresh_from_db()
        self.assertNotIn("retries", detector.parameters)

    def test_all_policy_presets_resolve_to_a_class(self):
        for preset, suffix in (
            ("match-ip-interface", "MatchingIpInterfacePolicy"),
            ("match-snmp-interface", "MatchingSnmpInterfacePolicy"),
            ("script-policy", "ScriptPolicy"),
            ("set-interface-metadata", "InterfaceMetadataSettingPolicy"),
            ("set-node-category", "NodeCategorySettingPolicy"),
            ("set-node-metadata", "NodeMetadataSettingPolicy"),
        ):
            cls, _params = resolve_policy(preset)
            self.assertTrue(cls.endswith(suffix), f"{preset} → {cls}")

    def test_tcp_preset_requires_port(self):
        bad = MonitoringDetector(requisition=self.req, name="tcp", preset="tcp")
        with self.assertRaises(ValidationError):
            bad.clean()
        ok = MonitoringDetector(
            requisition=self.req, name="tcp2", preset="tcp",
            parameters={"port": "8080"},
        )
        ok.clean()

    def test_set_category_preset_requires_category(self):
        bad = MonitoringPolicy(
            requisition=self.req, name="cat", preset="set-node-category"
        )
        with self.assertRaises(ValidationError):
            bad.clean()

    def test_detector_unique_per_requisition_name(self):
        MonitoringDetector.objects.create(
            requisition=self.req, name="ICMP", rule_class="X"
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MonitoringDetector.objects.create(
                requisition=self.req, name="ICMP", rule_class="Y"
            )


class RequisitionModelTest(TestCase):
    def test_url_unsafe_name_rejected(self):
        req = Requisition(name="bad name", filter_params={"site": ["x"]})
        with self.assertRaises(ValidationError):
            req.clean()

    def test_invalid_service_name_rejected(self):
        req = Requisition(name="x", filter_params=FILTER, services=["BOGUS"])
        with self.assertRaises(ValidationError):
            req.clean()


class OverrideAndServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Raleigh", slug="raleigh")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        cls.device = Device.objects.create(
            name="rtr-1", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(device=cls.device, name="eth0", type="virtual")
        cls.ip = IPAddress.objects.create(address="10.0.0.1/24", assigned_object=iface)
        cls.other_ip = IPAddress.objects.create(address="10.9.9.9/24")

    def test_object_ip_pks(self):
        self.assertEqual(object_ip_pks(self.device), {self.ip.pk})

    def test_override_str_and_ip_pks(self):
        override = MonitoringOverride.objects.create(
            assigned_object=self.device, management_ip=self.ip
        )
        self.assertEqual(str(override), "Override: rtr-1")
        self.assertEqual(override_ip_pks(override), {self.ip.pk})

    def test_override_unique_per_object(self):
        MonitoringOverride.objects.create(assigned_object=self.device)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MonitoringOverride.objects.create(assigned_object=self.device)

    def test_override_invalid_location(self):
        override = MonitoringOverride(assigned_object=self.device, location="bad name")
        with self.assertRaises(ValidationError):
            override.clean()

    def test_override_invalid_suppressed_service(self):
        override = MonitoringOverride(
            assigned_object=self.device, suppressed_services=["BOGUS"]
        )
        with self.assertRaises(ValidationError):
            override.clean()

    def test_service_must_be_on_override_ip(self):
        override = MonitoringOverride.objects.create(
            assigned_object=self.device, management_ip=self.ip
        )
        bad = MonitoredService(override=override, ip_address=self.other_ip, name="ICMP")
        with self.assertRaises(ValidationError):
            bad.clean()
        ok = MonitoredService(override=override, ip_address=self.ip, name="ICMP")
        ok.clean()

    def test_service_unique(self):
        override = MonitoringOverride.objects.create(
            assigned_object=self.device, management_ip=self.ip
        )
        MonitoredService.objects.create(
            override=override, ip_address=self.ip, name="ICMP"
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MonitoredService.objects.create(
                override=override, ip_address=self.ip, name="ICMP"
            )
