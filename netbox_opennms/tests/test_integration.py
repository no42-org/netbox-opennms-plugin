# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Live OpenNMS Horizon 35 round-trip (Story 4.4) — SKIPPED unless an endpoint is
configured.

Set ``OPENNMS_LIVE_URL`` (+ ``OPENNMS_LIVE_USERNAME`` / ``OPENNMS_LIVE_PASSWORD``)
to run a real import round-trip against a disposable OpenNMS. With nothing set the
class is skipped, so ``make verify`` stays green without OpenNMS. The live job runs
this via ``make integration`` / the nightly CI workflow against a throwaway H35.

It validates that OpenNMS accepts our rendered XML (XSD-valid), provisions the
node, and places it in the expected monitoring ``location`` (read back from the
deployed node, not the requisition we sent), with the import using
``rescanExisting=true`` — and that a malformed requisition is rejected (the client
raises ``OpenNMSHTTPError``). Read-back uses ``requests`` directly so the product
stays push-only for v1 (AD-12).
"""

import os
import time
import unittest
from urllib.parse import quote

import requests
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.test import TestCase
from ipam.models import IPAddress
from requests.auth import HTTPBasicAuth

from netbox_opennms.client import OpenNMSClient, OpenNMSHTTPError
from netbox_opennms.derivation import foreign_id_for
from netbox_opennms.models import MonitoringProfile
from netbox_opennms.translation import (
    render_foreign_source_definition,
    render_requisition,
)

LIVE_URL = os.environ.get("OPENNMS_LIVE_URL")
LIVE_USER = os.environ.get("OPENNMS_LIVE_USERNAME", "admin")
LIVE_PASSWORD = os.environ.get("OPENNMS_LIVE_PASSWORD", "admin")
# A throwaway Foreign Source for this test only — cleaned up in finally.
FS = "netbox.citest.router"
LOCATION = "Default"


@unittest.skipUnless(
    LIVE_URL, "set OPENNMS_LIVE_URL to run the live OpenNMS round-trip"
)
class OpenNMSRoundTripTest(TestCase):
    def _client(self):
        return OpenNMSClient(
            base_url=LIVE_URL, username=LIVE_USER, password=LIVE_PASSWORD
        )

    def _auth(self):
        return HTTPBasicAuth(LIVE_USER, LIVE_PASSWORD)

    def _delete_foreign_source(self):
        base = LIVE_URL.rstrip("/")
        encoded = quote(FS, safe="")
        paths = (
            f"/rest/requisitions/{encoded}",
            f"/rest/foreignSources/{encoded}",
        )
        for path in paths:
            try:
                requests.delete(f"{base}{path}", auth=self._auth(), timeout=10)
            except requests.RequestException:
                pass

    def _make_profile(self):
        site = Site.objects.create(name="CI Test", slug="citest")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        device = Device.objects.create(
            name="ci-rtr-1", device_type=dt, role=role, site=site
        )
        ip = IPAddress.objects.create(address="198.51.100.10/24")
        return MonitoringProfile.objects.create(
            assigned_object=device, management_ip=ip, location=LOCATION
        ), device

    def _poll_for_node(self, foreign_id):
        # Read the DEPLOYED node back (the import is async) — not the requisition
        # we POSTed. Returns the node dict once it appears, else None.
        url = f"{LIVE_URL.rstrip('/')}/rest/nodes"
        for _ in range(30):
            got = requests.get(
                url,
                auth=self._auth(),
                headers={"Accept": "application/json"},
                params={"foreignSource": FS},
                timeout=10,
            )
            if got.status_code == 200:
                for node in got.json().get("node") or []:
                    if node.get("foreignId") == foreign_id:
                        return node
            time.sleep(2)
        return None

    def test_import_round_trip(self):
        profile, device = self._make_profile()
        client = self._client()
        try:
            with client:
                # Happy path: definition → requisition → import (XSD-valid).
                client.post_foreign_source(render_foreign_source_definition(FS))
                client.post_requisition(render_requisition(FS, [profile]))
                response = client.import_requisition(FS, rescan_existing="true")
                self.assertIn(response.status_code, (200, 202))

            # The provisioned node carries the location we placed it in (AC4).
            node = self._poll_for_node(foreign_id_for(device))
            self.assertIsNotNone(node, "node was not provisioned after import")
            self.assertEqual(node.get("location"), LOCATION)
        finally:
            self._delete_foreign_source()

    def test_malformed_requisition_is_rejected(self):
        # XSD enforcement: a non-requisition body must be refused (4xx → typed err).
        client = self._client()
        with self.assertRaises(OpenNMSHTTPError):
            with client:
                client.post_requisition(b"<not-a-requisition/>")
