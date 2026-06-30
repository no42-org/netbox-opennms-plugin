# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Detector/policy preset registry (Epic 5).

Maps a preset key (see ``choices.DetectorPresetChoices`` / ``PolicyPresetChoices``)
to its OpenNMS class and default parameters. A Monitoring Profile stores the
resolved class + parameters, so a profile stays self-contained even if a preset
later changes.

WARNING: these class names and parameters are an OpenNMS-version contract. They
are a first cut and MUST be confirmed by the live ``make integration`` round-trip
against the target Horizon version before they ship (Epic 5, Story 5.2) — the
`:`-delimiter bug proved a unit test cannot catch a wire-contract mismatch.

``parameters`` values are strings (OpenNMS ``<parameter value="…">`` is text).
``schema`` lists the parameter keys a preset exposes in the UI (key, label,
default) — informational here; the form layer (Story 5.4) consumes it.
"""

_DETECTOR = "org.opennms.netmgt.provision.detector"
_POLICY = "org.opennms.netmgt.provision.persist.policies"

# preset key -> {class, parameters (defaults), schema}
DETECTOR_PRESETS = {
    "icmp": {
        "class": f"{_DETECTOR}.icmp.IcmpDetector",
        "parameters": {"timeout": "2000", "retries": "1"},
        "schema": [("timeout", "Timeout (ms)", "2000"), ("retries", "Retries", "1")],
    },
    "snmp": {
        # Uses the node's configured SNMP agent settings; no params by default.
        "class": f"{_DETECTOR}.snmp.SnmpDetector",
        "parameters": {},
        "schema": [],
    },
    "http": {
        "class": f"{_DETECTOR}.simple.HttpDetector",
        "parameters": {"port": "80", "url": "/", "response": "100-499"},
        "schema": [("port", "Port", "80"), ("url", "URL", "/")],
    },
    "https": {
        "class": f"{_DETECTOR}.simple.HttpsDetector",
        "parameters": {"port": "443"},
        "schema": [("port", "Port", "443")],
    },
    "ssh": {
        "class": f"{_DETECTOR}.ssh.SshDetector",
        "parameters": {"port": "22"},
        "schema": [("port", "Port", "22")],
    },
    "dns": {
        "class": f"{_DETECTOR}.dns.DnsDetector",
        "parameters": {"port": "53", "lookup": "localhost"},
        "schema": [("port", "Port", "53"), ("lookup", "Lookup", "localhost")],
    },
    "tcp": {
        "class": f"{_DETECTOR}.simple.TcpDetector",
        "parameters": {"banner": "*"},
        "schema": [("port", "Port", ""), ("banner", "Banner regex", "*")],
    },
}

# preset key -> {class, parameters (defaults incl. matchBehavior), schema}
_MATCH = ("matchBehavior", "Match behavior", "ALL_PARAMETERS")
POLICY_PRESETS = {
    "set-category": {
        "class": f"{_POLICY}.NodeCategorySettingPolicy",
        "parameters": {"matchBehavior": "ALL_PARAMETERS"},
        "schema": [("category", "Category", ""), _MATCH],
    },
    "manage-ip-interfaces": {
        "class": f"{_POLICY}.MatchingIpInterfacePolicy",
        "parameters": {
            "action": "DO_NOT_PERSIST",
            "matchBehavior": "ALL_PARAMETERS",
        },
        "schema": [("action", "Action", "DO_NOT_PERSIST"), _MATCH],
    },
    "snmp-collection": {
        "class": f"{_POLICY}.MatchingSnmpInterfacePolicy",
        "parameters": {
            "action": "DISABLE_COLLECTION",
            "matchBehavior": "ALL_PARAMETERS",
        },
        "schema": [("action", "Action", "DISABLE_COLLECTION"), _MATCH],
    },
}


def resolve_detector(preset):
    """The (class, default-parameters) for a detector preset key, or (None, {})."""
    spec = DETECTOR_PRESETS.get(preset)
    return (spec["class"], dict(spec["parameters"])) if spec else (None, {})


def resolve_policy(preset):
    """The (class, default-parameters) for a policy preset key, or (None, {})."""
    spec = POLICY_PRESETS.get(preset)
    return (spec["class"], dict(spec["parameters"])) if spec else (None, {})
