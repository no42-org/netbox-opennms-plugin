# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI view (CRUD) tests for the plugin models."""

from unittest import mock

from core.models import ObjectChange
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from ipam.models import IPAddress
from utilities.testing import ViewTestCases

from netbox_opennms.crypto import Undecryptable
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
        cls.server = make_server()
        for name in ("req-1", "req-2", "req-3"):
            Requisition.objects.create(
                server=cls.server, name=name, filter_params=FILTER
            )
        cls.form_data = {
            "name": "req-4",
            "server": cls.server.pk,
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
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server, name="req", filter_params=FILTER
        )
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
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server, name="req", filter_params=FILTER
        )
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


class AssetMappingViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = AssetMapping

    def _get_base_url(self):
        return "plugins:netbox_opennms:assetmapping_{}"

    @classmethod
    def setUpTestData(cls):
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server,
            name="am-req", filter_params={"role": ["switch"]}
        )
        for source, field in [
            ("serial", "serialNumber"),
            ("name", "displayCategory"),
            ("description", "description"),
        ]:
            AssetMapping.objects.create(
                requisition=req, netbox_source=source, asset_field=field
            )
        cls.form_data = {
            "requisition": req.pk,
            "netbox_source": "asset_tag",
            "asset_field": "assetNumber",
        }


class MetadataEntryViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MetadataEntry

    def _get_base_url(self):
        return "plugins:netbox_opennms:metadataentry_{}"

    @classmethod
    def setUpTestData(cls):
        cls.server = make_server()
        req = Requisition.objects.create(
            server=cls.server,
            name="me-req", filter_params={"role": ["switch"]}
        )
        for scope, key in [("node", "k1"), ("node", "k2"), ("interface", "k3")]:
            MetadataEntry.objects.create(
                requisition=req, scope=scope, context="requisition",
                key=key, literal_value="v",
            )
        cls.form_data = {
            "requisition": req.pk,
            "scope": "node",
            "context": "requisition",
            "key": "k4",
            "value_source": "",
            "literal_value": "v4",
        }


class OpenNMSServerViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = OpenNMSServer
    form_data = {
        "name": "new",
        "url": "https://n/opennms",
        "username": "u",
        "password": "p",
        "headers": "{}",
    }
    # The JSON headers field posts a string but stores a dict.
    validation_excluded_fields = ("headers",)

    def _get_base_url(self):
        return "plugins:netbox_opennms:opennmsserver_{}"

    @classmethod
    def setUpTestData(cls):
        for name in ("srv-1", "srv-2", "srv-3"):
            make_server(name)


class ServerLockingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="admin", password="pw", is_superuser=True
        )
        cls.prod = OpenNMSServer.objects.create(
            name="prod", origin="provisioned", url="https://p/opennms"
        )
        cls.local = make_server("local", url="https://l/")

    def setUp(self):
        self.client.force_login(self.user)
        patcher = mock.patch(
            "netbox_opennms.models.config.server_entry",
            return_value={"url": "https://p/opennms", "username": "cfg"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_provisioned_edit_is_refused(self):
        response = self.client.post(
            reverse("plugins:netbox_opennms:opennmsserver_edit", args=[self.prod.pk]),
            {"name": "prod", "url": "https://x/"},
        )
        self.assertRedirects(response, self.prod.get_absolute_url())
        self.assertEqual(
            OpenNMSServer.objects.get(pk=self.prod.pk).url, "https://p/opennms"
        )

    def test_provisioned_edit_form_is_locked(self):
        response = self.client.get(
            reverse("plugins:netbox_opennms:opennmsserver_edit", args=[self.prod.pk])
        )
        self.assertContains(response, "Managed by configuration")
        self.assertTrue(
            all(f.disabled for f in response.context["form"].fields.values())
        )

    def test_provisioned_delete_is_refused(self):
        url = reverse(
            "plugins:netbox_opennms:opennmsserver_delete", args=[self.prod.pk]
        )
        self.assertRedirects(self.client.get(url), self.prod.get_absolute_url())
        self.client.post(url, {"confirm": True})
        self.assertTrue(OpenNMSServer.objects.filter(pk=self.prod.pk).exists())

    def test_bulk_delete_skips_provisioned(self):
        response = self.client.post(
            reverse("plugins:netbox_opennms:opennmsserver_bulk_delete"),
            {"pk": [self.prod.pk, self.local.pk], "confirm": True, "_confirm": True},
            follow=True,
        )
        self.assertTrue(OpenNMSServer.objects.filter(pk=self.prod.pk).exists())
        self.assertFalse(OpenNMSServer.objects.filter(pk=self.local.pk).exists())
        self.assertContains(response, "prod")

    def test_missing_provisioned_converts_to_local(self):
        with mock.patch("netbox_opennms.models.config.server_entry", return_value=None):
            self.client.post(
                reverse(
                    "plugins:netbox_opennms:opennmsserver_edit", args=[self.prod.pk]
                ),
                {
                    "name": "prod",
                    "url": "https://p/opennms",
                    "username": "u",
                    "password": "p",
                    "headers": "{}",
                },
            )
        self.assertEqual(OpenNMSServer.objects.get(pk=self.prod.pk).origin, "local")

    def test_detail_never_renders_password(self):
        self.local.password = "SUPER-SECRET"
        self.local.headers = {"X-Token": "HEADER-SECRET"}
        self.local.save()
        response = self.client.get(self.local.get_absolute_url())
        self.assertNotContains(response, "SUPER-SECRET")
        self.assertNotContains(response, "HEADER-SECRET")
        self.assertContains(response, "X-Token")

    def test_blank_password_on_edit_keeps_stored_value(self):
        self.client.post(
            reverse("plugins:netbox_opennms:opennmsserver_edit", args=[self.local.pk]),
            {
                "name": "local",
                "url": "https://l/",
                "username": "u",
                "password": "",
                "headers": "{}",
            },
        )
        self.assertEqual(OpenNMSServer.objects.get(pk=self.local.pk).password, "pw")

    def test_blank_password_on_create_is_a_field_error(self):
        from netbox_opennms.forms import OpenNMSServerForm

        form = OpenNMSServerForm(
            data={"name": "n", "url": "https://n/", "username": "u", "password": ""}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_undecryptable_edit_requires_new_password(self):
        from netbox_opennms.forms import OpenNMSServerForm

        self.local.password = Undecryptable("gAAAA-broken")
        form = OpenNMSServerForm(
            data={"name": "local", "url": "https://l/", "username": "u",
                  "password": "", "headers": "{}"},
            instance=self.local,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_list_reconciles_provisioned_servers(self):
        with mock.patch(
            "netbox_opennms.views.reconcile_provisioned_servers"
        ) as reconcile:
            response = self.client.get(
                reverse("plugins:netbox_opennms:opennmsserver_list")
            )
        self.assertEqual(response.status_code, 200)
        reconcile.assert_called_once_with()


    def test_list_reconcile_logs_no_change_when_nothing_drifted(self):
        entry = {"url": "https://c/opennms", "username": "u", "password": "p",
                 "default_location": "", "headers": {}}
        with mock.patch(
            "netbox_opennms.config.server_entries", return_value={"cfg": entry}
        ):
            for _ in range(3):
                self.client.get(reverse("plugins:netbox_opennms:opennmsserver_list"))
        changes = ObjectChange.objects.filter(object_repr="cfg")
        self.assertEqual(list(changes.values_list("action", flat=True)), ["create"])
