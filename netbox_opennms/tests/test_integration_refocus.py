# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Live Horizon 36 SPIKE for the requisition refocus (ADR-001).

A **spike**, not a regression suite: it answers the four open questions ADR-001
left for a live round-trip, and it *prints the real payloads* so the results can
be pasted back into ADR-001's "confirm on live Horizon 36" section. It is
deliberately independent of the not-yet-built RD-1/RD-2/RD-3 code — it probes the
OpenNMS wire contract directly with hand-built ``model-import`` XML (reusing the
plugin's namespace) and raw REST reads.

Run: ``make integration-spike`` (boots a throwaway H36). SKIPPED unless
``OPENNMS_LIVE_URL`` is set, so ``make verify`` stays green without OpenNMS.

Probes → ADR-001 open questions:
  A ``test_capture_foreignsourceconfig_catalog`` → RD-1/RD-2 discovery: the real
    ``/foreignSourcesConfig/{detectors,policies,assets}`` JSON, incl. NMS-8690
    asset-field drift and ``required``/``options`` fidelity.
  B ``test_asset_field_round_trip``  → RD-2: ``<asset name=… value=…/>`` survives.
  C ``test_metadata_scope_round_trip`` → RD-3: ``<meta-data context=… key=… value=…/>``
    accepted at node/interface/service scope, context ``requisition`` and ``X-netbox``.
  D ``test_interfaceless_node_imports`` → RD-6/h: a node with no ``<interface>``
    imports cleanly (inventory-only), so the yellow warning is warn-not-block.

Each probe prints a delimited ``==== SPIKE CAPTURE: … ====`` block. Capture with
``make integration-spike 2>&1 | tee spike-h36.txt`` and fold the findings into
ADR-001.
"""

import json
import os
import time
import unittest
from urllib.parse import quote

import requests
from lxml import etree
from requests.auth import HTTPBasicAuth

from netbox_opennms.client import OpenNMSClient
from netbox_opennms.translation.requisition import MODEL_IMPORT_NS as NS

LIVE_URL = os.environ.get("OPENNMS_LIVE_URL")
LIVE_USER = os.environ.get("OPENNMS_LIVE_USERNAME", "admin")
LIVE_PASSWORD = os.environ.get("OPENNMS_LIVE_PASSWORD", "admin")
FS = "netbox.spike.refocus"
# A fixed date-stamp keeps the request reproducible (no wall-clock in the spike).
DATE_STAMP = "2026-07-03T00:00:00.000-00:00"
# Reachable BY the OpenNMS instance itself.
REACHABLE_IP = "127.0.0.1"


def _q(name):
    return f"{{{NS}}}{name}"


def _dump(label, value):
    """Print a delimited capture block for pasting into ADR-001."""
    body = (
        value
        if isinstance(value, str)
        else json.dumps(value, indent=2, sort_keys=True)
    )
    print(f"\n==== SPIKE CAPTURE: {label} ====\n{body}\n==== END {label} ====\n")


@unittest.skipUnless(LIVE_URL, "set OPENNMS_LIVE_URL to run the live H36 spike")
class RefocusSpikeH36(unittest.TestCase):
    """Direct-to-REST probes; no NetBox ORM needed (hand-built requisition XML)."""

    # --- REST helpers -----------------------------------------------------

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

    def _client(self):
        return OpenNMSClient(
            base_url=LIVE_URL, username=LIVE_USER, password=LIVE_PASSWORD
        )

    def _cleanup(self):
        encoded = quote(FS, safe="")
        for path in (
            f"/rest/requisitions/{encoded}",
            f"/rest/foreignSources/{encoded}",
        ):
            try:
                requests.delete(
                    f"{LIVE_URL.rstrip('/')}{path}", auth=self._auth(), timeout=10
                )
            except requests.RequestException:
                pass

    def _post_and_import(self, requisition_xml):
        with self._client() as client:
            client.post_requisition(requisition_xml)
            response = client.import_requisition(FS, rescan_existing="true")
            self.assertIn(
                response.status_code,
                (200, 202),
                f"import rejected: {response.status_code} "
                f"{getattr(response, 'text', '')}",
            )

    def _poll_node(self, foreign_id):
        for _ in range(45):
            got = self._get("/rest/nodes", foreignSource=FS)
            if got.status_code == 200:
                for node in got.json().get("node") or []:
                    if node.get("foreignId") == foreign_id:
                        return node
            time.sleep(2)
        return None

    # --- XML builders (hand-built; RD-2/RD-3 rendering not built yet) ------

    def _model_import(self):
        root = etree.Element(_q("model-import"), nsmap={None: NS})
        root.set("foreign-source", FS)
        root.set("date-stamp", DATE_STAMP)
        return root

    def _xml(self, root):
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    def _node(self, root, foreign_id, label):
        node = etree.SubElement(root, _q("node"))
        node.set("node-label", label)
        node.set("foreign-id", foreign_id)
        return node

    def _primary_iface(self, node, ip=REACHABLE_IP):
        iface = etree.SubElement(node, _q("interface"))
        iface.set("ip-addr", ip)
        iface.set("snmp-primary", "P")
        svc = etree.SubElement(iface, _q("monitored-service"))
        svc.set("service-name", "ICMP")
        return iface

    def _asset(self, node, name, value):
        el = etree.SubElement(node, _q("asset"))
        el.set("name", name)
        el.set("value", value)

    def _meta(self, parent, context, key, value):
        el = etree.SubElement(parent, _q("meta-data"))
        el.set("context", context)
        el.set("key", key)
        el.set("value", value)

    # --- Probe A: discovery catalog --------------------------------------

    def test_capture_foreignsourceconfig_catalog(self):
        """RD-1/RD-2: capture the real discovery payloads + assert their shape."""
        findings = {}
        for kind in ("detectors", "policies", "assets"):
            got = self._get(f"/rest/foreignSourcesConfig/{kind}")
            self.assertEqual(got.status_code, 200, f"{kind}: HTTP {got.status_code}")
            payload = got.json()
            _dump(f"foreignSourcesConfig/{kind}", payload)
            findings[kind] = payload

        # Locate the plugin list wherever the JSON nests it (shape is what we're
        # here to confirm — dig leniently rather than assume).
        def plugins(payload):
            node = payload
            for key in ("plugin-configuration", "plugins", "plugin"):
                if isinstance(node, dict) and key in node:
                    node = node[key]
            if isinstance(node, dict):  # single-element JSON collapses to an object
                node = [node]
            return node if isinstance(node, list) else []

        detectors = plugins(findings["detectors"])
        self.assertTrue(detectors, "no detectors returned by foreignSourcesConfig")
        sample = detectors[0]
        self.assertIn("class", sample, f"detector entry missing 'class': {sample}")

        # Confirm the parameter schema carries key/required/options somewhere.
        param_shapes = []
        for plugin in detectors:
            params = plugin.get("parameters")
            if isinstance(params, dict):
                params = params.get("parameter", [])
            if isinstance(params, dict):
                params = [params]
            for p in params or []:
                param_shapes.append(sorted(p.keys()))
        _dump("detector parameter key-shapes (first 20)", param_shapes[:20])
        flat = {k for shape in param_shapes for k in shape}
        self.assertTrue(
            {"key"} & flat,
            f"no parameter carried a 'key' — schema shape unexpected: {flat}",
        )
        # Informational: are required/options present as ADR-001 assumes?
        _dump(
            "ADR-001 fidelity check",
            {
                "param_keys_seen": sorted(flat),
                "has_required": "required" in flat,
                "has_options": "options" in flat,
                "detector_count": len(detectors),
            },
        )

    # --- Probe B: asset round-trip ---------------------------------------

    def test_asset_field_round_trip(self):
        """RD-2: assets in the requisition survive to the node's asset record."""
        root = self._model_import()
        node = self._node(root, "spike-asset", "spike-asset-node")
        self._primary_iface(node)
        # A safe field + a couple more to probe NMS-8690 acceptance.
        wanted = {
            "serialNumber": "SN-CI-1",
            "assetNumber": "AN-CI-1",
            "manufacturer": "Acme",
        }
        for name, value in wanted.items():
            self._asset(node, name, value)
        try:
            self._post_and_import(self._xml(root))
            found = self._poll_node("spike-asset")
            self.assertIsNotNone(found, "asset node not provisioned")
            got = self._get(f"/rest/nodes/{found['id']}/assetRecord")
            _dump("node assetRecord readback", got.text)
            self.assertEqual(got.status_code, 200)
            record = got.json()
            self.assertEqual(
                record.get("serialNumber"), "SN-CI-1", "serialNumber did not round-trip"
            )
            survived = {k: record.get(k) for k in wanted}
            _dump("asset fields that survived", survived)
        finally:
            self._cleanup()

    # --- Probe C: metadata scopes ----------------------------------------

    def test_metadata_scope_round_trip(self):
        """RD-3: node/iface/service meta-data accepted; requisition + X- contexts."""
        root = self._model_import()
        node = self._node(root, "spike-meta", "spike-meta-node")
        iface = self._primary_iface(node)
        # service element is the ICMP monitored-service on the interface
        svc = iface.find(_q("monitored-service"))
        self._meta(node, "requisition", "netbox-node-key", "node-val")
        self._meta(node, "X-netbox", "source", "netbox")
        self._meta(iface, "requisition", "netbox-iface-key", "iface-val")
        self._meta(svc, "requisition", "netbox-svc-key", "svc-val")
        try:
            self._post_and_import(self._xml(root))
            found = self._poll_node("spike-meta")
            self.assertIsNotNone(found, "metadata node not provisioned")
            nid = found["id"]
            # H36 has no /rest/nodes/{id}/metadata endpoint (returns 404), so verify
            # acceptance + persistence via the DEPLOYED requisition round-trip: the
            # import was already accepted (no rejection in _post_and_import); the
            # <meta-data> we posted must survive into the deployed requisition.
            deployed = self._get(f"/rest/requisitions/deployed/{quote(FS, safe='')}")
            _dump("deployed requisition readback", deployed.text)
            node_md = self._get(f"/rest/nodes/{nid}/metadata")  # best-effort capture
            _dump("node /metadata endpoint status", {"status": node_md.status_code})
            self.assertEqual(
                deployed.status_code, 200, "deployed requisition not readable"
            )
            self.assertIn(
                "netbox-node-key", deployed.text, "node meta-data did not round-trip"
            )
            self.assertIn(
                "X-netbox", deployed.text, "X- context meta-data did not round-trip"
            )
            self.assertIn(
                "netbox-svc-key", deployed.text, "service meta-data did not round-trip"
            )
        finally:
            self._cleanup()

    # --- Probe D: interface-less node ------------------------------------

    def test_interfaceless_node_imports(self):
        """RD-6/h: a node with NO <interface> imports cleanly (inventory-only)."""
        root = self._model_import()
        # No interface, no services — just the node shell.
        self._node(root, "spike-bare", "spike-bare-node")
        try:
            # The assertion inside _post_and_import is the probe: import must NOT
            # be rejected for a missing interface.
            self._post_and_import(self._xml(root))
            found = self._poll_node("spike-bare")
            self.assertIsNotNone(found, "interface-less node not provisioned")
            ifaces = self._get(f"/rest/nodes/{found['id']}/ipinterfaces")
            _dump("interface-less node ipinterfaces readback", ifaces.text)
            # H36 returns {"count": null, "totalCount": 0, "ipInterface": []} for a
            # node with no interfaces — assert on totalCount (count is null here).
            body = ifaces.json() or {}
            total = body.get("totalCount")
            _dump(
                "interface-less node result",
                {"node_id": found["id"], "totalCount": total},
            )
            self.assertEqual(total, 0, "expected zero interfaces on the bare node")
        finally:
            self._cleanup()
