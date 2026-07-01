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
        # TcpDetector has no default port — the user MUST supply which port to probe.
        "required": ["port"],
    },
}

# preset key -> {class, parameters (defaults incl. matchBehavior), schema}
# One entry per built-in OpenNMS provisioning policy class. The class names +
# parameters are an OpenNMS-version contract (Horizon 36) — a first cut that MUST
# be confirmed by the live ``make integration`` round-trip before shipping.
_MATCH = ("matchBehavior", "Match behavior", "ALL_PARAMETERS")
POLICY_PRESETS = {
    "match-ip-interface": {
        "class": f"{_POLICY}.MatchingIpInterfacePolicy",
        "parameters": {
            "action": "DO_NOT_PERSIST",
            "matchBehavior": "ALL_PARAMETERS",
        },
        "schema": [("action", "Action", "DO_NOT_PERSIST"), _MATCH],
    },
    "match-snmp-interface": {
        "class": f"{_POLICY}.MatchingSnmpInterfacePolicy",
        "parameters": {
            "action": "DISABLE_COLLECTION",
            "matchBehavior": "ALL_PARAMETERS",
        },
        "schema": [("action", "Action", "DISABLE_COLLECTION"), _MATCH],
    },
    "script-policy": {
        "class": f"{_POLICY}.ScriptPolicy",
        "parameters": {},
        "schema": [("script", "Script name", "")],
        # ScriptPolicy runs a named provisioning script — no default; require it.
        "required": ["script"],
    },
    "set-interface-metadata": {
        "class": f"{_POLICY}.InterfaceMetadataSettingPolicy",
        "parameters": {"matchBehavior": "ALL_PARAMETERS"},
        "schema": [
            ("metadataKey", "Metadata key", ""),
            ("metadataValue", "Metadata value", ""),
            _MATCH,
        ],
        "required": ["metadataKey", "metadataValue"],
    },
    "set-node-category": {
        "class": f"{_POLICY}.NodeCategorySettingPolicy",
        "parameters": {"matchBehavior": "ALL_PARAMETERS"},
        "schema": [("category", "Category", ""), _MATCH],
        # The category to assign is node/site-specific — no default; require it.
        "required": ["category"],
    },
    "set-node-metadata": {
        "class": f"{_POLICY}.NodeMetadataSettingPolicy",
        "parameters": {"matchBehavior": "ALL_PARAMETERS"},
        "schema": [
            ("metadataKey", "Metadata key", ""),
            ("metadataValue", "Metadata value", ""),
            _MATCH,
        ],
        "required": ["metadataKey", "metadataValue"],
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


def detector_required_params(preset):
    """Param keys a detector preset's class needs but can't default (or ``[]``)."""
    spec = DETECTOR_PRESETS.get(preset)
    return list(spec.get("required", [])) if spec else []


def policy_required_params(preset):
    """Param keys a policy preset's class needs but can't default (or ``[]``)."""
    spec = POLICY_PRESETS.get(preset)
    return list(spec.get("required", [])) if spec else []
