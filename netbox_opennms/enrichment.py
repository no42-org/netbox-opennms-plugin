# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Resolve NetBox attribute sources for asset/metadata enrichment (RD-2/RD-3).

A curated, None-safe map from a source key to a per-object value (works for both
Device and VirtualMachine — a source that doesn't apply yields ``None``), plus
custom-field access via a ``cf_<name>`` source. ``None`` means "omit the
``<asset>``/``<meta-data>`` element for this member" (the renderer never emits a
blank value). Pure — reads the object, no I/O — so it stays in the resolution
layer and the translation layer remains side-effect-free (AD-3).
"""


def _rel_name(obj, attr):
    """The ``.name`` of a related object at ``attr``, or ``None``."""
    related = getattr(obj, attr, None)
    return getattr(related, "name", None) if related is not None else None


# source key -> callable(obj) -> value|None. Each is None-safe across Device/VM.
CURATED = {
    "name": lambda o: getattr(o, "name", None),
    "serial": lambda o: getattr(o, "serial", None),
    "asset_tag": lambda o: getattr(o, "asset_tag", None),
    "model": lambda o: getattr(getattr(o, "device_type", None), "model", None),
    "manufacturer": lambda o: getattr(
        getattr(getattr(o, "device_type", None), "manufacturer", None), "name", None
    ),
    "platform": lambda o: _rel_name(o, "platform"),
    "role": lambda o: _rel_name(o, "role"),
    "site": lambda o: _rel_name(o, "site"),
    "rack": lambda o: _rel_name(o, "rack"),
    "tenant": lambda o: _rel_name(o, "tenant"),
    "description": lambda o: getattr(o, "description", None) or None,
    "comments": lambda o: getattr(o, "comments", None) or None,
}


def resolve_source(obj, source):
    """The string value of a source for an object, or ``None`` (→ omit the element).

    ``source`` is a curated key (see ``CURATED`` / ``NetBoxSourceChoices``) or a
    ``cf_<name>`` custom-field reference. An empty/absent value yields ``None``.
    """
    if not source:
        return None
    if source.startswith("cf_"):
        value = (getattr(obj, "custom_field_data", None) or {}).get(source[3:])
    else:
        resolver = CURATED.get(source)
        value = resolver(obj) if resolver else None
    if value in (None, ""):
        return None
    return str(value)
