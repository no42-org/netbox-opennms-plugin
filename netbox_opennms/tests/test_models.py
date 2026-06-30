# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the Epic 5 data model: clean() rules, constraints, helpers."""

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
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
    object_ip_pks,
    override_ip_pks,
)


class ProfileAndRuleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.profile = MonitoringProfile.objects.create(name="Network device")

    def test_profile_str(self):
        self.assertEqual(str(self.profile), "Network device")

    def test_detector_preset_fills_class_and_params(self):
        detector = MonitoringDetector(
            profile=self.profile, name="ICMP", preset="icmp"
        )
        detector.clean()
        self.assertTrue(detector.rule_class.endswith("IcmpDetector"))
        self.assertIn("timeout", detector.parameters)

    def test_detector_user_params_win_over_preset_defaults(self):
        detector = MonitoringDetector(
            profile=self.profile,
            name="ICMP",
            preset="icmp",
            parameters={"timeout": "9000"},
        )
        detector.clean()
        self.assertEqual(detector.parameters["timeout"], "9000")

    def test_detector_save_persists_preset_class(self):
        # save() resolves the preset (not just clean()), so the API/bulk paths
        # that skip clean() still persist a non-empty rule_class.
        detector = MonitoringDetector.objects.create(
            profile=self.profile, name="ICMP", preset="icmp"
        )
        detector.refresh_from_db()
        self.assertTrue(detector.rule_class.endswith("IcmpDetector"))
        self.assertIn("timeout", detector.parameters)

    def test_detector_without_preset_or_class_is_invalid(self):
        detector = MonitoringDetector(profile=self.profile, name="x")
        with self.assertRaises(ValidationError):
            detector.clean()

    def test_policy_preset_fills_class(self):
        policy = MonitoringPolicy(
            profile=self.profile,
            name="cat",
            preset="set-category",
            parameters={"category": "Routers"},
        )
        policy.clean()
        self.assertTrue(policy.rule_class.endswith("NodeCategorySettingPolicy"))

    def test_tcp_preset_requires_port(self):
        # TcpDetector has no default port — clean() rejects the bare preset.
        bad = MonitoringDetector(profile=self.profile, name="tcp", preset="tcp")
        with self.assertRaises(ValidationError):
            bad.clean()
        ok = MonitoringDetector(
            profile=self.profile,
            name="tcp2",
            preset="tcp",
            parameters={"port": "8080"},
        )
        ok.clean()  # with the required port — valid

    def test_set_category_preset_requires_category(self):
        bad = MonitoringPolicy(
            profile=self.profile, name="cat", preset="set-category"
        )
        with self.assertRaises(ValidationError):
            bad.clean()
        ok = MonitoringPolicy(
            profile=self.profile,
            name="cat2",
            preset="set-category",
            parameters={"category": "Routers"},
        )
        ok.clean()

    def test_detector_unique_per_profile_name(self):
        MonitoringDetector.objects.create(
            profile=self.profile, name="ICMP", rule_class="X"
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MonitoringDetector.objects.create(
                profile=self.profile, name="ICMP", rule_class="Y"
            )


class AssignmentTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.profile = MonitoringProfile.objects.create(name="P")
        cls.site = Site.objects.create(name="Raleigh", slug="raleigh")
        cls.role = DeviceRole.objects.create(name="Router", slug="router")

    def test_str(self):
        assignment = MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.role
        )
        self.assertEqual(str(assignment), "P → Raleigh / Router")

    def test_invalid_location_rejected(self):
        assignment = MonitoringAssignment(
            profile=self.profile, site=self.site, location="bad location"
        )
        with self.assertRaises(ValidationError):
            assignment.clean()

    def test_site_role_unique(self):
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=self.role
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MonitoringAssignment.objects.create(
                profile=self.profile, site=self.site, role=self.role
            )

    def test_site_level_unique_nulls_not_distinct(self):
        # Two site-level (role NULL) assignments for the same site collide.
        MonitoringAssignment.objects.create(
            profile=self.profile, site=self.site, role=None
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MonitoringAssignment.objects.create(
                profile=self.profile, site=self.site, role=None
            )


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

    def test_service_must_be_on_override_ip(self):
        override = MonitoringOverride.objects.create(
            assigned_object=self.device, management_ip=self.ip
        )
        bad = MonitoredService(override=override, ip_address=self.other_ip, name="ICMP")
        with self.assertRaises(ValidationError):
            bad.clean()
        ok = MonitoredService(override=override, ip_address=self.ip, name="ICMP")
        ok.clean()  # the override's management IP — valid

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
