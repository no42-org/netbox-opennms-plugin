# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Requisition membership + node resolution (Requisition redesign).

A Foreign Source is a user-named **Requisition** whose members are a live NetBox
**filter** over Devices/VMs. Overlap is resolved in **priority order**: a single
global pass claims each object for the highest-priority Requisition whose filter
matches it, then removes it from the pool so lower-priority filters never see it
(R3). This module answers:

* which objects a Requisition **claims** (the priority-ordered global resolver),
* what each claimed object **resolves to** after per-object overrides
  (``resolve_node`` → a ``NodeSpec`` the renderer emits),
* which Requisition **governs** a given Device/VM (``governing_requisition``).

It reads the ORM but performs no writes/network. The unknown-key guard is a
**key-set diff** against the filtersets' known keys (NOT ``is_valid()``, which
silently ignores unknown keys — C1); an empty/no-effective-key filter is rejected
(H1); a recognized key with a stale value is a warning, not a silent empty (L4).
``foreign_id_for`` is unchanged (R6): node identity survives renames.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from dcim.filtersets import DeviceFilterSet
from dcim.models import Device, Site
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from virtualization.filtersets import VirtualMachineFilterSet
from virtualization.models import VirtualMachine

from .choices import InterfaceScopeChoices, ObjectTypeChoices
from .derivation import foreign_id_for
from .models import MonitoringOverride, Requisition

# select_related sets keeping resolve() query-lean for the primary-IP/site lookups.
_DEVICE_RELATED = ("site", "role", "primary_ip4", "primary_ip6")
_VM_RELATED = ("role", "site", "cluster", "primary_ip4", "primary_ip6")


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
    """The resolved state of one Requisition: the Requisition + its nodes."""

    foreign_source: str
    requisition: object
    nodes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _bare_ip(ip):
    """The bare IP (no CIDR mask); ``IPAddress.address`` may be netaddr or str."""
    return str(ip.address).split("/")[0]


def _wants_devices(requisition):
    return requisition.object_types in (
        ObjectTypeChoices.DEVICE,
        ObjectTypeChoices.BOTH,
    )


def _wants_vms(requisition):
    return requisition.object_types in (ObjectTypeChoices.VM, ObjectTypeChoices.BOTH)


def _device_filter_keys():
    # An INSTANTIATED filterset, not DeviceFilterSet.base_filters: NetBox adds the
    # custom-field (cf_*) filters per instance in NetBoxModelFilterSet.__init__, so
    # base_filters would wrongly exclude them and the guard would reject a valid
    # custom-field filter the resolver actually honours (review #2).
    return set(DeviceFilterSet(data={}, queryset=Device.objects.none()).filters)


def _vm_filter_keys():
    return set(
        VirtualMachineFilterSet(data={}, queryset=VirtualMachine.objects.none()).filters
    )


def known_filter_keys(requisition):
    """The set of filter keys understood by the Requisition's selected types (H8).

    A key understood by at least one selected type is valid (it simply doesn't
    constrain the other type); a key understood by none is unknown.
    """
    keys = set()
    if _wants_devices(requisition):
        keys |= _device_filter_keys()
    if _wants_vms(requisition):
        keys |= _vm_filter_keys()
    return keys


def filter_errors(requisition):
    """Blocking filter problems for a Requisition (unknown keys / no effective key).

    Returns a list of error strings (empty = OK). The unknown-key check is a
    key-set diff, not ``is_valid()`` (C1); an empty filter or one whose every key
    is unknown has no effective constraint and is rejected so it can't become a
    catch-all at priority 1 (H1).
    """
    params = requisition.filter_params or {}
    known = known_filter_keys(requisition)
    errors = []
    unknown = sorted(set(params) - known)
    if unknown:
        errors.append(
            f"Filter contains keys not recognized by the selected object types: "
            f"{', '.join(unknown)}."
        )
    # An *effective* key is a known key whose value actually constrains — an empty
    # value ({"role": []}) is a no-op the FilterSet treats as "match everything",
    # so it must not satisfy the guard (H1).
    def _constrains(keys):
        return any(
            key in keys and params[key] not in (None, "", [], {}) for key in params
        )

    if not _constrains(known):
        errors.append(
            "Filter has no effective constraint (empty, or every known key has an "
            "empty value) — refusing to match every object."
        )
    else:
        # Each SELECTED type must be constrained by at least one key it understands,
        # else it is an unguarded catch-all over that type (H8 hardened — review #3).
        per_type = []
        if _wants_devices(requisition):
            per_type.append(("devices", _device_filter_keys()))
        if _wants_vms(requisition):
            per_type.append(("virtual machines", _vm_filter_keys()))
        for label, keys in per_type:
            if not _constrains(keys):
                errors.append(
                    f"Filter does not constrain {label} (a selected object type), so "
                    f"it would match every one — add a {label} filter key or narrow "
                    "the object types."
                )
    return errors


def _apply_filterset(filterset_class, params, queryset, warnings):
    """Run a NetBox FilterSet, collecting stale-value warnings (L4). Returns a qs.

    A recognized key with a value that no longer resolves makes the filterset
    invalid; that is surfaced as a warning (not conflated with an unknown key) and
    the filterset's (possibly-empty) queryset is still returned.
    """
    filterset = filterset_class(params, queryset=queryset)
    if not filterset.is_valid():
        # A recognized key with an invalid value (e.g. a stale/typo'd slug) makes
        # django-filter DROP that field and return the whole queryset unfiltered —
        # a silent catch-all. Treat an invalid filter as matching NOTHING for this
        # type, and surface why (L4). Never fall through to filterset.qs here.
        for field_name, errors in filterset.errors.items():
            warnings.append(
                f"Filter value for {field_name!r} is invalid, so it matched "
                f"nothing: {'; '.join(str(e) for e in errors)}"
            )
        return queryset.none()
    return filterset.qs


def _vm_queryset_for_site(params, base_qs):
    """Expand a VM queryset so a ``site`` filter honours cluster.scope siting (AD-14).

    ``VirtualMachineFilterSet.site`` matches only the VM's direct site FK, but a
    VM's site may be inherited from its cluster's scope (H6). When the filter has a
    ``site`` key, pre-expand to (direct site OR cluster-scope) matches and drop
    ``site`` from the params the filterset then applies, so the rest of the filter
    still narrows. Returns ``(queryset, params_for_filterset)``.
    """
    site_slugs = params.get("site")
    if not site_slugs:
        return base_qs, params
    site_ct = ContentType.objects.get_for_model(Site)
    site_ids = list(
        Site.objects.filter(slug__in=site_slugs).values_list("pk", flat=True)
    )
    expanded = base_qs.filter(
        Q(site__slug__in=site_slugs)
        | Q(cluster__scope_type=site_ct, cluster__scope_id__in=site_ids)
    ).distinct()
    remaining = {key: value for key, value in params.items() if key != "site"}
    return expanded, remaining


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
    for obj in objects:
        ct = ContentType.objects.get_for_model(type(obj))
        by_ct[ct.id].append(obj.pk)
    result = {}
    for ct_id, pks in by_ct.items():
        overrides = MonitoringOverride.objects.filter(
            assigned_object_type_id=ct_id, assigned_object_id__in=pks
        ).prefetch_related("additional_ips", "services", "management_ip")
        for override in overrides:
            result[(ct_id, override.assigned_object_id)] = override
    return result


def resolve_node(obj, requisition, override):
    """Resolve one claimed object to a ``NodeSpec``, or ``(None, warning)``.

    Excluded objects are dropped (monitored nowhere — no fall-through, M2). Objects
    without a resolvable management IP/name are skipped with a warning. Effective
    services per interface = ``(requisition.services ∪ per-IP added) − suppressed``
    (R5).
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

    primary_bare = _bare_ip(management)
    interfaces = {
        primary_bare: InterfaceSpec(primary_bare, True, [management.pk]),
    }

    extra = []
    if requisition.default_interfaces == InterfaceScopeChoices.ALL:
        extra.extend(_object_ips(obj))
    if override is not None:
        extra.extend(override.additional_ips.all())
    for ip in sorted(extra, key=_bare_ip):
        bare = _bare_ip(ip)
        if bare in interfaces:
            interfaces[bare].ip_pks.append(ip.pk)
        else:
            interfaces[bare] = InterfaceSpec(bare, False, [ip.pk])

    declared = list(requisition.services or [])
    added_by_ip = defaultdict(list)
    suppressed = set()
    if override is not None:
        for service in override.services.all():
            added_by_ip[service.ip_address_id].append(service.name)
        suppressed = set(override.suppressed_services or [])
    for spec in interfaces.values():
        names = set(declared)
        for ip_pk in spec.ip_pks:
            names.update(added_by_ip.get(ip_pk, []))
        spec.services = sorted(names - suppressed)

    override_location = override.location if override is not None else ""
    location = override_location or requisition.location
    node = NodeSpec(label, foreign_id_for(obj), location, list(interfaces.values()))
    return node, None


def resolve_all():
    """Resolve every Requisition in priority order (the single global pass, R3).

    Builds the Device/VM pool once, then for each Requisition (ascending priority,
    pk-tiebroken) applies its filter to the remaining pool, claims the matches, and
    removes them so a lower-priority Requisition never re-claims them. Returns a
    list of ``Resolution`` — one per Requisition, in priority order (a Requisition
    that resolves to zero nodes is still present, with its warnings).
    """
    device_pool = set(Device.objects.values_list("pk", flat=True))
    vm_pool = set(VirtualMachine.objects.values_list("pk", flat=True))

    resolutions = []
    for requisition in Requisition.objects.all():  # Meta.ordering = (priority, pk)
        warnings = list(filter_errors(requisition))
        matched = []
        if not warnings:  # a rejected filter contributes no members (and blocks Sync)
            params = requisition.filter_params or {}
            if _wants_devices(requisition):
                qs = Device.objects.filter(pk__in=device_pool).select_related(
                    *_DEVICE_RELATED
                )
                matched.extend(_apply_filterset(DeviceFilterSet, params, qs, warnings))
            if _wants_vms(requisition):
                qs = VirtualMachine.objects.filter(pk__in=vm_pool).select_related(
                    *_VM_RELATED
                )
                qs, vm_params = _vm_queryset_for_site(params, qs)
                matched.extend(
                    _apply_filterset(VirtualMachineFilterSet, vm_params, qs, warnings)
                )
            for obj in matched:
                if isinstance(obj, Device):
                    device_pool.discard(obj.pk)
                else:
                    vm_pool.discard(obj.pk)

        overrides = _overrides_by_object(matched)
        nodes = []
        for obj in matched:
            ct = ContentType.objects.get_for_model(type(obj))
            node, warning = resolve_node(
                obj, requisition, overrides.get((ct.id, obj.pk))
            )
            if warning:
                warnings.append(warning)
            if node is not None:
                nodes.append(node)
        nodes.sort(key=lambda n: n.foreign_id)
        resolutions.append(
            Resolution(requisition.name, requisition, nodes, warnings)
        )
    return resolutions


def resolve(foreign_source):
    """Resolve one Foreign Source (Requisition name) to its ``Resolution``, or None.

    Runs the global priority pass (membership is order-dependent, so a single
    Requisition cannot be resolved in isolation) and returns the matching
    ``Resolution``. ``None`` when no Requisition has that name.
    """
    for resolution in resolve_all():
        if resolution.foreign_source == foreign_source:
            return resolution
    return None


def governing_requisition(target):
    """The Requisition that CLAIMS a given Device/VM, or None — without a fleet pass.

    Drives the Device/VM observability panel, called on every detail-page render, so
    it must NOT run the whole-fleet ``resolve_all()`` (review #8). For a single
    object the governing Requisition is the highest-priority one whose filter matches
    it (higher priority claims first), tested by applying each Requisition's
    filterset to just this object — O(requisitions), not O(fleet).

    Exclusion is deliberately NOT applied here: an excluded object is still *claimed*
    by its Requisition and should show it (review #9); the caller checks the override
    for the excluded/monitored distinction.
    """
    if not isinstance(target, (Device, VirtualMachine)):
        return None
    is_device = isinstance(target, Device)
    for requisition in Requisition.objects.all():  # Meta.ordering = (priority, pk)
        if is_device and not _wants_devices(requisition):
            continue
        if not is_device and not _wants_vms(requisition):
            continue
        if filter_errors(requisition):
            continue
        params = requisition.filter_params or {}
        if is_device:
            base = Device.objects.filter(pk=target.pk)
            matched = _apply_filterset(DeviceFilterSet, params, base, [])
        else:
            base = VirtualMachine.objects.filter(pk=target.pk)
            base, vm_params = _vm_queryset_for_site(params, base)
            matched = _apply_filterset(VirtualMachineFilterSet, vm_params, base, [])
        if matched.filter(pk=target.pk).exists():
            return requisition
    return None


def monitored_foreign_sources():
    """Every Foreign Source (Requisition name) that resolves to ≥1 node.

    Drives 'sync all' / the preview / the drift reconciler. A Requisition that
    resolves to zero nodes is omitted so an empty requisition is never pushed
    (which would wipe a live Foreign Source — M3).
    """
    return sorted(r.foreign_source for r in resolve_all() if r.nodes)
