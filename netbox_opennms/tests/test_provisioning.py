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
            reconcile_provisioned_servers()
        row = OpenNMSServer.objects.get(name="prod")
        self.assertEqual(
            (row.origin, row.password, row.username), ("provisioned", "", "")
        )

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
