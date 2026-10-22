# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Test helper: the one place that knows the default test server values."""

from netbox_opennms.choices import ServerOriginChoices
from netbox_opennms.models import OpenNMSServer


def make_server(name="default", **overrides):
    """Create a ``LOCAL`` ``OpenNMSServer`` called ``name``."""
    fields = {
        "origin": ServerOriginChoices.LOCAL,
        "url": "https://onms.example.org/opennms",
        "username": "svc",
        "password": "pw",
        **overrides,
    }
    return OpenNMSServer.objects.create(name=name, **fields)
