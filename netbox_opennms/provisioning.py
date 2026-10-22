# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Reconcile PLUGINS_CONFIG server entries into locked database rows."""

import logging

import netbox_opennms.config as config

from .choices import ServerOriginChoices
from .models import OpenNMSServer

logger = logging.getLogger("netbox_opennms")


def reconcile_provisioned_servers():
    """Upsert one provisioned row per config entry. Never deletes or demotes rows."""
    rows = []
    for name, entry in config.server_entries().items():
        if not (entry["url"] and entry["username"] and entry["password"]):
            logger.warning(
                f"Provisioned server {name!r} skipped: missing url or credentials."
            )
            continue
        row, _ = OpenNMSServer.objects.update_or_create(
            name=name,
            defaults={
                "origin": ServerOriginChoices.PROVISIONED,
                "url": entry["url"],
                "default_location": entry["default_location"],
                "username": "",
                "password": "",
                "headers": {},
            },
        )
        rows.append(row)
    return rows
