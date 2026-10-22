# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Reconcile PLUGINS_CONFIG server entries into locked database rows."""

import logging

import netbox_opennms.config as config

from .choices import ServerOriginChoices
from .models import OpenNMSServer

logger = logging.getLogger("netbox_opennms")


def _same_instance_row(name, url):
    """Another row (not called ``name``) that points at the OpenNMS at ``url``."""
    wanted = config.normalised_url(url)
    for other in OpenNMSServer.objects.exclude(name=name):
        if config.normalised_url(other.effective_url) == wanted:
            return other
    return None


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
        drifted = row is not None and any(
            getattr(row, field) != value for field, value in wanted.items()
        )
        if row is None or drifted:
            # This bypasses OpenNMSServer.clean(), so check its one-row-per-
            # OpenNMS rule here. A clash never creates a second row.
            clash = _same_instance_row(name, entry["url"])
            if clash is not None:
                logger.warning(
                    f"Provisioned server {name!r} points at the same OpenNMS as "
                    f"server {clash.name!r}; each OpenNMS instance needs one "
                    "server." + (" Row not created." if row is None else "")
                )
                if row is None:
                    continue
        if row is None:
            row = OpenNMSServer.objects.create(name=name, **wanted)
        elif drifted:
            # Save only on drift: the server list view calls this on every
            # GET, and a no-op save would still write an ObjectChange.
            if row.origin == ServerOriginChoices.LOCAL:
                logger.warning(
                    f"Provisioned server {name!r} takes over a local row; its "
                    "stored credentials are discarded."
                )
            row.snapshot()
            for field, value in wanted.items():
                setattr(row, field, value)
            row.save()
        rows.append(row)
    return rows
