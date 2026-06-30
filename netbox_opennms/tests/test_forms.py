# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Form-layer tests for the Epic 5 models (override IP ownership)."""

from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Interface,
    Manufacturer,
    Site,
)
from django.test import TestCase
from ipam.models import IPAddress

from netbox_opennms.forms import MonitoringOverrideForm


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
