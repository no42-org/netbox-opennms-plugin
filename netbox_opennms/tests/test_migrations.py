# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Executed test of the 0007 data step: existing rows get one server.

Runs the real migration with ``MigrationExecutor`` from 0006 to 0007 for each
config shape the upgrade note documents, then back to 0006. A
``TransactionTestCase``, because each migration runs in its own transaction as
it does in ``manage.py migrate``.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

APP = "netbox_opennms"
BEFORE = [(APP, "0006_assetmapping_metadataentry")]
AFTER = [(APP, "0007_opennmsserver")]


class ServerMigrationTest(TransactionTestCase):
    def _migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def setUp(self):
        apps = self._migrate(BEFORE)
        apps.get_model(APP, "Requisition").objects.create(
            name="r1", filter_params={"site": ["x"]}
        )
        apps.get_model(APP, "Requisition").objects.create(
            name="r2", filter_params={"site": ["y"]}
        )
        apps.get_model(APP, "DeployedForeignSource").objects.create(name="r1")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes(APP))

    def _upgrade(self, plugin_config):
        with override_settings(PLUGINS_CONFIG={APP: plugin_config}):
            return self._migrate(AFTER)

    def _assert_bound_to(self, apps, name, url, default_location):
        servers = list(apps.get_model(APP, "OpenNMSServer").objects.all())
        self.assertEqual(len(servers), 1)
        server = servers[0]
        self.assertEqual(
            (server.name, server.origin, server.url, server.default_location),
            (name, "provisioned", url, default_location),
        )
        for model in ("Requisition", "DeployedForeignSource"):
            rows = apps.get_model(APP, model).objects.all()
            self.assertTrue(rows.exists(), model)
            self.assertEqual(
                set(rows.values_list("server_id", flat=True)), {server.pk}, model
            )

    def test_flat_keys(self):
        apps = self._upgrade(
            {"opennms_url": "https://flat/opennms", "default_location": "Lab"}
        )
        self._assert_bound_to(apps, "default", "https://flat/opennms", "Lab")

    def test_single_servers_entry(self):
        apps = self._upgrade(
            {"servers": {"prod": {"url": "https://prod/opennms",
                                  "default_location": "DC1"}}}
        )
        self._assert_bound_to(apps, "prod", "https://prod/opennms", "DC1")

    def test_several_entries_without_flat_keys(self):
        apps = self._upgrade(
            {"servers": {"a": {"url": "https://a/"}, "b": {"url": "https://b/"}}}
        )
        self._assert_bound_to(apps, "default", "", "")

    def test_migration_is_reversible(self):
        self._upgrade({"opennms_url": "https://flat/opennms"})
        apps = self._migrate(BEFORE)
        names = apps.get_model(APP, "Requisition").objects.values_list(
            "name", flat=True
        )
        self.assertEqual(sorted(names), ["r1", "r2"])
        self.assertNotIn(
            "netbox_opennms_opennmsserver", connection.introspection.table_names()
        )
