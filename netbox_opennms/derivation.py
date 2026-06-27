# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Foreign Source name derivation — the single owner (AD-14).

Every consumer (translation, jobs, views) must call ``foreign_source_for``;
no module may derive the name inline. The name groups monitored objects by
(site, role) so OpenNMS node identity stays stable.

AD-14 specifies a VM's site as ``vm.site or vm.cluster.site``. NetBox 4.x
replaced ``Cluster.site`` with a generic ``scope``, so the cluster fallback
resolves the cluster's scope when that scope is a Site.

The function has no side effects (no writes, no network) and is deterministic,
but reading the target's ``site``/``role``/``cluster.scope`` may lazily load
related objects — callers in the render/sync paths should pass prefetched
(``select_related``) instances to keep it query-free.
"""

import re

from dcim.models import Device, Site
from virtualization.models import VirtualMachine

# Characters OpenNMS forbids in a Foreign Source (requisition) name. ':' is
# forbidden too — OpenNMS rejects it on import with HTTP 400 (caught by the
# Story 4.4 live round-trip), which is why the delimiter below is '.'.
_FORBIDDEN_CHARS = set("/\\?*'\":")

# OpenNMS Monitoring Location names: ASCII alphanumeric plus '-' and '.' (AD-9).
# \A...\Z (not ^...$) so a trailing newline is rejected, not accepted.
_LOCATION_ALLOWED = re.compile(r"\A[A-Za-z0-9.-]*\Z")


def validate_location_name(name):
    """Raise ``ValueError`` if *name* is not a valid OpenNMS location name (AD-9).

    An empty value is allowed (it means "use the default location"). Otherwise
    only ASCII letters, digits, ``-`` and ``.`` are permitted.
    """
    if name and not _LOCATION_ALLOWED.match(name):
        raise ValueError(
            f"Location name {name!r} may contain only ASCII letters, digits, "
            "'-' and '.'."
        )
    return name


def validate_foreign_source_name(name):
    """Raise ``ValueError`` if *name* contains an OpenNMS-forbidden character.

    NetBox slugs are already URL-safe, so this is a contract guard rather than
    an expected failure path.
    """
    bad = sorted(_FORBIDDEN_CHARS.intersection(name))
    if bad:
        raise ValueError(
            f"Foreign Source name {name!r} contains forbidden characters: "
            f"{''.join(bad)}"
        )
    return name


def _site_for(target):
    """Resolve a target's site: a VM falls back to its cluster's scope (4.x)."""
    site = getattr(target, "site", None)
    if site is None:
        cluster = getattr(target, "cluster", None)
        if cluster is not None:
            scope = getattr(cluster, "scope", None)
            if isinstance(scope, Site):
                site = scope
    return site


def foreign_id_for(target):
    """Return the type-qualified OpenNMS Foreign ID for a Device/VM (AD-8).

    ``device-{pk}`` / ``vm-{pk}`` — the type prefix keeps a Device and a VM with
    the same primary key from colliding on node identity.
    """
    if isinstance(target, Device):
        return f"device-{target.pk}"
    if isinstance(target, VirtualMachine):
        return f"vm-{target.pk}"
    raise TypeError(
        "foreign_id_for() expects a Device or VirtualMachine, "
        f"got {type(target).__name__}."
    )


def foreign_source_for(target):
    """Return the Foreign Source name for a monitored Device or VirtualMachine.

    Format: ``netbox.{site.slug}.{role.slug}``, with ``no-site`` / ``no-role``
    substituted when the site or role is absent (AD-9, AD-14).
    """
    if not isinstance(target, (Device, VirtualMachine)):
        raise TypeError(
            "foreign_source_for() expects a Device or VirtualMachine, "
            f"got {type(target).__name__}."
        )
    site = _site_for(target)
    role = getattr(target, "role", None)
    site_slug = site.slug if (site and site.slug) else "no-site"
    role_slug = role.slug if (role and role.slug) else "no-role"
    # '.' delimiter (not '-'): NetBox slugs are [-A-Za-z0-9_] so a hyphen (or
    # underscore) separator is ambiguous (site "a-b"+role "c" vs site "a"+role
    # "b-c"). A slug cannot contain '.', keeping the name injective — and '.' is
    # OpenNMS-legal whereas ':' is FORBIDDEN by OpenNMS (it 400s on import).
    name = f"netbox.{site_slug}.{role_slug}"
    return validate_foreign_source_name(name)
