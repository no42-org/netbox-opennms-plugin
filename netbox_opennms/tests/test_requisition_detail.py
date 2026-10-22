# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""The Requisition detail page exposes per-panel Add buttons.

Detectors, policies, asset mappings, and metadata are separate models attached to
a Requisition by FK; the detail page must let the user add each one in-context,
pre-selecting the parent requisition via `?requisition=<pk>`.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netbox_opennms.models import Requisition
from netbox_opennms.tests.servers import make_server


class RequisitionAddButtonsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.server = make_server()
        cls.requisition = Requisition.objects.create(
            server=cls.server,
            name="router", filter_params={"role": ["router"]}
        )
        cls.user = get_user_model().objects.create_user(
            username="admin", password="pw", is_superuser=True
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_each_panel_has_an_add_button_prefilling_the_requisition(self):
        url = reverse(
            "plugins:netbox_opennms:requisition", kwargs={"pk": self.requisition.pk}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        for name in (
            "monitoringdetector",
            "monitoringpolicy",
            "assetmapping",
            "metadataentry",
        ):
            add_url = reverse(f"plugins:netbox_opennms:{name}_add")
            self.assertContains(
                response, f"{add_url}?requisition={self.requisition.pk}"
            )

    def test_detail_shows_server(self):
        url = reverse(
            "plugins:netbox_opennms:requisition", kwargs={"pk": self.requisition.pk}
        )
        response = self.client.get(url)
        self.assertContains(response, self.server.get_absolute_url())

    def _move_to(self, server):
        data = {
            "name": self.requisition.name,
            "server": server.pk,
            "object_types": self.requisition.object_types,
            "filter_params": '{"role": ["router"]}',
            "scan_interval": self.requisition.scan_interval,
            "default_interfaces": self.requisition.default_interfaces,
        }
        response = self.client.post(
            reverse(
                "plugins:netbox_opennms:requisition_edit",
                args=[self.requisition.pk],
            ),
            data,
            follow=True,
        )
        self.requisition.refresh_from_db()
        self.assertEqual(self.requisition.server_id, server.pk)
        return response

    def test_server_change_warns(self):
        b = make_server("b", url="https://b.example.org/opennms")
        response = self._move_to(b)
        self.assertContains(
            response, "the reconciler removes the copy on default"
        )

    def test_move_to_same_opennms_keeps_nodes(self):
        # Same instance, different spelling of the URL.
        b = make_server("b", url="HTTPS://ONMS.example.org/opennms/")
        response = self._move_to(b)
        self.assertContains(
            response,
            "Moved to server b, which points at the same OpenNMS as default; "
            "the existing nodes stay in place.",
        )
        self.assertNotContains(response, "reconciler removes")
