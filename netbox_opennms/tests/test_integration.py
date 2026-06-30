# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Live OpenNMS Horizon 36 round-trip + PRESET LIVE-VERIFY (Epic 5).

SKIPPED unless ``OPENNMS_LIVE_URL`` (+ ``OPENNMS_LIVE_USERNAME`` /
``OPENNMS_LIVE_PASSWORD``) is set, so ``make verify`` stays green without OpenNMS.
``make integration`` boots a throwaway H36 and runs this.

This is the Epic 5 HARD GATE. The detector/policy preset registry encodes an
OpenNMS-version contract (class names + parameters) that a unit test cannot
check — the ':'-delimiter bug proved that. This test validates the contract
against a real Horizon:

* ``test_detector_preset_detects_icmp`` — provisions a node whose management IP
  is reachable BY OpenNMS (127.0.0.1) with the ICMP detector preset, imports,
  and asserts OpenNMS auto-detects the ICMP service. This proves the IcmpDetector
  preset class actually resolves and runs (not just that the XML is accepted).
* ``test_all_presets_accepted_and_round_trip`` — builds a profile carrying every
  detector and policy preset, POSTs the foreign-source definition, and reads it
  back asserting each preset's class survived. Catches a malformed/garbled class
  string or XSD-invalid parameter shape for the whole registry.
* ``test_malformed_requisition_is_rejected`` — XSD enforcement still holds.
"""

import os
import time
import unittest
from urllib.parse import quote

import requests
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
from requests.auth import HTTPBasicAuth

from netbox_opennms.client import OpenNMSClient, OpenNMSHTTPError
from netbox_opennms.derivation import foreign_id_for
from netbox_opennms.membership import resolve
from netbox_opennms.models import (
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringPolicy,
    MonitoringProfile,
)
from netbox_opennms.presets import (
    DETECTOR_PRESETS,
    POLICY_PRESETS,
    resolve_detector,
    resolve_policy,
)
from netbox_opennms.translation import (
    render_foreign_source_definition,
    render_requisition,
)

LIVE_URL = os.environ.get("OPENNMS_LIVE_URL")
LIVE_USER = os.environ.get("OPENNMS_LIVE_USERNAME", "admin")
LIVE_PASSWORD = os.environ.get("OPENNMS_LIVE_PASSWORD", "admin")
FS = "netbox.citest.router"
LOCATION = "Default"
# Reachable BY the OpenNMS instance itself, so the ICMP detector can detect.
REACHABLE_IP = "127.0.0.1"


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

    def _get(self, path, **params):
        return requests.get(
            f"{LIVE_URL.rstrip('/')}{path}",
            auth=self._auth(),
            headers={"Accept": "application/json"},
            params=params or None,
            timeout=15,
        )

    def _delete_foreign_source(self):
        encoded = quote(FS, safe="")
        paths = (f"/rest/requisitions/{encoded}", f"/rest/foreignSources/{encoded}")
        for path in paths:
            try:
                requests.delete(
                    f"{LIVE_URL.rstrip('/')}{path}", auth=self._auth(), timeout=10
                )
            except requests.RequestException:
                pass

    def _profile_with_icmp(self):
        profile = MonitoringProfile.objects.create(name="CI Network device")
        cls, params = resolve_detector("icmp")
        MonitoringDetector.objects.create(
            profile=profile,
            name="ICMP",
            preset="icmp",
            rule_class=cls,
            parameters=params,
        )
        return profile

    def _device_node(self, profile):
        site = Site.objects.create(name="CI Test", slug="citest")
        role = DeviceRole.objects.create(name="Router", slug="router")
        mfr = Manufacturer.objects.create(name="Acme", slug="acme")
        dt = DeviceType.objects.create(manufacturer=mfr, model="M1", slug="m1")
        device = Device.objects.create(
            name="ci-rtr-1", device_type=dt, role=role, site=site
        )
        iface = Interface.objects.create(device=device, name="eth0", type="virtual")
        ip = IPAddress.objects.create(
            address=f"{REACHABLE_IP}/32", assigned_object=iface
        )
        device.primary_ip4 = ip
        device.save()
        MonitoringAssignment.objects.create(
            profile=profile, site=site, role=role, location=LOCATION
        )
        return device

    def _poll_for_node(self, foreign_id):
        for _ in range(30):
            got = self._get("/rest/nodes", foreignSource=FS)
            if got.status_code == 200:
                for node in got.json().get("node") or []:
                    if node.get("foreignId") == foreign_id:
                        return node
            time.sleep(2)
        return None

    def _poll_for_service(self, node_id, service_name):
        path = f"/rest/nodes/{node_id}/ipinterfaces/{REACHABLE_IP}/services"
        for _ in range(45):
            got = self._get(path)
            if got.status_code == 200:
                for svc in got.json().get("service") or []:
                    if (svc.get("serviceType") or {}).get("name") == service_name:
                        return True
            time.sleep(4)
        return False

    def test_detector_preset_detects_icmp(self):
        profile = self._profile_with_icmp()
        device = self._device_node(profile)
        nodes = resolve(FS).nodes
        try:
            with self._client() as client:
                fs_xml = render_foreign_source_definition(FS, profile)
                client.post_foreign_source(fs_xml)
                client.post_requisition(render_requisition(FS, nodes))
                response = client.import_requisition(FS, rescan_existing="true")
                self.assertIn(response.status_code, (200, 202))

            node = self._poll_for_node(foreign_id_for(device))
            self.assertIsNotNone(node, "node was not provisioned after import")
            self.assertEqual(node.get("location"), LOCATION)
            # THE preset gate: OpenNMS instantiated + ran the ICMP detector.
            self.assertTrue(
                self._poll_for_service(node["id"], "ICMP"),
                "ICMP detector preset did not detect the service — the preset "
                "class/params may be wrong for this Horizon version.",
            )
        finally:
            self._delete_foreign_source()

    def test_reconcile_wire_contract(self):
        # The drift reconciler's contract: list the netbox.* requisitions OpenNMS
        # holds (GET /rest/requisitions JSON shape) and delete the requisition +
        # foreign-source shell of an orphan. Validates list_requisition_names +
        # delete_requisition + delete_foreign_source against real H36.
        profile = self._profile_with_icmp()
        self._device_node(profile)
        nodes = resolve(FS).nodes
        try:
            with self._client() as client:
                fs_xml = render_foreign_source_definition(FS, profile)
                client.post_foreign_source(fs_xml)
                client.post_requisition(render_requisition(FS, nodes))
                client.import_requisition(FS, rescan_existing="true")
                self.assertIn(FS, client.list_requisition_names())
                client.delete_requisition(FS)
                client.delete_foreign_source(FS)
                # The orphan shell is gone, so the reconciler won't re-find it.
                gone = False
                for _ in range(10):
                    if FS not in client.list_requisition_names():
                        gone = True
                        break
                    time.sleep(2)
                self.assertTrue(gone, "requisition still listed after delete")
        finally:
            self._delete_foreign_source()

    def test_all_presets_accepted_and_round_trip(self):
        profile = MonitoringProfile.objects.create(name="CI All presets")
        for key in DETECTOR_PRESETS:
            cls, params = resolve_detector(key)
            MonitoringDetector.objects.create(
                profile=profile, name=key, preset=key, rule_class=cls, parameters=params
            )
        for key in POLICY_PRESETS:
            cls, params = resolve_policy(key)
            MonitoringPolicy.objects.create(
                profile=profile, name=key, preset=key, rule_class=cls, parameters=params
            )
        expected = {resolve_detector(k)[0] for k in DETECTOR_PRESETS}
        expected |= {resolve_policy(k)[0] for k in POLICY_PRESETS}
        try:
            with self._client() as client:
                # Accepted by OpenNMS (XSD-valid for every preset's parameters).
                fs_xml = render_foreign_source_definition(FS, profile)
                client.post_foreign_source(fs_xml)
            got = self._get(f"/rest/foreignSources/{quote(FS, safe='')}")
            self.assertEqual(got.status_code, 200)
            # Every preset's class survived the round-trip (raw-text search is
            # robust to the foreign-source JSON shape).
            for cls in sorted(expected):
                self.assertIn(cls, got.text, f"{cls} missing from readback")
        finally:
            self._delete_foreign_source()

    def test_malformed_requisition_is_rejected(self):
        with self.assertRaises(OpenNMSHTTPError):
            with self._client() as client:
                client.post_requisition(b"<not-a-requisition/>")
