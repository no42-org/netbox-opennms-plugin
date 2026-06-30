# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Scope membership + node resolution (Epic 5).

A Foreign Source is now a live NetBox query, not a set of per-object profiles:
the Devices/VMs in a (site, role) ARE the nodes. This module is the resolution
layer the renderer and the sync job consume — it answers three questions:

* which assignment **governs** a Foreign Source (``governing_assignment``),
* which Devices/VMs are its **members** (``members``),
* what each member **resolves to** after per-object overrides (``resolve``).

It reads the ORM but performs no writes/network. ``foreign_source_for`` /
``foreign_id_for`` stay the single derivation owners (AD-14); this is their
inverse for the membership query.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from virtualization.models import VirtualMachine

from .choices import InterfaceScopeChoices
from .derivation import foreign_id_for, foreign_source_for
from .models import MonitoringAssignment, MonitoringOverride


@dataclass
class InterfaceSpec:
    """One resolved OpenNMS interface: a bare IP, primary flag, its services."""

    ip: str
    primary: bool
    ip_pks: list = field(default_factory=list)
    services: list = field(default_factory=list)


@dataclass
class NodeSpec:
    """One resolved OpenNMS node (what the requisition renderer emits)."""

    node_label: str
    foreign_id: str
    location: str
    interfaces: list = field(default_factory=list)


@dataclass
class Resolution:
    """The resolved state of one Foreign Source: governing assignment + nodes."""

    foreign_source: str
    assignment: object
    nodes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _bare_ip(ip):
    """The bare IP (no CIDR mask); ``IPAddress.address`` may be netaddr or str."""
    return str(ip.address).split("/")[0]


def parse_foreign_source(foreign_source):
    """Inverse of ``foreign_source_for``: ``(site_slug, role_slug)``, ``None`` token.

    The name is ``netbox.{site}.{role}`` and a NetBox slug never contains '.', so
    a 3-way split is injective. ``no-site`` / ``no-role`` map back to ``None``.
    """
    parts = foreign_source.split(".")
    if len(parts) != 3 or parts[0] != "netbox":
        raise ValueError(f"Not a netbox Foreign Source name: {foreign_source!r}")
    site = None if parts[1] == "no-site" else parts[1]
    role = None if parts[2] == "no-role" else parts[2]
    return site, role


def governing_assignment(foreign_source):
    """The MonitoringAssignment that governs a Foreign Source, or ``None``.

    A (site, role) assignment beats a site-level (role NULL) one for the same
    site (D9 precedence). A ``no-site`` Foreign Source has no site and so can
    never be governed.
    """
    site_slug, role_slug = parse_foreign_source(foreign_source)
    if site_slug is None:
        return None
    qs = MonitoringAssignment.objects.filter(site__slug=site_slug).select_related(
        "profile", "site", "role"
    )
    if role_slug is None:
        return qs.filter(role__isnull=True).first()
    assignments = list(qs.filter(Q(role__slug=role_slug) | Q(role__isnull=True)))
    exact = next(
        (a for a in assignments if a.role and a.role.slug == role_slug), None
    )
    return exact or next((a for a in assignments if a.role_id is None), None)


def members(foreign_source):
    """The Devices/VMs whose derived Foreign Source equals ``foreign_source``.

    Devices carry a concrete site+role (both required in NetBox) so they filter
    on the indexed slugs directly; a ``no-site``/``no-role`` Foreign Source can
    therefore only hold VMs. A VM's site is indirect (cluster scope, AD-14), so
    VMs filter on the indexed role then confirm the exact derived name.
    """
    site_slug, role_slug = parse_foreign_source(foreign_source)
    objects = []

    if site_slug is not None and role_slug is not None:
        objects.extend(
            Device.objects.filter(
                site__slug=site_slug, role__slug=role_slug
            ).select_related("site", "role", "primary_ip4", "primary_ip6")
        )

    vms = VirtualMachine.objects.select_related(
        "role", "site", "cluster", "primary_ip4", "primary_ip6"
    )
    vms = vms.filter(role__isnull=True) if role_slug is None else vms.filter(
        role__slug=role_slug
    )
    objects.extend(vm for vm in vms if foreign_source_for(vm) == foreign_source)
    return objects


def _object_ips(obj):
    """Every IPAddress on a Device's/VM's interfaces (for the 'all IPs' scope)."""
    ips = []
    interfaces = getattr(obj, "interfaces", None)
    if interfaces is None:
        return ips
    for interface in interfaces.all():
        ips.extend(interface.ip_addresses.all())
    return ips


def _overrides_by_object(objects):
    """Bulk-load the MonitoringOverride for each object, keyed by ``(ct_id, pk)``."""
    by_ct = defaultdict(list)
    cts = {}
    for obj in objects:
        ct = ContentType.objects.get_for_model(type(obj))
        cts[type(obj)] = ct
        by_ct[ct.id].append(obj.pk)
    result = {}
    for ct_id, pks in by_ct.items():
        overrides = MonitoringOverride.objects.filter(
            assigned_object_type_id=ct_id, assigned_object_id__in=pks
        ).prefetch_related("additional_ips", "services", "management_ip")
        for override in overrides:
            result[(ct_id, override.assigned_object_id)] = override
    return result


def resolve_node(obj, assignment, override):
    """Resolve one member to a ``NodeSpec``, or ``(None, warning)`` if unmonitorable.

    Excluded objects and objects without a resolvable management IP/name are
    skipped (a warning, never fatal — AD-15/D-skip). The management IP is the
    primary interface; the profile's interface scope and the override's extra IPs
    add non-primary interfaces; explicit override services attach per IP.
    """
    label = obj.name
    if not label:
        return None, f"{obj} has no name; skipped (a node-label is required)."
    if override is not None and override.exclude:
        return None, None

    management = None
    if override is not None and override.management_ip_id:
        management = override.management_ip
    if management is None:
        management = obj.primary_ip
    if management is None:
        return None, (
            f"{label}: no management IP (set a primary IP or an override); skipped."
        )

    profile = assignment.profile
    primary_bare = _bare_ip(management)
    interfaces = {
        primary_bare: InterfaceSpec(primary_bare, True, [management.pk]),
    }

    extra = []
    if profile.default_interfaces == InterfaceScopeChoices.ALL:
        extra.extend(_object_ips(obj))
    if override is not None:
        extra.extend(override.additional_ips.all())
    for ip in sorted(extra, key=_bare_ip):
        bare = _bare_ip(ip)
        if bare in interfaces:
            interfaces[bare].ip_pks.append(ip.pk)
        else:
            interfaces[bare] = InterfaceSpec(bare, False, [ip.pk])

    services_by_ip = defaultdict(list)
    if override is not None:
        for service in override.services.all():
            services_by_ip[service.ip_address_id].append(service.name)
    for spec in interfaces.values():
        names = set()
        for ip_pk in spec.ip_pks:
            names.update(services_by_ip.get(ip_pk, []))
        spec.services = sorted(names)

    override_location = override.location if override is not None else ""
    location = override_location or assignment.location
    node = NodeSpec(label, foreign_id_for(obj), location, list(interfaces.values()))
    return node, None


def resolve(foreign_source):
    """Resolve a whole Foreign Source to its governing assignment + node specs.

    Returns ``None`` when no assignment governs it (the Foreign Source is not
    monitored). Unmonitorable members become ``warnings``, not nodes.
    """
    assignment = governing_assignment(foreign_source)
    if assignment is None:
        return None
    objects = members(foreign_source)
    overrides = _overrides_by_object(objects)
    nodes, warnings = [], []
    for obj in objects:
        ct = ContentType.objects.get_for_model(type(obj))
        node, warning = resolve_node(
            obj, assignment, overrides.get((ct.id, obj.pk))
        )
        if warning:
            warnings.append(warning)
        if node is not None:
            nodes.append(node)
    nodes.sort(key=lambda n: n.foreign_id)
    return Resolution(foreign_source, assignment, nodes, warnings)


def monitored_foreign_sources():
    """Every Foreign Source that has a governing assignment AND ≥1 member.

    Drives 'sync all' / the preview screen. Expands each assignment to the
    concrete (site, role) Foreign Sources its members fall into, so a site-level
    assignment fans out across the roles actually present in the site. A more
    specific assignment wins, so a Foreign Source is attributed once.
    """
    result = set()
    for assignment in MonitoringAssignment.objects.select_related("site", "role"):
        site_slug = assignment.site.slug
        if assignment.role_id is not None:
            fs = f"netbox.{site_slug}.{assignment.role.slug}"
            if members(fs):
                result.add(fs)
            continue
        # Site-level: one Foreign Source per role present among the site's objects.
        seen = set()
        for obj in Device.objects.filter(site__slug=site_slug).select_related(
            "site", "role"
        ):
            seen.add(foreign_source_for(obj))
        for vm in VirtualMachine.objects.select_related("role", "site", "cluster"):
            fs = foreign_source_for(vm)
            if fs.startswith(f"netbox.{site_slug}."):
                seen.add(fs)
        for fs in seen:
            if governing_assignment(fs) == assignment:
                result.add(fs)
    return sorted(result)
