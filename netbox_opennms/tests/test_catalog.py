# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Unit tests for the detector/policy catalog service (RD-1)."""

from unittest import mock

from django import forms as django_forms
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from netbox_opennms import catalog
from netbox_opennms import forms as onms_forms
from netbox_opennms.catalog import (
    ASSET_FIELDS,
    Catalog,
    CatalogEntry,
    CatalogParam,
)
from netbox_opennms.client import DiscoveredParam, DiscoveredPlugin, OpenNMSError
from netbox_opennms.models import MonitoringDetector, MonitoringPolicy, Requisition
from netbox_opennms.presets import DETECTOR_PRESETS, POLICY_PRESETS

ICMP_CLASS = DETECTOR_PRESETS["icmp"]["class"]
MIP_CLASS = POLICY_PRESETS["match-ip-interface"]["class"]


class FakeClient:
    """A stand-in port: returns canned plugins or raises, counts calls."""

    def __init__(self, detectors=None, raises=False):
        self._detectors = detectors or []
        self._raises = raises
        self.calls = 0

    def list_detectors(self):
        self.calls += 1
        if self._raises:
            raise OpenNMSError("offline")
        return self._detectors

    def close(self):
        pass


class CatalogMergeTest(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_merges_discovered_with_overlay(self):
        client = FakeClient(
            detectors=[
                DiscoveredPlugin(
                    name="ICMP",
                    plugin_class=ICMP_CLASS,
                    parameters=(DiscoveredParam(key="timeout"),),
                )
            ]
        )
        cat = catalog.get_detector_catalog(client=client)
        self.assertFalse(cat.live_unavailable)
        entry = cat.by_class(ICMP_CLASS)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.source, "discovered")
        self.assertEqual(entry.preset_key, "icmp")
        timeout = next(p for p in entry.parameters if p.key == "timeout")
        # overlay supplies the label + default the REST catalog doesn't return
        self.assertEqual(timeout.label, "Timeout (ms)")
        self.assertEqual(timeout.default, "2000")

    def test_discovered_options_and_required_surface(self):
        client = FakeClient(
            detectors=[
                DiscoveredPlugin(
                    name="X",
                    plugin_class="org.opennms.XDetector",
                    parameters=(
                        DiscoveredParam(
                            key="mode", required=True, options=("A", "B")
                        ),
                    ),
                )
            ]
        )
        entry = catalog.get_detector_catalog(client=client).by_class(
            "org.opennms.XDetector"
        )
        mode = entry.parameters[0]
        self.assertTrue(mode.required)
        self.assertEqual(mode.options, ("A", "B"))

    def test_overlay_only_preset_offered_when_not_discovered(self):
        # OpenNMS reachable but reports no detectors → overlay presets still offered.
        cat = catalog.get_detector_catalog(client=FakeClient(detectors=[]))
        self.assertFalse(cat.live_unavailable)
        icmp = cat.by_preset("icmp")
        self.assertIsNotNone(icmp)
        self.assertEqual(icmp.source, "overlay")

    def test_degrades_to_overlay_when_offline(self):
        client = FakeClient(raises=True)
        cat = catalog.get_detector_catalog(client=client)
        self.assertTrue(cat.live_unavailable)
        # Overlay presets are still usable while degraded.
        self.assertIsNotNone(cat.by_preset("icmp"))

    def test_degraded_result_is_cached_briefly(self):
        client = FakeClient(raises=True)
        catalog.get_detector_catalog(client=client)
        catalog.get_detector_catalog(client=client)
        # A degraded result is cached briefly, so the second read is served from
        # cache — a down OpenNMS is not re-hit on every call.
        self.assertEqual(client.calls, 1)

    def test_successful_result_is_cached(self):
        client = FakeClient(detectors=[])
        catalog.get_detector_catalog(client=client)
        catalog.get_detector_catalog(client=client)
        self.assertEqual(client.calls, 1)  # second read served from cache

    def test_refresh_clears_cache(self):
        client = FakeClient(detectors=[])
        catalog.get_detector_catalog(client=client)
        catalog.refresh_catalogs()
        catalog.get_detector_catalog(client=client)
        self.assertEqual(client.calls, 2)


class AssetFieldsTest(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_discovered_asset_fields(self):
        client = mock.Mock()
        client.list_assets.return_value = {"serialNumber", "customField"}
        self.assertEqual(
            catalog.get_asset_fields(client=client), {"serialNumber", "customField"}
        )

    def test_degrades_to_constant_when_offline(self):
        client = mock.Mock()
        client.list_assets.side_effect = OpenNMSError("offline")
        self.assertEqual(catalog.get_asset_fields(client=client), ASSET_FIELDS)


class PresetSeedTest(TestCase):
    """The overlay still seeds defaults on save (unchanged _apply_preset)."""

    def test_apply_preset_seeds_class_and_defaults(self):
        req = Requisition.objects.create(
            name="r-seed", filter_params={"role": ["switch"]}
        )
        detector = MonitoringDetector.objects.create(
            requisition=req, name="icmp", preset="icmp"
        )
        self.assertEqual(detector.rule_class, ICMP_CLASS)
        self.assertEqual(detector.parameters, {"timeout": "2000", "retries": "1"})


def _policy_catalog(live_unavailable=False):
    """A catalog with one policy carrying an enum + a text parameter."""
    entry = CatalogEntry(
        name="Match IP Interface",
        plugin_class=MIP_CLASS,
        preset_key="match-ip-interface",
        source="discovered",
        parameters=[
            CatalogParam(
                key="action",
                required=True,
                options=("DO_NOT_PERSIST", "UNMANAGE"),
                label="Action",
                default="DO_NOT_PERSIST",
            ),
            CatalogParam(
                key="matchBehavior", label="Match behavior", default="ALL_PARAMETERS"
            ),
        ],
    )
    return Catalog(entries=[entry], live_unavailable=live_unavailable)


class PresetRuleFormTest(TestCase):
    """The detector/policy form renders parameters from the catalog (RD-1)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.req = Requisition.objects.create(
            name="form-req", filter_params={"role": ["switch"]}
        )

    @mock.patch.object(onms_forms, "get_policy_catalog")
    def test_enum_param_is_dropdown_text_param_is_charfield(self, mock_cat):
        mock_cat.return_value = _policy_catalog()
        policy = MonitoringPolicy.objects.create(
            requisition=self.req, name="mip", preset="match-ip-interface"
        )
        form = onms_forms.MonitoringPolicyForm(instance=policy)
        self.assertIsInstance(form.fields["param_action"], django_forms.ChoiceField)
        self.assertIn(("UNMANAGE", "UNMANAGE"), form.fields["param_action"].choices)
        self.assertIsInstance(
            form.fields["param_matchBehavior"], django_forms.CharField
        )
        # The raw JSON field is hidden; the overlay default seeds the widget.
        self.assertNotIn("parameters", form.fields)
        self.assertEqual(form.fields["param_action"].initial, "DO_NOT_PERSIST")

    @mock.patch.object(onms_forms, "get_policy_catalog")
    def test_submit_assembles_parameters(self, mock_cat):
        mock_cat.return_value = _policy_catalog()
        policy = MonitoringPolicy.objects.create(
            requisition=self.req, name="mip", preset="match-ip-interface"
        )
        form = onms_forms.MonitoringPolicyForm(
            data={
                "requisition": self.req.pk,
                "name": "mip",
                "preset": "match-ip-interface",
                "param_action": "UNMANAGE",
                "param_matchBehavior": "ANY_PARAMETER",
            },
            instance=policy,
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(
            obj.parameters, {"action": "UNMANAGE", "matchBehavior": "ANY_PARAMETER"}
        )

    @mock.patch.object(onms_forms, "get_policy_catalog")
    def test_live_unavailable_notes_and_still_renders_overlay(self, mock_cat):
        mock_cat.return_value = _policy_catalog(live_unavailable=True)
        policy = MonitoringPolicy.objects.create(
            requisition=self.req, name="mip", preset="match-ip-interface"
        )
        form = onms_forms.MonitoringPolicyForm(instance=policy)
        self.assertIn("unavailable", str(form.fields["rule_class"].help_text).lower())
        self.assertIn("param_action", form.fields)

    @mock.patch.object(onms_forms, "get_policy_catalog")
    def test_freeform_rule_keeps_json_field(self, mock_cat):
        mock_cat.return_value = Catalog(entries=[], live_unavailable=False)
        policy = MonitoringPolicy.objects.create(
            requisition=self.req, name="freeform", rule_class="org.example.CustomPolicy"
        )
        form = onms_forms.MonitoringPolicyForm(instance=policy)
        self.assertIn("parameters", form.fields)
        self.assertFalse(any(k.startswith("param_") for k in form.fields))

    @mock.patch.object(onms_forms, "get_policy_catalog")
    def test_edit_preserves_unsurfaced_stored_param(self, mock_cat):
        mock_cat.return_value = _policy_catalog()
        policy = MonitoringPolicy.objects.create(
            requisition=self.req, name="mip", preset="match-ip-interface"
        )
        # A stored key the catalog entry does NOT surface as a field (e.g. set via
        # the API, or by a richer prior catalog) must survive an edit+save.
        policy.parameters["extraKey"] = "keepme"
        policy.save()
        form = onms_forms.MonitoringPolicyForm(
            data={
                "requisition": self.req.pk,
                "name": "mip",
                "preset": "match-ip-interface",
                "param_action": "UNMANAGE",
                "param_matchBehavior": "ANY_PARAMETER",
            },
            instance=policy,
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.assertEqual(obj.parameters.get("extraKey"), "keepme")
        self.assertEqual(obj.parameters.get("action"), "UNMANAGE")

    @mock.patch.object(onms_forms, "get_detector_catalog")
    def test_blank_add_form_does_not_fetch_catalog(self, mock_cat):
        # A blank add form (no preset, no class) must not hit OpenNMS.
        onms_forms.MonitoringDetectorForm()
        mock_cat.assert_not_called()
