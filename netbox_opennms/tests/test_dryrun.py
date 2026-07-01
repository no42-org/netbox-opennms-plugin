# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the pure dry-run differ (Requisition redesign, R7)."""

from django.test import SimpleTestCase

from netbox_opennms.dryrun import diff
from netbox_opennms.membership import InterfaceSpec, NodeSpec, Resolution


class _Rules:
    def __init__(self, items=()):
        self._items = list(items)

    def all(self):
        return self._items


class _Req:
    def __init__(self, scan_interval="1d"):
        self.scan_interval = scan_interval
        self.detectors = _Rules()
        self.policies = _Rules()


def _resolution(nodes):
    return Resolution("fs", _Req(), nodes=nodes, warnings=[])


def _node(ip="10.0.0.1", services=("ICMP",)):
    return NodeSpec(
        "rtr-1", "device-1", "",
        [InterfaceSpec(ip, True, services=list(services))],
    )


def _current(ip="10.0.0.1", services=("ICMP",)):
    return {
        "node": [
            {
                "foreign-id": "device-1",
                "node-label": "rtr-1",
                "interface": [
                    {
                        "ip-addr": ip,
                        "snmp-primary": "P",
                        "monitored-service": [{"service-name": s} for s in services],
                    }
                ],
            }
        ]
    }


class DryRunDiffTest(SimpleTestCase):
    def test_empty_diff_on_identical(self):
        result = diff(_resolution([_node()]), _current(), {"scan-interval": "1d"})
        self.assertFalse(result.has_changes)
        self.assertEqual(result.unchanged, 1)

    def test_never_synced_is_all_added(self):
        result = diff(_resolution([_node()]), None, None)
        self.assertFalse(result.exists)
        self.assertEqual([n.foreign_id for n in result.added], ["device-1"])

    def test_management_ip_change(self):
        result = diff(
            _resolution([_node(ip="10.0.0.1")]),
            _current(ip="10.0.0.9"),
            {"scan-interval": "1d"},
        )
        self.assertEqual(len(result.changed), 1)
        self.assertTrue(
            any("management IP" in c for c in result.changed[0].changes)
        )

    def test_service_change(self):
        result = diff(
            _resolution([_node(services=("ICMP", "SNMP"))]),
            _current(services=("ICMP",)),
            {"scan-interval": "1d"},
        )
        self.assertEqual(len(result.changed), 1)

    def test_removed_node(self):
        result = diff(_resolution([]), _current(), {"scan-interval": "1d"})
        self.assertEqual([n.foreign_id for n in result.removed], ["device-1"])

    def test_definition_scan_interval_change(self):
        result = diff(_resolution([_node()]), _current(), {"scan-interval": "30m"})
        self.assertTrue(
            any("scan-interval" in c for c in result.definition_changes)
        )

    def test_blank_location_matches_configured_default(self):
        # Node location blank + OpenNMS holds the configured default_location →
        # not a change (the renderer substitutes the default).
        current = _current()
        current["node"][0]["location"] = "Default"
        result = diff(
            _resolution([_node()]), current, {"scan-interval": "1d"},
            default_location="Default",
        )
        self.assertFalse(result.has_changes)

    def test_single_element_json_not_mislabeled(self):
        # OpenNMS v1 REST may serialize a lone node/interface/service as a bare
        # object rather than a list — an in-sync node must not read as "added".
        current = {
            "node": {
                "foreign-id": "device-1",
                "node-label": "rtr-1",
                "interface": {
                    "ip-addr": "10.0.0.1",
                    "snmp-primary": "P",
                    "monitored-service": {"service-name": "ICMP"},
                },
            }
        }
        result = diff(_resolution([_node()]), current, {"scan-interval": "1d"})
        self.assertEqual(result.added, [])
        self.assertEqual(result.unchanged, 1)
