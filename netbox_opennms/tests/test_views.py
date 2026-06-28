# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""UI view tests for MonitoringProfile."""

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from ipam.models import IPAddress
from utilities.testing import ViewTestCases

from netbox_opennms.models import MonitoringProfile


class MonitoringProfileViewTest(
    ViewTestCases.GetObjectViewTestCase,
    ViewTestCases.GetObjectChangelogViewTestCase,
    ViewTestCases.CreateObjectViewTestCase,
    ViewTestCases.EditObjectViewTestCase,
    ViewTestCases.DeleteObjectViewTestCase,
    ViewTestCases.ListObjectsViewTestCase,
    ViewTestCases.BulkDeleteObjectsViewTestCase,
):
    model = MonitoringProfile

    def _get_base_url(self):
        # Plugin views live under the ``plugins:`` namespace.
        return "plugins:netbox_opennms:monitoringprofile_{}"

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name="Site 1", slug="site-1")
        manufacturer = Manufacturer.objects.create(name="Acme", slug="acme")
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model="Model 1", slug="model-1"
        )
        role = DeviceRole.objects.create(name="Router", slug="router")
        devices = [
            Device.objects.create(
                name=f"device-{i}",
                device_type=device_type,
                role=role,
                site=site,
            )
            for i in range(6)
        ]

        MonitoringProfile.objects.create(assigned_object=devices[0])
        MonitoringProfile.objects.create(assigned_object=devices[1])
        MonitoringProfile.objects.create(assigned_object=devices[2])

        ip = IPAddress.objects.create(address="10.0.0.1/24")

        # devices[3] has no profile → valid target for create/edit form posts;
        # the form requires a resolvable management IP, so supply one.
        cls.form_data = {
            "device": devices[3].pk,
            "management_ip": ip.pk,
            "enabled": True,
        }
