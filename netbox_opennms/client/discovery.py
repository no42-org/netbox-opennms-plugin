# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Discovered detector/policy catalog DTOs + defensive JSON parsing (RD-1).

The OpenNMS ``foreignSourcesConfig/{detectors,policies}`` endpoints return each
registered plugin's ``name`` + ``class`` + ``parameters`` (each parameter a
``key`` / ``required`` / ``options`` triad). The JSON nesting varies (JAXB
wrapper collapse, single-element objects vs lists), so parse leniently — the exact
shape is confirmed by the live-H36 spike, and this parser digs for the data
wherever it lands rather than assuming one shape.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DiscoveredParam:
    """One configurable parameter of a discovered detector/policy."""

    key: str
    required: bool = False
    options: tuple = ()  # enumerated allowed values (empty when not enumerated)


@dataclass(frozen=True)
class DiscoveredPlugin:
    """A detector/policy the target OpenNMS instance actually registers."""

    name: str
    plugin_class: str
    parameters: tuple = field(default_factory=tuple)


def _unwrap(node, keys):
    """Descend single-key wrapper dicts, then coerce to a list of items."""
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
    if node is None:
        return []
    if isinstance(node, dict):
        return [node]
    return node if isinstance(node, list) else []


def _str_list(node, *keys):
    """Coerce a wrapped/scalar/list value into a list of strings."""
    for key in keys:
        if isinstance(node, dict) and key in node:
            node = node[key]
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [o for o in node if isinstance(o, str)]
    return []


def parse_plugins(payload):
    """Parse a ``foreignSourcesConfig`` payload into ``DiscoveredPlugin`` records.

    Tolerates the wrapper shapes JAXB/Jackson may emit
    (``{"plugins": {"plugin": [...]}}``, ``{"plugin": [...]}``, a bare list, or a
    single object). Entries without a ``class`` are dropped.
    """
    plugins = _unwrap(payload, ("plugin-configuration", "plugins", "plugin"))
    result = []
    for raw in plugins:
        if not isinstance(raw, dict):
            continue
        plugin_class = raw.get("class") or ""
        if not plugin_class:
            continue
        params = []
        for param in _unwrap(raw.get("parameters"), ("parameter",)):
            if not isinstance(param, dict) or not param.get("key"):
                continue
            params.append(
                DiscoveredParam(
                    key=param["key"],
                    required=bool(param.get("required")),
                    options=tuple(_str_list(param.get("options"), "option")),
                )
            )
        result.append(
            DiscoveredPlugin(
                name=raw.get("name") or plugin_class,
                plugin_class=plugin_class,
                parameters=tuple(params),
            )
        )
    return result
