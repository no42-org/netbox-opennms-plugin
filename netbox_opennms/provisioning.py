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
        wanted = {
            "origin": ServerOriginChoices.PROVISIONED,
            "url": entry["url"],
            "default_location": entry["default_location"],
            "username": "",
            "password": "",
            "headers": {},
        }
        row = OpenNMSServer.objects.filter(name=name).first()
        if row is None:
            row = OpenNMSServer.objects.create(name=name, **wanted)
        elif any(getattr(row, field) != value for field, value in wanted.items()):
            # Save only on drift: the server list view calls this on every
            # GET, and a no-op save would still write an ObjectChange.
            row.snapshot()
            for field, value in wanted.items():
                setattr(row, field, value)
            row.save()
        rows.append(row)
    return rows
