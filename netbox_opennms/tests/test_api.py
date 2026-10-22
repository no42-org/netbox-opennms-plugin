# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""REST API tests for the plugin models."""

import unittest
from unittest import mock

from core.models import ObjectType
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from extras.models import CustomLink
from ipam.models import IPAddress
from netbox.models.features import get_model_features
from utilities.testing import APITestCase, APIViewTestCases

from netbox_opennms.choices import ServerOriginChoices
from netbox_opennms.filtersets import OpenNMSServerFilterSet
from netbox_opennms.models import (
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    OpenNMSServer,
    Requisition,
)
from netbox_opennms.tests.servers import make_server

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
        cls.server = make_server()
        for name in ("r1", "r2", "r3"):
            Requisition.objects.create(
                server=cls.server, name=name, filter_params=FILTER
            )
        cls.create_data = [
            {
                "name": "r4",
                "server": cls.server.pk,
                "object_types": "both",
                "filter_params": {"site": ["rdu"]},
            },
            {
                "name": "r5",
                "server": cls.server.pk,
                "filter_params": {"role": ["router"]},
            },
            {
                "name": "r6",
                "server": cls.server.pk,
                "filter_params": {"site": ["raleigh"]},
            },
        ]


class MonitoringDetectorAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = MonitoringDetector
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]

    @classmethod
    def setUpTestData(cls):
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server, name="req", filter_params=FILTER
        )
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
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server, name="req", filter_params=FILTER
        )
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
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server,
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
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server,
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


SERVERS = "/api/plugins/opennms/servers/"
REQUISITIONS = "/api/plugins/opennms/requisitions/"


class OpenNMSServerAPITest(_NoGraphQL, APIViewTestCases.APIViewTestCase):
    model = OpenNMSServer
    view_namespace = "plugins-api:netbox_opennms"
    brief_fields = ["display", "id", "name", "url"]
    # Write-only, so the round-trip comparison cannot read them back.
    validation_excluded_fields = ["password", "headers"]
    create_data = [
        {"name": f"n{i}", "address": f"https://{i}/", "username": "u", "password": "p"}
        for i in (4, 5, 6)
    ]
    bulk_update_data = {"description": "bulk"}

    @classmethod
    def setUpTestData(cls):
        # Distinct URLs: two rows on one OpenNMS fail clean() (bulk update).
        for name in ("s1", "s2", "s3"):
            make_server(name, url=f"https://{name}.example.org/opennms")


class _SuperuserAPITestCase(APITestCase):
    """A NetBox API test client authenticated as a superuser via token."""

    def setUp(self):
        super().setUp()
        self.user.is_superuser = True
        self.user.save()

    def get(self, url):
        return self.client.get(url, **self.header)

    def send(self, method, url, data=None):
        return getattr(self.client, method)(url, data, format="json", **self.header)


class ServerAPISecretsTest(_SuperuserAPITestCase):
    def test_password_and_headers_are_write_only(self):
        server = make_server(password="SUPER-SECRET", headers={"X": "TOKEN"})
        for body in (self.get(f"{SERVERS}{server.pk}/").json(),
                     self.get(SERVERS).json()["results"][0]):
            self.assertNotIn("password", body)
            self.assertNotIn("headers", body)
            self.assertNotIn("SUPER-SECRET", str(body))
            self.assertNotIn("TOKEN", str(body))
        self.assertEqual(body["address"], "https://onms.example.org/opennms")

    def test_provisioned_rows_reject_writes(self):
        prod = OpenNMSServer.objects.create(
            name="prod", origin=ServerOriginChoices.PROVISIONED, url="https://p/"
        )
        entry = {"url": "https://p/", "username": "cfg-user"}
        with mock.patch(
            "netbox_opennms.models.config.server_entry", return_value=entry
        ):
            self.assertEqual(self.get(f"{SERVERS}{prod.pk}/").json()["username"],
                             "cfg-user")
            response = self.send("patch", f"{SERVERS}{prod.pk}/", {"description": "x"})
            self.assertEqual(response.status_code, 403)
            self.assertIn("PLUGINS_CONFIG", str(response.json()))
            response = self.send("delete", f"{SERVERS}{prod.pk}/")
            self.assertEqual(response.status_code, 403)
            bulk = [{"id": prod.pk, "description": "x"}]
            response = self.send("patch", SERVERS, bulk)
            self.assertEqual(response.status_code, 403)
            response = self.send("delete", SERVERS, [{"id": prod.pk}])
            self.assertEqual(response.status_code, 403)
        prod.refresh_from_db()
        self.assertEqual(prod.description, "")
        self.assertTrue(OpenNMSServer.objects.filter(pk=prod.pk).exists())

    def test_missing_provisioned_row_can_be_deleted(self):
        gone = OpenNMSServer.objects.create(
            name="gone", origin=ServerOriginChoices.PROVISIONED, url="https://g/"
        )
        with mock.patch("netbox_opennms.models.config.server_entry", return_value=None):
            response = self.send("delete", f"{SERVERS}{gone.pk}/")
        self.assertEqual(response.status_code, 204)

    def test_rename_cannot_unlock_a_provisioned_row(self):
        # Only "prod" has a config entry: a rename must not make the row look
        # missing (I1). Single and bulk PATCH both judge the stored row.
        prod = OpenNMSServer.objects.create(
            name="prod", origin=ServerOriginChoices.PROVISIONED, url="https://p/"
        )
        entry = {"url": "https://p/", "username": "cfg-user"}
        with mock.patch(
            "netbox_opennms.models.config.server_entry",
            side_effect=lambda name: entry if name == "prod" else None,
        ):
            data = {"name": "renamed", "address": "https://evil/"}
            response = self.send("patch", f"{SERVERS}{prod.pk}/", data)
            self.assertEqual(response.status_code, 403)
            response = self.send("patch", SERVERS, [{"id": prod.pk, **data}])
            self.assertEqual(response.status_code, 403)
        prod.refresh_from_db()
        self.assertEqual(
            (prod.name, prod.origin, prod.url),
            ("prod", ServerOriginChoices.PROVISIONED, "https://p/"),
        )

    def test_missing_provisioned_row_is_adopted_as_local(self):
        gone = OpenNMSServer.objects.create(
            name="gone", origin=ServerOriginChoices.PROVISIONED, url="https://g/"
        )
        with mock.patch("netbox_opennms.models.config.server_entry", return_value=None):
            response = self.send("patch", f"{SERVERS}{gone.pk}/", {"description": "d"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("password", response.json())
            response = self.send(
                "patch", f"{SERVERS}{gone.pk}/", {"username": "u", "password": "p"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["origin"], ServerOriginChoices.LOCAL)
        gone.refresh_from_db()
        self.assertEqual(gone.origin, ServerOriginChoices.LOCAL)
        self.assertEqual(gone.password, "p")

    def test_origin_is_read_only(self):
        response = self.send("post", SERVERS, {
            "name": "x", "address": "https://x/", "username": "u", "password": "p",
            "origin": "provisioned",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["origin"], "local")
        self.assertEqual(OpenNMSServer.objects.get(name="x").password, "p")

    def test_create_requires_credentials(self):
        response = self.send("post", SERVERS, {"name": "x", "address": "https://x/"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json())
        self.assertIn("password", response.json())

    def test_blank_password_on_update_keeps_stored_value(self):
        server = make_server(password="keep-me")
        for data in ({"description": "d"}, {"password": ""}):
            response = self.send("patch", f"{SERVERS}{server.pk}/", data)
            self.assertEqual(response.status_code, 200)
            server.refresh_from_db()
            self.assertEqual(server.password, "keep-me")
        self.send("patch", f"{SERVERS}{server.pk}/", {"password": "new"})
        server.refresh_from_db()
        self.assertEqual(server.password, "new")

    def test_headers_must_map_strings_to_strings(self):
        server = make_server()
        for bad in ({"X": 1}, ["X"]):
            response = self.send("patch", f"{SERVERS}{server.pk}/", {"headers": bad})
            self.assertEqual(response.status_code, 400)
            self.assertIn("headers", response.json())
        response = self.send("patch", f"{SERVERS}{server.pk}/", {"headers": {"X": "y"}})
        self.assertEqual(response.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.headers, {"X": "y"})

    def test_bad_address_error_names_address(self):
        response = self.send("post", SERVERS, {
            "name": "x", "address": "ftp://x/", "username": "u", "password": "p",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("address", response.json())

    def test_filterset_exposes_no_secret_filters(self):
        for name in OpenNMSServerFilterSet.base_filters:
            self.assertFalse(name.startswith(("password", "headers")), name)

    def test_export_templates_and_custom_links_are_disabled(self):
        for feature in ("export_templates", "custom_links"):
            self.assertNotIn(feature, get_model_features(OpenNMSServer))
            self.assertIn(feature, get_model_features(Requisition))
        # Every other NetBoxModel feature is kept.
        dropped = set(get_model_features(Requisition)) - set(
            get_model_features(OpenNMSServer)
        )
        self.assertEqual(dropped, {"export_templates", "custom_links"})

    def test_custom_link_cannot_target_servers(self):
        # A custom link renders arbitrary attributes ({{ object.password }}) on
        # the detail page and in table exports; NetBox only lets one target a
        # model with the custom_links feature.
        response = self.send("post", "/api/extras/custom-links/", {
            "name": "leak",
            "object_types": ["netbox_opennms.opennmsserver"],
            "link_text": "{{ object.password }}",
            "link_url": "https://x/{{ object.password }}",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("object_types", response.json())
        self.assertFalse(CustomLink.objects.filter(name="leak").exists())
        self.assertNotIn(
            ObjectType.objects.get_for_model(OpenNMSServer),
            ObjectType.objects.with_feature("custom_links"),
        )


class RequisitionServerDefaultTest(_SuperuserAPITestCase):
    data = {"name": "r", "filter_params": {"site": ["x"]}}

    def test_single_server_is_defaulted(self):
        server = make_server()
        response = self.send("post", REQUISITIONS, self.data)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["server"]["id"], server.pk)
        self.assertEqual(response.json()["server"]["name"], server.name)

    def test_two_servers_require_explicit_server(self):
        make_server("a")
        make_server("b")
        response = self.send("post", REQUISITIONS, self.data)
        self.assertEqual(response.status_code, 400)
        self.assertIn("server", response.json())

    def test_no_server_requires_explicit_server(self):
        response = self.send("post", REQUISITIONS, self.data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["server"],
            ["This field is required: no OpenNMS server exists yet."],
        )

    def test_two_servers_message_names_the_reason(self):
        make_server("a")
        make_server("b")
        response = self.send("post", REQUISITIONS, self.data)
        self.assertEqual(
            response.json()["server"],
            ["This field is required when more than one server exists."],
        )

    def test_explicit_server_is_used(self):
        make_server("a")
        b = make_server("b")
        response = self.send("post", REQUISITIONS, {**self.data, "server": b.pk})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["server"]["id"], b.pk)

    def test_update_without_server_keeps_it(self):
        a = make_server("a")
        make_server("b")
        req = Requisition.objects.create(
            server=a, name="r", filter_params={"site": ["x"]}
        )
        response = self.send("patch", f"{REQUISITIONS}{req.pk}/", {"description": "d"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["server"]["id"], a.pk)

    def test_filter_by_server(self):
        a = make_server("a")
        b = make_server("b")
        Requisition.objects.create(server=a, name="ra", filter_params={"site": ["x"]})
        Requisition.objects.create(server=b, name="rb", filter_params={"site": ["y"]})
        for query in (f"server_id={b.pk}", "server=b"):
            results = self.get(f"{REQUISITIONS}?{query}").json()["results"]
            names = [r["name"] for r in results]
            self.assertEqual(names, ["rb"], query)
