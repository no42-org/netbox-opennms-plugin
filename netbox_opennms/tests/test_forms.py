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
from django.test import TestCase
from extras.models import SavedFilter
from ipam.models import IPAddress

from netbox_opennms.forms import MonitoringOverrideForm, RequisitionForm


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

    def test_additional_ip_must_belong_to_object(self):
        form = MonitoringOverrideForm(
            data={
                "device": self.device.pk,
                "exclude": False,
                "additional_ips": [self.foreign_ip.pk],
                "location": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("additional_ips", form.errors)

    def test_own_additional_ip_is_accepted(self):
        form = MonitoringOverrideForm(
            data={
                "device": self.device.pk,
                "exclude": False,
                "additional_ips": [self.own_ip.pk],
                "location": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_exactly_one_target_required(self):
        form = MonitoringOverrideForm(data={"exclude": False, "location": ""})
        self.assertFalse(form.is_valid())


class RequisitionSavedFilterImportTest(TestCase):
    def _form(self, **overrides):
        data = {
            "name": "core-switches",
            "priority": 100,
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
