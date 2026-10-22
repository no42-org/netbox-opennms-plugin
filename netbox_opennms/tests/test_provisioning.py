# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Tests for reconciling PLUGINS_CONFIG servers into locked rows."""

from unittest import mock

from django.db.models.signals import post_migrate
from django.test import TestCase

from netbox_opennms.models import OpenNMSServer
from netbox_opennms.provisioning import reconcile_provisioned_servers
from netbox_opennms.signals import on_post_migrate
from netbox_opennms.tests.servers import make_server

ENTRY = {
    "url": "https://p/opennms",
    "username": "u",
    "password": "p",
    "default_location": "Lab",
    "headers": {},
}
PATCH = "netbox_opennms.provisioning.config.server_entries"


class ReconcileProvisionedServersTest(TestCase):
    def test_creates_provisioned_rows(self):
        with mock.patch(PATCH, return_value={"prod": ENTRY}):
            rows = reconcile_provisioned_servers()
        row = OpenNMSServer.objects.get(name="prod")
        self.assertEqual(
            (row.origin, row.url, row.default_location, row.password),
            ("provisioned", "https://p/opennms", "Lab", ""),
        )
        self.assertEqual(rows, [row])

    def test_takes_over_same_named_local_row(self):
        make_server("prod", password="local-secret")
        with mock.patch(PATCH, return_value={"prod": ENTRY}):
            with self.assertLogs("netbox_opennms", level="WARNING") as logs:
                reconcile_provisioned_servers()
        row = OpenNMSServer.objects.get(name="prod")
        self.assertEqual(
            (row.origin, row.password, row.username), ("provisioned", "", "")
        )
        self.assertEqual(
            logs.output,
            [
                "WARNING:netbox_opennms:Provisioned server 'prod' takes over a "
                "local row; its stored credentials are discarded."
            ],
        )

    def test_entry_on_the_url_of_a_local_row_is_skipped(self):
        make_server("local", url="HTTPS://P/opennms/")
        with mock.patch(PATCH, return_value={"prod": ENTRY}):
            with self.assertLogs("netbox_opennms", level="WARNING") as logs:
                rows = reconcile_provisioned_servers()
        self.assertEqual(rows, [])
        self.assertFalse(OpenNMSServer.objects.filter(name="prod").exists())
        self.assertIn("same OpenNMS as server 'local'", logs.output[0])

    def test_second_entry_on_the_same_url_is_skipped(self):
        entries = {"a": ENTRY, "b": {**ENTRY, "url": "https://p/opennms/"}}
        with mock.patch(PATCH, return_value=entries):
            with self.assertLogs("netbox_opennms", level="WARNING") as logs:
                reconcile_provisioned_servers()
        self.assertEqual(
            list(OpenNMSServer.objects.values_list("name", flat=True)), ["a"]
        )
        self.assertIn("'b' points at the same OpenNMS as server 'a'", logs.output[0])

    def test_removed_entry_keeps_row(self):
        OpenNMSServer.objects.create(name="old", origin="provisioned", url="https://o/")
        with mock.patch(PATCH, return_value={}):
            reconcile_provisioned_servers()
        self.assertTrue(OpenNMSServer.objects.filter(name="old").exists())

    def test_entry_without_url_or_credentials_is_skipped(self):
        entries = {"nourl": {**ENTRY, "url": ""}, "nopw": {**ENTRY, "password": ""}}
        with mock.patch(PATCH, return_value=entries):
            with self.assertLogs("netbox_opennms", level="WARNING") as logs:
                reconcile_provisioned_servers()
        self.assertEqual(OpenNMSServer.objects.count(), 0)
        self.assertEqual(len(logs.records), 2)

    def test_unchanged_row_is_not_saved(self):
        with mock.patch(PATCH, return_value={"prod": ENTRY}):
            reconcile_provisioned_servers()
            with mock.patch.object(OpenNMSServer, "save") as save:
                reconcile_provisioned_servers()
        save.assert_not_called()

    def test_post_migrate_signal_is_connected(self):
        receivers = [r[1]() if callable(r[1]) else r[1] for r in post_migrate.receivers]
        self.assertIn(on_post_migrate, receivers)
