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


class RequisitionAddButtonsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requisition = Requisition.objects.create(
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
