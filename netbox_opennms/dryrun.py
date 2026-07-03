# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Dry-run diff of a Requisition against the live OpenNMS state (R7).

Before Sync, compare the rendered intent (``membership.resolve`` → nodes, plus the
Requisition's definition) against what OpenNMS currently holds, per node
(add / remove / change) and for the foreign-source definition
(detectors / policies / scan-interval). The differ is **pure** given the two
parsed OpenNMS JSON documents (the fetch is a thin wrapper), so it is unit-testable
without a live server. A never-synced Foreign Source (current = ``None``) reads as
an all-added diff (M5).

The exact OpenNMS JSON shapes are a wire contract confirmed by ``make integration``;
parsing is deliberately tolerant.
"""

from dataclasses import dataclass, field

from netbox.plugins import get_plugin_config

from .choices import InterfaceRoleChoices
from .client import OpenNMSClient
from .membership import resolve

PLUGIN_NAME = "netbox_opennms"


def _as_list(value, key=None):
    """Normalize an OpenNMS JSON collection to a list.

    OpenNMS's v1 REST serializer unwraps a single-element collection into a bare
    object, so ``node`` / ``interface`` / ``detectors`` may arrive as a dict, a
    list, or absent. ``key`` optionally unwraps a nesting element (e.g. the
    ``detector`` inside ``{"detectors": {"detector": [...]}}``).
    """
    if isinstance(value, dict) and key is not None:
        value = value.get(key)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


@dataclass
class NodeDiff:
    foreign_id: str
    label: str
    status: str  # "added" | "removed" | "changed"
    changes: list = field(default_factory=list)


@dataclass
class DryRun:
    foreign_source: str
    exists: bool  # False = the Foreign Source is not yet in OpenNMS (all-added)
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    unchanged: int = 0
    definition_changes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # Non-empty = the Requisition is FROZEN (C1): the conflicts are reported
    # instead of a node diff, because no sync could push what the diff shows.
    conflicts: list = field(default_factory=list)

    @property
    def has_changes(self):
        return bool(
            self.added or self.removed or self.changed or self.definition_changes
        )


def _desired_nodes(resolution, default_location=""):
    """Map foreign-id → normalized desired node from a Resolution.

    ``default_location`` mirrors the renderer (``render_requisition`` substitutes it
    for a blank node location), so the diff doesn't report a phantom location change
    when the requisition/override location is blank but a default is configured.
    """
    result = {}
    if resolution is None:
        return result
    for node in resolution.nodes:
        primary = next(
            (i for i in node.interfaces if i.role == InterfaceRoleChoices.PRIMARY),
            None,
        )
        result[node.foreign_id] = {
            "label": node.node_label,
            "management_ip": primary.ip if primary else None,
            "location": node.location or default_location or "",
            "interfaces": {i.ip: sorted(i.services) for i in node.interfaces},
        }
    return result


def _current_nodes(current):
    """Map foreign-id → normalized current node from an OpenNMS requisition JSON."""
    result = {}
    if not isinstance(current, dict):
        return result
    for node in _as_list(current.get("node")):
        if not isinstance(node, dict):
            continue
        fid = node.get("foreign-id")
        if not fid:
            continue
        interfaces = {}
        primary_ip = None
        for iface in _as_list(node.get("interface")):
            if not isinstance(iface, dict):
                continue
            ip = iface.get("ip-addr")
            if not ip:
                continue
            if iface.get("snmp-primary") == "P":
                primary_ip = ip
            services = sorted(
                svc.get("service-name")
                for svc in _as_list(iface.get("monitored-service"))
                if isinstance(svc, dict) and svc.get("service-name")
            )
            interfaces[ip] = services
        result[fid] = {
            "label": node.get("node-label", ""),
            "management_ip": primary_ip,
            "location": node.get("location") or "",
            "interfaces": interfaces,
        }
    return result


def _node_changes(desired, current):
    """Human-readable field-level changes between a desired and current node."""
    changes = []
    if desired["management_ip"] != current["management_ip"]:
        changes.append(
            f"management IP {current['management_ip']} → {desired['management_ip']}"
        )
    if desired["location"] != current["location"]:
        changes.append(
            f"location {current['location'] or '—'} → {desired['location'] or '—'}"
        )
    added_ifaces = sorted(set(desired["interfaces"]) - set(current["interfaces"]))
    removed_ifaces = sorted(set(current["interfaces"]) - set(desired["interfaces"]))
    for ip in added_ifaces:
        changes.append(f"+interface {ip}")
    for ip in removed_ifaces:
        changes.append(f"-interface {ip}")
    for ip in sorted(set(desired["interfaces"]) & set(current["interfaces"])):
        if desired["interfaces"][ip] != current["interfaces"][ip]:
            changes.append(
                f"services on {ip}: {current['interfaces'][ip]} → "
                f"{desired['interfaces'][ip]}"
            )
    return changes


def _definition_changes(requisition, current_def):
    """Detector/policy/scan-interval changes between the Requisition and OpenNMS."""
    changes = []
    current_def = current_def if isinstance(current_def, dict) else {}
    cur_scan = current_def.get("scan-interval")
    want_scan = requisition.scan_interval or "1d"
    if cur_scan is not None and cur_scan != want_scan:
        changes.append(f"scan-interval {cur_scan} → {want_scan}")

    def _named(entries):
        out = {}
        for entry in entries:
            if isinstance(entry, dict) and entry.get("name"):
                out[entry["name"]] = entry.get("class", "")
        return out

    for kind, want_qs, cur_raw in (
        ("detector", requisition.detectors.all(), current_def.get("detectors")),
        ("policy", requisition.policies.all(), current_def.get("policies")),
    ):
        want = {r.name: r.rule_class for r in want_qs}
        # OpenNMS nests the list under the singular element (e.g. detector) and may
        # unwrap a lone element to a bare object — _as_list normalizes both.
        current = _named(_as_list(cur_raw, kind))
        for name in sorted(set(want) - set(current)):
            changes.append(f"+{kind} {name}")
        for name in sorted(set(current) - set(want)):
            changes.append(f"-{kind} {name}")
        for name in sorted(set(want) & set(current)):
            if want[name] != current[name]:
                changes.append(f"~{kind} {name} class changed")
    return changes


def diff(resolution, current_requisition, current_definition, default_location=""):
    """Pure dry-run diff. ``current_*`` are parsed OpenNMS JSON (or ``None``).

    ``default_location`` mirrors the renderer so a blank node location compared
    against OpenNMS's default doesn't read as a change.
    """
    foreign_source = resolution.foreign_source if resolution is not None else ""
    result = DryRun(
        foreign_source=foreign_source, exists=current_requisition is not None
    )
    if resolution is not None:
        result.warnings = list(resolution.warnings)
        if resolution.conflicts:
            # Frozen: report the conflicts INSTEAD of a node diff — a diff of a
            # push that is blocked would only mislead (C1).
            result.conflicts = list(resolution.conflicts)
            return result

    desired = _desired_nodes(resolution, default_location)
    current = _current_nodes(current_requisition)

    for fid in sorted(set(desired) - set(current)):
        result.added.append(NodeDiff(fid, desired[fid]["label"], "added"))
    for fid in sorted(set(current) - set(desired)):
        result.removed.append(NodeDiff(fid, current[fid]["label"], "removed"))
    for fid in sorted(set(desired) & set(current)):
        changes = _node_changes(desired[fid], current[fid])
        if changes:
            result.changed.append(
                NodeDiff(fid, desired[fid]["label"], "changed", changes)
            )
        else:
            result.unchanged += 1

    if resolution is not None:
        result.definition_changes = _definition_changes(
            resolution.requisition, current_definition
        )
    return result


def dry_run(foreign_source):
    """Fetch OpenNMS state for *foreign_source* and diff the rendered intent (R7).

    Resolved FIRST: a frozen Requisition's report needs no remote data, so the
    conflict freeze is shown even when OpenNMS is unreachable (review #6) — and
    two live GETs are skipped when their result would be discarded anyway.
    """
    default_location = get_plugin_config(PLUGIN_NAME, "default_location") or ""
    resolution = resolve(foreign_source)
    if resolution is not None and resolution.conflicts:
        return diff(resolution, None, None, default_location)
    with OpenNMSClient.from_config() as client:
        current_requisition = client.get_requisition(foreign_source)
        current_definition = client.get_foreign_source(foreign_source)
    return diff(resolution, current_requisition, current_definition, default_location)
