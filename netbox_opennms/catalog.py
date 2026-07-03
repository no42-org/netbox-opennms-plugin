# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Detector/policy catalog service (RD-1): live discovery + overlay merge + cache.

The single place the UI asks "what detectors/policies exist and what parameters do
they take?". It fetches the catalog from the live OpenNMS instance through the
``OpenNMSClient`` port (AD-2), caches it briefly, and **merges** the discovered
schema (authoritative class list + each parameter's ``key`` / ``required`` /
``options``) with the ``presets.py`` **curation overlay** (friendly labels, sensible
defaults, the curated shortlist). When OpenNMS is unreachable it **degrades
gracefully** to the overlay alone and flags ``live_unavailable`` — the editor stays
usable (curated presets + freeform class entry) and the save path is never blocked.

Merge key is the OpenNMS **class**. Discovered ``required`` is annotation-driven (not
"user must fill") and ``options`` is only present for enum parameters, so the overlay
supplies labels/defaults and hard "must-fill" validation stays on the overlay's
required list (``presets.detector_required_params`` / ``policy_required_params``).
"""

from dataclasses import dataclass, field

from django.core.cache import cache

from .client import OpenNMSClient, OpenNMSError
from .presets import DETECTOR_PRESETS, POLICY_PRESETS

CACHE_TTL = 300  # seconds — short; refreshed on demand and at Sync
# A degraded (overlay-only) result is cached briefly so a configured-but-down
# OpenNMS doesn't re-block every form render on the client timeout; it still
# recovers within this window (and `refresh_catalogs()` clears it at Sync).
DEGRADED_CACHE_TTL = 30
_DETECTOR_CACHE_KEY = "netbox_opennms:catalog:detectors"
_POLICY_CACHE_KEY = "netbox_opennms:catalog:policies"


@dataclass
class CatalogParam:
    """One parameter: discovery key/required/options + overlay label/default."""

    key: str
    required: bool = False
    options: tuple = ()
    label: str = ""
    default: str = ""


@dataclass
class CatalogEntry:
    """A selectable detector/policy: class, merged params, and overlay preset key."""

    name: str
    plugin_class: str
    parameters: list = field(default_factory=list)
    preset_key: str = ""  # the matching overlay preset, if any
    source: str = "discovered"  # "discovered" | "overlay"


@dataclass
class Catalog:
    """The merged catalog handed to the UI, plus whether live discovery succeeded."""

    entries: list = field(default_factory=list)
    live_unavailable: bool = False

    def by_class(self, plugin_class):
        """The entry for an OpenNMS class, or ``None``."""
        for entry in self.entries:
            if entry.plugin_class == plugin_class:
                return entry
        return None

    def by_preset(self, preset_key):
        """The entry for an overlay preset key, or ``None``."""
        for entry in self.entries:
            if preset_key and entry.preset_key == preset_key:
                return entry
        return None


def get_detector_catalog(client=None, force_refresh=False):
    """The merged detector catalog (cached; degrades to the overlay when offline)."""
    return _get_catalog(
        _DETECTOR_CACHE_KEY, DETECTOR_PRESETS, "list_detectors", client, force_refresh
    )


def get_policy_catalog(client=None, force_refresh=False):
    """The merged policy catalog (cached; degrades to the overlay when offline)."""
    return _get_catalog(
        _POLICY_CACHE_KEY, POLICY_PRESETS, "list_policies", client, force_refresh
    )


def refresh_catalogs():
    """Drop the cached catalogs so the next read re-discovers (on demand / at Sync)."""
    cache.delete_many([_DETECTOR_CACHE_KEY, _POLICY_CACHE_KEY])


def _get_catalog(cache_key, presets, method_name, client, force_refresh):
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    discovered, live_unavailable = _discover(method_name, client)
    catalog = Catalog(
        entries=_merge(discovered, presets), live_unavailable=live_unavailable
    )
    # Cache a degraded (overlay-only) result only briefly, so a down OpenNMS doesn't
    # re-block every render on the client timeout but a recovery is still picked up
    # quickly; a live result is cached for the full TTL.
    cache.set(cache_key, catalog, DEGRADED_CACHE_TTL if live_unavailable else CACHE_TTL)
    return catalog


def _discover(method_name, client):
    """Fetch discovered plugins; ``([], True)`` when OpenNMS can't be reached."""
    created = None
    try:
        if client is None:
            client = created = OpenNMSClient.from_config()
    except OpenNMSError:
        return [], True
    try:
        return list(getattr(client, method_name)()), False
    except OpenNMSError:
        return [], True
    finally:
        if created is not None:
            try:
                created.close()
            except Exception:  # noqa: BLE001 — close is best-effort
                pass


def _overlay_by_class(presets):
    """Map each overlay preset's OpenNMS class → (preset_key, spec)."""
    return {spec["class"]: (key, spec) for key, spec in presets.items()}


def _overlay_labels_defaults(spec):
    """(labels, defaults) for an overlay preset's params, from its schema + defaults."""
    labels, defaults = {}, {}
    if not spec:
        return labels, defaults
    for key, label, default in spec.get("schema", []):
        labels[key] = label
        if default:
            defaults[key] = default
    for key, value in (spec.get("parameters") or {}).items():
        defaults.setdefault(key, value)
    return labels, defaults


def _entry_from_discovered(plugin, preset_key, spec):
    labels, defaults = _overlay_labels_defaults(spec)
    seen = set()
    params = []
    for param in plugin.parameters:
        seen.add(param.key)
        params.append(
            CatalogParam(
                key=param.key,
                required=param.required,
                options=param.options,
                label=labels.get(param.key, ""),
                default=defaults.get(param.key, ""),
            )
        )
    # Overlay params discovery didn't surface (e.g. TcpDetector's required `port`)
    # are still offered so the curated form is complete.
    for key in labels:
        if key not in seen:
            params.append(
                CatalogParam(key=key, label=labels[key], default=defaults.get(key, ""))
            )
    return CatalogEntry(
        name=plugin.name or plugin.plugin_class,
        plugin_class=plugin.plugin_class,
        parameters=params,
        preset_key=preset_key,
        source="discovered",
    )


def _entry_from_overlay(preset_key, spec, required_keys):
    labels, defaults = _overlay_labels_defaults(spec)
    params = [
        CatalogParam(
            key=key,
            required=key in required_keys,
            label=labels[key],
            default=defaults.get(key, ""),
        )
        for key in labels
    ]
    for key in defaults:
        if key not in labels:
            params.append(CatalogParam(key=key, default=defaults[key]))
    return CatalogEntry(
        name=preset_key,
        plugin_class=spec["class"],
        parameters=params,
        preset_key=preset_key,
        source="overlay",
    )


def _merge(discovered, presets):
    """Merge discovered plugins (authoritative) with the overlay (labels/defaults)."""
    overlay = _overlay_by_class(presets)
    entries = []
    seen_classes = set()
    for plugin in discovered:
        preset_key, spec = overlay.get(plugin.plugin_class, ("", None))
        entries.append(_entry_from_discovered(plugin, preset_key, spec))
        seen_classes.add(plugin.plugin_class)
    # Curated presets whose class the instance didn't report (e.g. OpenNMS offline,
    # or a plugin not installed) remain selectable from the overlay.
    for plugin_class, (preset_key, spec) in overlay.items():
        if plugin_class not in seen_classes:
            required = set(spec.get("required", []))
            entries.append(_entry_from_overlay(preset_key, spec, required))
    entries.sort(key=lambda entry: entry.name.lower())
    return entries
