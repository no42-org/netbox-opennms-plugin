# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for the PLUGINS_CONFIG ``servers`` loader."""

from unittest import mock

from django.test import SimpleTestCase

from netbox_opennms.config import (
    normalised_url,
    server_entries,
    server_entry,
    upgrade_target,
)


def _cfg(values):
    return lambda name, key: values.get(key, {} if key == "servers" else "")


def _patch(values):
    return mock.patch(
        "netbox_opennms.config.get_plugin_config", side_effect=_cfg(values)
    )


class ServerEntriesTest(SimpleTestCase):
    def test_flat_keys_synthesise_default(self):
        with _patch({
                    "opennms_url": "https://a/opennms",
                    "opennms_username": "u",
                    "opennms_password": "p",
                    "default_location": "Default",
                }):
            entries = server_entries()
        self.assertEqual(
            entries,
            {
                "default": {
                    "url": "https://a/opennms",
                    "username": "u",
                    "password": "p",
                    "default_location": "Default",
                    "headers": {},
                }
            },
        )

    def test_explicit_default_entry_wins_over_flat_keys(self):
        with _patch({"opennms_url": "https://flat/", "servers": {"default": {"url": "https://m/"}}}):
            self.assertEqual(server_entries()["default"]["url"], "https://m/")

    def test_entry_is_normalised(self):
        with _patch({"servers": {"prod": {"url": "https://p/"}}}):
            self.assertEqual(
                server_entries()["prod"],
                {
                    "url": "https://p/",
                    "username": "",
                    "password": "",
                    "default_location": "",
                    "headers": {},
                },
            )

    def test_missing_entry_is_none(self):
        with _patch({}):
            self.assertIsNone(server_entry("nope"))

    def test_server_entry_returns_entry(self):
        with _patch({"servers": {"prod": {"url": "https://p/"}}}):
            self.assertEqual(server_entry("prod")["url"], "https://p/")

    def test_returns_copy_callers_cannot_mutate(self):
        raw = {"prod": {"url": "https://p/", "headers": {"X": "1"}}}
        with _patch({"servers": raw}):
            server_entries()["prod"]["headers"]["Y"] = "2"
            self.assertEqual(server_entries()["prod"]["headers"], {"X": "1"})
        self.assertEqual(raw["prod"]["headers"], {"X": "1"})

    def test_malformed_values_are_tolerated(self):
        ok = {"url": None, "username": 5, "headers": "x"}
        with _patch({"servers": {"bad": "nope", "ok": ok}}):
            entries = server_entries()
        self.assertNotIn("bad", entries)
        self.assertEqual(entries["ok"]["url"], "")
        self.assertEqual(entries["ok"]["username"], "5")
        self.assertEqual(entries["ok"]["headers"], {})

    def test_header_keys_and_values_are_coerced_to_text(self):
        # requests raises InvalidHeader for a non-str value at request time.
        headers = {"X-Port": 8980, 7: "seven", "X-None": None, None: "v"}
        with _patch({"servers": {"prod": {"url": "https://p/", "headers": headers}}}):
            self.assertEqual(
                server_entries()["prod"]["headers"],
                {"X-Port": "8980", "7": "seven"},
            )

    def test_normalised_url_ignores_trailing_slash_and_case(self):
        self.assertEqual(
            normalised_url("HTTPS://ONMS.Example.org/opennms/"),
            normalised_url("https://onms.example.org/opennms"),
        )
        self.assertNotEqual(
            normalised_url("https://onms.example.org/OpenNMS"),
            normalised_url("https://onms.example.org/opennms"),
        )

    def test_non_dict_servers_is_empty(self):
        with _patch({"servers": ["x"]}):
            self.assertEqual(server_entries(), {})


class UpgradeTargetTest(SimpleTestCase):
    def test_flat_keys(self):
        self.assertEqual(
            upgrade_target({"opennms_url": "https://a/", "default_location": "L"}),
            ("default", "https://a/", "L"),
        )

    def test_single_servers_entry(self):
        self.assertEqual(
            upgrade_target({"servers": {"prod": {"url": "https://p/"}}}),
            ("prod", "https://p/", ""),
        )

    def test_no_config(self):
        self.assertEqual(upgrade_target({}), ("default", "", ""))

    def test_several_entries_without_flat_keys(self):
        self.assertEqual(
            upgrade_target({"servers": {"a": {"url": "x"}, "b": {"url": "y"}}}),
            ("default", "", ""),
        )
