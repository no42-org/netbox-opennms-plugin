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

from dcim.models import Device, Site
from virtualization.models import VirtualMachine

# Characters OpenNMS forbids in a Foreign Source (requisition) name.
_FORBIDDEN_CHARS = set("/\\?*'\"")


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


def foreign_source_for(target):
    """Return the Foreign Source name for a monitored Device or VirtualMachine.

    Format: ``netbox:{site.slug}:{role.slug}``, with ``no-site`` / ``no-role``
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
    # ':' delimiter (not '-'): slugs may contain hyphens, so a hyphen separator
    # is ambiguous (site "a-b"+role "c" vs site "a"+role "b-c"). ':' cannot
    # appear in a NetBox slug and is OpenNMS-legal, keeping the name injective.
    name = f"netbox:{site_slug}:{role_slug}"
    return validate_foreign_source_name(name)
