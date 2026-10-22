# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the data model: clean() rules, constraints, helpers."""

from unittest import mock

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from ipam.models import IPAddress

from netbox_opennms.choices import ServerOriginChoices
from netbox_opennms.crypto import Undecryptable
from netbox_opennms.models import (
    DeployedForeignSource,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    OpenNMSServer,
    Requisition,
    object_ip_pks,
    override_ip_pks,
)
from netbox_opennms.presets import resolve_policy
from netbox_opennms.tests.servers import make_server

FILTER = {"site": ["raleigh"], "role": ["router"]}


class RequisitionAndRuleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.req = Requisition.objects.create(
            server=make_server(),
            name="netbox.raleigh.router",
            filter_params=FILTER,
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


class OpenNMSServerModelTest(TestCase):
    def test_password_is_ciphertext_in_database(self):
        server = make_server(password="hunter2")
        with connection.cursor() as cur:
            cur.execute(
                "SELECT password, headers FROM netbox_opennms_opennmsserver "
                "WHERE id = %s",
                [server.pk],
            )
            password, headers = cur.fetchone()
        self.assertNotIn("hunter2", password)
        self.assertEqual(OpenNMSServer.objects.get(pk=server.pk).password, "hunter2")

    def test_changelog_excludes_secrets(self):
        server = make_server(password="hunter2", headers={"X-Token": "t"})
        data = server.serialize_object()
        self.assertNotIn("password", data)
        self.assertNotIn("headers", data)

    def test_url_scheme_required(self):
        with self.assertRaises(ValidationError) as ctx:
            OpenNMSServer(
                name="x", url="onms.example.org", username="u", password="p"
            ).full_clean()
        self.assertIn("url", ctx.exception.message_dict)

    def test_headers_must_be_string_mapping(self):
        for bad in (["a"], {"X": 1}):
            with self.assertRaises(ValidationError) as ctx:
                OpenNMSServer(
                    name="x", url="https://a/", username="u", password="p",
                    headers=bad,
                ).full_clean()
            self.assertIn("headers", ctx.exception.message_dict)

    def test_local_requires_credentials_on_create(self):
        with self.assertRaises(ValidationError) as ctx:
            OpenNMSServer(name="x", url="https://a/").full_clean()
        self.assertIn("username", ctx.exception.message_dict)
        self.assertIn("password", ctx.exception.message_dict)

    def _errors(self, server):
        try:
            server.full_clean()
        except ValidationError as exc:
            return exc.message_dict
        return {}

    def test_url_of_another_local_row_is_rejected(self):
        # Stored by a path that skips clean(), so the upper-case scheme stays.
        make_server("a", url="HTTPS://onms.example.org/opennms")
        for url in (
            "https://onms.example.org/opennms",
            "https://onms.example.org/opennms/",
            "https://ONMS.Example.org/opennms",
        ):
            server = OpenNMSServer(name="b", url=url, username="u", password="p")
            self.assertEqual(
                self._errors(server)["url"],
                ["Another server (a) already points at this OpenNMS instance."],
            )

    def test_effective_url_of_provisioned_row_is_rejected(self):
        # The stored url is stale; the config entry decides where it points.
        OpenNMSServer.objects.create(
            name="prod", origin=ServerOriginChoices.PROVISIONED, url="https://old/"
        )
        entry = {"url": "https://p.example.org/opennms", "username": "u"}
        with mock.patch(
            "netbox_opennms.models.config.server_entry",
            side_effect=lambda name: entry if name == "prod" else None,
        ):
            clash = OpenNMSServer(
                name="b", url="https://p.example.org/opennms/", username="u",
                password="p",
            )
            free = OpenNMSServer(
                name="c", url="https://old/", username="u", password="p"
            )
            self.assertIn("(prod)", self._errors(clash)["url"][0])
            self.assertEqual(self._errors(free), {})

    def test_own_url_is_not_a_clash(self):
        server = OpenNMSServer.objects.get(pk=make_server().pk)
        server.description = "edited"
        self.assertEqual(self._errors(server), {})

    def test_config_entry_is_read_once_per_instance(self):
        server = OpenNMSServer.objects.create(
            name="prod", origin=ServerOriginChoices.PROVISIONED, url="https://p/"
        )
        entry = {"url": "https://p/", "username": "cfg"}
        with mock.patch(
            "netbox_opennms.models.config.server_entry", return_value=entry
        ) as lookup:
            values = (
                server.is_missing,
                server.effective_username,
                server.effective_url,
            )
        self.assertEqual(values, (False, "cfg", "https://p/"))
        lookup.assert_called_once_with("prod")

    def test_untouched_stored_password_is_kept(self):
        server = OpenNMSServer.objects.get(pk=make_server().pk)
        self.assertEqual(self._errors(server), {})

    def test_new_password_on_edit_is_accepted(self):
        server = OpenNMSServer.objects.get(pk=make_server().pk)
        server.password = "new"
        self.assertEqual(self._errors(server), {})

    def test_empty_stored_password_requires_one(self):
        # E.g. a missing provisioned row being adopted as local.
        server = OpenNMSServer.objects.create(
            name="gone", origin=ServerOriginChoices.PROVISIONED, url="https://g/"
        )
        server.origin = ServerOriginChoices.LOCAL
        server.username = "u"
        self.assertIn("password", self._errors(server))
        server.password = "p"
        self.assertEqual(self._errors(server), {})

    def test_undecryptable_password_is_not_a_stored_password(self):
        server = make_server()
        server.password = Undecryptable("gAAAA-broken")
        self.assertFalse(server.credentials_ok)
        self.assertIn("password", self._errors(server))
        server.password = "re-entered"
        self.assertEqual(self._errors(server), {})

    def test_undecryptable_headers_must_be_re_entered(self):
        server = make_server()
        server.headers = Undecryptable("gAAAA-broken")
        self.assertFalse(server.credentials_ok)
        self.assertIn("cannot be decrypted", self._errors(server)["headers"][0])
        server.headers = {}
        self.assertEqual(self._errors(server), {})

    def test_local_requires_username(self):
        server = OpenNMSServer.objects.get(pk=make_server().pk)
        server.username = ""
        self.assertIn("username", self._errors(server))

    def test_provisioned_needs_no_stored_credentials(self):
        server = OpenNMSServer(
            name="prod", origin=ServerOriginChoices.PROVISIONED, url="https://p/"
        )
        self.assertEqual(self._errors(server), {})

    def test_is_missing_for_provisioned_without_config(self):
        server = OpenNMSServer.objects.create(
            name="ghost", origin=ServerOriginChoices.PROVISIONED, url="https://g/"
        )
        with mock.patch("netbox_opennms.models.config.server_entry", return_value=None):
            self.assertTrue(server.is_missing)

    def test_requisition_requires_server(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Requisition.objects.create(name="r", filter_params={"site": ["x"]})

    def test_deployed_foreign_source_unique_per_server(self):
        a, b = make_server("a"), make_server("b")
        DeployedForeignSource.objects.create(server=a, name="fs")
        DeployedForeignSource.objects.create(server=b, name="fs")  # no error
        with transaction.atomic(), self.assertRaises(IntegrityError):
            DeployedForeignSource.objects.create(server=a, name="fs")
