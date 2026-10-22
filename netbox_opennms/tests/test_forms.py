# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Form-layer tests for the Epic 5 models (override IP ownership)."""

from core.models import ObjectType
from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.core.exceptions import ValidationError
from django.test import TestCase
from extras.models import SavedFilter
from ipam.models import IPAddress

from netbox_opennms.catalog import get_detector_catalog
from netbox_opennms.client import DiscoveredParam, DiscoveredPlugin
from netbox_opennms.forms import (
    MonitoringDetectorForm,
    MonitoringOverrideForm,
    RequisitionForm,
    _server_for_rule_form,
)
from netbox_opennms.models import (
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    Requisition,
)
from netbox_opennms.presets import DETECTOR_PRESETS
from netbox_opennms.tests.servers import make_server
from netbox_opennms.tests.test_catalog import FakeClient


class MonitoringOverrideFormTest(TestCase):
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
        cls.own_ip = IPAddress.objects.create(
            address="10.0.0.1/24", assigned_object=iface
        )
        cls.other = Device.objects.create(
            name="rtr-2", device_type=dt, role=role, site=site
        )
        oface = Interface.objects.create(device=cls.other, name="eth0", type="virtual")
        cls.foreign_ip = IPAddress.objects.create(
            address="10.0.0.2/24", assigned_object=oface
        )

    def test_exactly_one_target_required(self):
        form = MonitoringOverrideForm(data={"exclude": False, "location": ""})
        self.assertFalse(form.is_valid())


class RequisitionSavedFilterImportTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.server = make_server()

    def _form(self, **overrides):
        data = {
            "name": "core-switches",
            "server": self.server.pk,
            "object_types": "device",
            "filter_params": "{}",
            "scan_interval": "1d",
            "default_interfaces": "primary",
        }
        data.update(overrides)
        return RequisitionForm(data=data)

    def test_import_copies_saved_filter_parameters(self):
        saved = SavedFilter.objects.create(
            name="Switches", slug="switches", parameters={"role": ["switch"]}
        )
        saved.object_types.set([ObjectType.objects.get_for_model(Device)])
        form = self._form(import_from_saved_filter=saved.pk)
        self.assertTrue(form.is_valid(), form.errors)
        # One-shot copy: the empty filter is replaced by the Saved Filter's params.
        self.assertEqual(form.cleaned_data["filter_params"], {"role": ["switch"]})

    def test_import_and_typed_filter_conflict_is_rejected(self):
        # Picking a Saved Filter AND typing a filter is ambiguous — reject, don't
        # silently discard the typed one (review #5).
        saved = SavedFilter.objects.create(
            name="Switches", slug="switches", parameters={"role": ["switch"]}
        )
        saved.object_types.set([ObjectType.objects.get_for_model(Device)])
        form = self._form(
            import_from_saved_filter=saved.pk, filter_params='{"role": ["router"]}'
        )
        self.assertFalse(form.is_valid())
        self.assertIn("import_from_saved_filter", form.errors)

    def test_import_of_empty_saved_filter_is_still_guarded(self):
        # Importing a Saved Filter with no effective constraint is rejected (H1).
        saved = SavedFilter.objects.create(
            name="Everything", slug="everything", parameters={}
        )
        saved.object_types.set([ObjectType.objects.get_for_model(Device)])
        form = self._form(import_from_saved_filter=saved.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("filter_params", form.errors)


class MonitoredInterfaceValidationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Durham", slug="durham")
        role = DeviceRole.objects.create(name="Switch", slug="switch")
        mfr = Manufacturer.objects.create(name="Acme2", slug="acme2")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M2", slug="m2")
        cls.device = Device.objects.create(
            name="sw-1", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(device=cls.device, name="eth0", type="virtual")
        cls.mgmt = IPAddress.objects.create(
            address="10.1.0.1/24", assigned_object=iface
        )
        cls.extra = IPAddress.objects.create(
            address="10.1.0.2/24", assigned_object=iface
        )
        cls.device.primary_ip4 = cls.mgmt
        cls.device.save()
        other = Device.objects.create(
            name="sw-2", device_type=dt, role=role, site=site
        )
        oface = Interface.objects.create(device=other, name="eth0", type="virtual")
        cls.foreign = IPAddress.objects.create(
            address="10.1.0.9/24", assigned_object=oface
        )
        cls.override = MonitoringOverride.objects.create(assigned_object=cls.device)

    def test_foreign_ip_rejected(self):
        interface = MonitoredInterface(
            override=self.override, ip_address=self.foreign, role="N"
        )
        with self.assertRaises(ValidationError):
            interface.clean()

    def test_own_ip_accepted(self):
        interface = MonitoredInterface(
            override=self.override, ip_address=self.extra, role="N"
        )
        interface.clean()  # no raise

    def test_second_primary_rejected(self):
        # management_role defaults to Primary, so a second Primary is rejected.
        interface = MonitoredInterface(
            override=self.override, ip_address=self.extra, role="P"
        )
        with self.assertRaises(ValidationError):
            interface.clean()


class InterfaceServicePruneTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Cary", slug="cary")
        role = DeviceRole.objects.create(name="Sw", slug="sw")
        mfr = Manufacturer.objects.create(name="Acme3", slug="acme3")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M3", slug="m3")
        cls.device = Device.objects.create(
            name="sw-p", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(device=cls.device, name="eth0", type="virtual")
        cls.ip_a = IPAddress.objects.create(
            address="10.2.0.2/24", assigned_object=iface
        )
        cls.ip_b = IPAddress.objects.create(
            address="10.2.0.3/24", assigned_object=iface
        )
        cls.override = MonitoringOverride.objects.create(assigned_object=cls.device)

    def test_editing_interface_ip_prunes_stale_service(self):
        interface = MonitoredInterface.objects.create(
            override=self.override, ip_address=self.ip_a, role="N"
        )
        MonitoredService.objects.create(
            override=self.override, ip_address=self.ip_a, name="HTTP"
        )
        # Move the interface to IP-B: the service on IP-A is now orphaned and pruned.
        interface.ip_address = self.ip_b
        interface.save()
        self.assertFalse(
            MonitoredService.objects.filter(
                override=self.override, ip_address=self.ip_a
            ).exists()
        )

ICMP_CLASS = DETECTOR_PRESETS["icmp"]["class"]


class DetectorFormServerTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)

    def _prime(self):
        a, b = make_server("a"), make_server("b")
        for server, key in ((a, "pA"), (b, "pB")):
            plugin = DiscoveredPlugin(
                name="ICMP",
                plugin_class=ICMP_CLASS,
                parameters=(DiscoveredParam(key=key),),
            )
            get_detector_catalog(server, client=FakeClient(detectors=[plugin]))
        req = Requisition.objects.create(
            server=b, name="r-b", filter_params={"role": ["switch"]}
        )
        return a, b, req

    def test_instance_path_uses_requisition_server(self):
        _a, _b, req = self._prime()
        det = MonitoringDetector.objects.create(
            requisition=req, name="icmp", preset="icmp"
        )
        form = MonitoringDetectorForm(instance=det)
        self.assertIn("param_pB", form.fields)
        self.assertNotIn("param_pA", form.fields)

    def test_data_path_uses_requisition_server(self):
        _a, b, req = self._prime()
        form = MonitoringDetectorForm(data={"requisition": str(req.pk)})
        self.assertEqual(_server_for_rule_form(form), b)

    def test_invalid_pk_resolves_no_server(self):
        self._prime()
        for bad in ("abc", "999999", ""):
            form = MonitoringDetectorForm(data={"requisition": bad})
            self.assertIsNone(_server_for_rule_form(form))

    def test_no_server_falls_back_to_overlay(self):
        det = MonitoringDetector(name="icmp", preset="icmp")
        form = MonitoringDetectorForm(instance=det)
        self.assertIn("param_timeout", form.fields)
        self.assertIn("unavailable", str(form.fields["rule_class"].help_text))

    def test_asset_form_uses_requisition_server(self):
        a, b, req = self._prime()
        from netbox_opennms.catalog import get_asset_fields
        from netbox_opennms.forms import AssetMappingForm

        get_asset_fields(a, client=FakeClient(assets=["fromA"]))
        get_asset_fields(b, client=FakeClient(assets=["fromB"]))
        form = AssetMappingForm(initial={"requisition": req.pk})
        values = [v for v, _l in form.fields["asset_field"].choices]
        self.assertEqual(values, ["fromB"])


class RequisitionFormServerTest(TestCase):
    def test_single_server_is_preselected(self):
        server = make_server()
        self.assertEqual(RequisitionForm().fields["server"].initial, server.pk)

    def test_two_servers_no_preselection(self):
        make_server("a")
        make_server("b")
        self.assertIsNone(RequisitionForm().fields["server"].initial)
