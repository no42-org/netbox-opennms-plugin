# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Extensible choice sets for the plugin."""

from utilities.choices import ChoiceSet


class ServiceChoices(ChoiceSet):
    """OpenNMS service names monitored on an interface (AD-11 — explicit only).

    ``key`` makes the list admin-extensible without code: set
    ``FIELD_CHOICES['netbox_opennms.MonitoredService.name']`` to replace the
    defaults, or ``'netbox_opennms.MonitoredService.name+'`` to append. The
    chosen names must match OpenNMS's poller/service config server-side.
    """

    key = "MonitoredService.name"

    CHOICES = [
        ("ICMP", "ICMP"),
        ("SNMP", "SNMP"),
        ("HTTP", "HTTP"),
        ("HTTPS", "HTTPS"),
        ("SSH", "SSH"),
        ("DNS", "DNS"),
        ("NTP", "NTP"),
    ]
