# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for pre-push resolution validation (Requisition redesign)."""

from django.test import SimpleTestCase

from netbox_opennms.membership import Conflict, NodeSpec, Resolution
from netbox_opennms.validation import validate_resolution


class _Requisition:
    def __init__(self, location=""):
        self.location = location


class ValidateResolutionTest(SimpleTestCase):
    def test_none_resolution_is_clean(self):
        result = validate_resolution(None)
        self.assertTrue(result.ok)
        self.assertEqual(result.errors, [])

    def test_warnings_forwarded(self):
        resolution = Resolution(
            "fs", _Requisition(), nodes=[], warnings=["rtr-x: no management IP"]
        )
        result = validate_resolution(resolution)
        self.assertTrue(result.ok)
        self.assertEqual(result.warnings, ["rtr-x: no management IP"])

    def test_invalid_requisition_location_is_error(self):
        resolution = Resolution("fs", _Requisition(location="bad name"), nodes=[])
        result = validate_resolution(resolution)
        self.assertFalse(result.ok)
        self.assertTrue(
            any("invalid requisition location" in e for e in result.errors)
        )

    def test_invalid_node_location_is_error(self):
        node = NodeSpec("rtr-1", "device-1", location="bad name", interfaces=[])
        resolution = Resolution("fs", _Requisition(), nodes=[node])
        result = validate_resolution(resolution)
        self.assertFalse(result.ok)
        self.assertTrue(any("rtr-1: invalid location" in e for e in result.errors))

    def test_valid_location_ok(self):
        node = NodeSpec("rtr-1", "device-1", location="edge-1", interfaces=[])
        resolution = Resolution("fs", _Requisition(location="core"), nodes=[node])
        self.assertTrue(validate_resolution(resolution).ok)

    def test_conflict_is_a_blocking_error(self):
        # C1: conflicts are errors (freeze), never warnings.
        resolution = Resolution(
            "fs",
            _Requisition(),
            conflicts=[Conflict("rtr-1", "device-1", ["a", "b"])],
        )
        result = validate_resolution(resolution)
        self.assertFalse(result.ok)
        self.assertTrue(any("resolve the overlap" in e for e in result.errors))

    def test_conflict_errors_are_bounded(self):
        # Review #5: a broad overlap must not flood messages — first N + summary.
        conflicts = [
            Conflict(f"d{i}", f"device-{i}", ["a", "b"]) for i in range(8)
        ]
        resolution = Resolution("fs", _Requisition(), conflicts=conflicts)
        result = validate_resolution(resolution)
        self.assertEqual(len(result.errors), 6)  # 5 detailed + 1 summary
        self.assertIn("3 more conflicted", result.errors[-1])
