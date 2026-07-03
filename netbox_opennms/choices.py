# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Extensible choice sets for the plugin."""

from utilities.choices import ChoiceSet


class ServiceChoices(ChoiceSet):
    """OpenNMS service names declared on a Requisition or per-object override.

    A Requisition's ``services`` are applied to every member's interfaces; a
    Monitoring Override may add extra ``MonitoredService`` rows or suppress a
    declared default. ``key`` keeps the list admin-extensible
    (``FIELD_CHOICES['netbox_opennms.MonitoredService.name']``); the names must
    match OpenNMS's poller/service config server-side.
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


class ObjectTypeChoices(ChoiceSet):
    """Which NetBox object types a Requisition's filter draws members from."""

    DEVICE = "device"
    VM = "vm"
    BOTH = "both"

    CHOICES = [
        (DEVICE, "Devices only"),
        (VM, "Virtual machines only"),
        (BOTH, "Devices and virtual machines"),
    ]


class DetectorPresetChoices(ChoiceSet):
    """Provisioning detector presets a Requisition can select (Epic 5).

    Each key resolves (via ``presets.DETECTOR_PRESETS``) to an OpenNMS detector
    class + default parameters. Blank preset = freeform detector (user supplies the
    class). Admin-extensible via
    ``FIELD_CHOICES['netbox_opennms.MonitoringDetector.preset']``.
    """

    key = "MonitoringDetector.preset"

    CHOICES = [
        ("icmp", "ICMP"),
        ("snmp", "SNMP"),
        ("http", "HTTP"),
        ("https", "HTTPS"),
        ("ssh", "SSH"),
        ("dns", "DNS"),
        ("tcp", "TCP"),
    ]


class PolicyPresetChoices(ChoiceSet):
    """Provisioning policy presets a Requisition can select.

    One preset per built-in OpenNMS provisioning policy class (labels match the
    OpenNMS UI). Resolve via ``presets.POLICY_PRESETS``; the preset owns the
    rule class (the user cannot change it). Blank = freeform policy class.
    """

    key = "MonitoringPolicy.preset"

    CHOICES = [
        ("match-ip-interface", "Match IP Interface"),
        ("match-snmp-interface", "Match SNMP Interface"),
        ("script-policy", "Script Policy"),
        ("set-interface-metadata", "Set Interface Metadata"),
        ("set-node-category", "Set Node Category"),
        ("set-node-metadata", "Set Node Metadata"),
    ]


class InterfaceScopeChoices(ChoiceSet):
    """Which of a node's NetBox IPs become OpenNMS interfaces by default (Epic 5)."""

    PRIMARY = "primary"
    ALL = "all"

    CHOICES = [
        (PRIMARY, "Primary IP only"),
        (ALL, "All of the object's IPs"),
    ]


class InterfaceRoleChoices(ChoiceSet):
    """A node interface's SNMP role — OpenNMS ``snmp-primary`` P/S/N (RD-5).

    Primary (`P`) is the SNMP-primary interface (at most one per node); Secondary
    (`S`) is an eligible-secondary; Not-eligible (`N`) is never probed for SNMP.
    """

    PRIMARY = "P"
    SECONDARY = "S"
    NOT_ELIGIBLE = "N"

    CHOICES = [
        (PRIMARY, "Primary"),
        (SECONDARY, "Secondary"),
        (NOT_ELIGIBLE, "Not eligible"),
    ]


class MetadataScopeChoices(ChoiceSet):
    """Which requisition element a metadata entry attaches to (RD-3)."""

    NODE = "node"
    INTERFACE = "interface"
    SERVICE = "service"

    CHOICES = [
        (NODE, "Node"),
        (INTERFACE, "Interface"),
        (SERVICE, "Service"),
    ]


class NetBoxSourceChoices(ChoiceSet):
    """Curated NetBox attributes selectable as an asset/metadata value source (RD-2/3).

    Admin-extensible via ``FIELD_CHOICES`` — but a new key also needs a resolver in
    ``enrichment.CURATED``. Custom fields are referenced out-of-band as ``cf_<name>``.
    """

    key = "AssetMapping.netbox_source"

    CHOICES = [
        ("name", "Name"),
        ("serial", "Serial number"),
        ("asset_tag", "Asset tag"),
        ("model", "Device model"),
        ("manufacturer", "Manufacturer"),
        ("platform", "Platform"),
        ("role", "Role"),
        ("site", "Site"),
        ("rack", "Rack"),
        ("tenant", "Tenant"),
        ("description", "Description"),
        ("comments", "Comments"),
    ]
