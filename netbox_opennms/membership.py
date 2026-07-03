# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Requisition membership + node resolution (conflict model).

A Foreign Source is a user-named **Requisition** whose members are a live NetBox
**filter** over Devices/VMs. Each Requisition resolves **independently** — there
is no priority and no ordering. An object matching two or more Requisitions'
filters is a blocking **conflict** (C1): it is rendered into none of them, and
every involved Requisition is frozen (Sync blocked) until the user makes the
filters disjoint or excludes the object. Excluded objects never conflict — they
are monitored nowhere regardless (C3). This module answers:

* which objects each Requisition **matches** and which of those **conflict**
  (the order-free global resolver, ``resolve_all``),
* what each unconflicted member **resolves to** after per-object overrides
  (``resolve_node`` → a ``NodeSpec`` the renderer emits),
* which Requisitions **match** a given Device/VM (``matching_requisitions`` —
  one match = governed, several = conflicted).

It reads the ORM but performs no writes/network. The unknown-key guard is a
**key-set diff** against the filtersets' known keys (NOT ``is_valid()``, which
silently ignores unknown keys); an empty/no-effective-key filter is rejected;
a recognized key with a stale value is a warning, not a silent empty.
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

from .choices import InterfaceRoleChoices, InterfaceScopeChoices, ObjectTypeChoices
from .derivation import foreign_id_for
from .models import MonitoringOverride, Requisition

# select_related sets keeping resolve() query-lean for the primary-IP/site lookups.
_DEVICE_RELATED = ("site", "role", "primary_ip4", "primary_ip6")
_VM_RELATED = ("role", "site", "cluster", "primary_ip4", "primary_ip6")


@dataclass
class InterfaceSpec:
    """One resolved OpenNMS interface: a bare IP, its SNMP role (P/S/N), services."""

    ip: str
    role: str
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
class Conflict:
    """One object claimed by ≥2 Requisitions' filters — blocks Sync of all of them."""

    label: str
    foreign_id: str
    requisition_names: list = field(default_factory=list)

    def __str__(self):
        return (
            f"{self.label} is matched by {len(self.requisition_names)} requisitions "
            f"({', '.join(self.requisition_names)})"
        )


@dataclass
class Resolution:
    """The resolved state of one Requisition: nodes, warnings, blocking states.

    ``conflicts`` (filter overlap) and ``rejected`` (unknown-key / no-effective-
    constraint filter) both block Sync via ``validate_resolution``; ``warnings``
    never block. ``rejected`` is kept apart from ``warnings`` so the same text is
    not reported twice (once as warning, once as error).
    """

    foreign_source: str
    requisition: object
    nodes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    rejected: list = field(default_factory=list)


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
    key-set diff, not ``is_valid()`` (which silently ignores unknown keys); an
    empty filter or one whose every key is unknown has no effective constraint
    and is rejected so it can't become a fleet-wide catch-all.
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
        ).prefetch_related("interfaces__ip_address", "services", "management_ip")
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

    mgmt_role = InterfaceRoleChoices.PRIMARY
    if override is not None and override.management_role:
        mgmt_role = override.management_role
    primary_bare = _bare_ip(management)
    interfaces = {
        primary_bare: InterfaceSpec(primary_bare, mgmt_role, [management.pk]),
    }

    # Extra interfaces as (ip, role). Override-defined interfaces take precedence
    # over all-scope IPs for the same address: they are listed first and the sort
    # is stable, so their role wins when both surface the same IP (AD-15/RD-5).
    extra_pairs = []
    if override is not None:
        for interface in override.interfaces.all():
            extra_pairs.append((interface.ip_address, interface.role))
    if requisition.default_interfaces == InterfaceScopeChoices.ALL:
        for ip in _object_ips(obj):
            extra_pairs.append((ip, InterfaceRoleChoices.NOT_ELIGIBLE))
    for ip, role in sorted(extra_pairs, key=lambda pair: _bare_ip(pair[0])):
        bare = _bare_ip(ip)
        spec = interfaces.get(bare)
        if spec is not None:
            if ip.pk not in spec.ip_pks:
                spec.ip_pks.append(ip.pk)
        else:
            interfaces[bare] = InterfaceSpec(bare, role, [ip.pk])

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


def _matched_objects(requisition, warnings):
    """The objects a Requisition's filter matches, independent of every other one."""
    matched = []
    params = requisition.filter_params or {}
    if _wants_devices(requisition):
        qs = Device.objects.select_related(*_DEVICE_RELATED)
        matched.extend(_apply_filterset(DeviceFilterSet, params, qs, warnings))
    if _wants_vms(requisition):
        qs = VirtualMachine.objects.select_related(*_VM_RELATED)
        qs, vm_params = _vm_queryset_for_site(params, qs)
        matched.extend(
            _apply_filterset(VirtualMachineFilterSet, vm_params, qs, warnings)
        )
    return matched


def resolve_all():
    """Resolve every Requisition independently and detect conflicts (C1/C4).

    Order-free: each Requisition's filter is applied on its own (no priority, no
    claim pool), then a global object→requisitions map is derived. An object
    matched by ≥2 Requisitions (after exclusion, C3) is a **conflict** recorded on
    EVERY involved Resolution — those objects render nowhere and the involved
    Requisitions are frozen (Sync blocked) until the user resolves the overlap.
    Returns one ``Resolution`` per Requisition (zero-node/frozen ones included).
    """
    requisitions = list(Requisition.objects.all())

    # Pass 1: independent membership per Requisition. A rejected filter (unknown
    # key / no effective constraint) contributes no members; the rejection lands
    # in Resolution.rejected, which validate_resolution raises as blocking
    # errors — Sync fails loudly rather than quietly skipping. Matched rows are
    # deduped by (ct, pk): a to-many join filter yielding the same object twice
    # must not become a false self-conflict or a duplicate node (review #7).
    membership, req_warnings, req_rejected = {}, {}, {}
    for requisition in requisitions:
        rejected = filter_errors(requisition)
        warnings = []
        matched = [] if rejected else _matched_objects(requisition, warnings)
        unique, seen = [], set()
        for obj in matched:
            ct = ContentType.objects.get_for_model(type(obj))
            key = (ct.id, obj.pk)
            if key not in seen:
                seen.add(key)
                unique.append(obj)
        membership[requisition.pk] = unique
        req_warnings[requisition.pk] = warnings
        req_rejected[requisition.pk] = rejected

    # Bulk-load overrides once across every matched object.
    seen_objects = {}
    for objects in membership.values():
        for obj in objects:
            ct = ContentType.objects.get_for_model(type(obj))
            seen_objects.setdefault((ct.id, obj.pk), obj)
    overrides = _overrides_by_object(list(seen_objects.values()))

    # Conflict detection runs POST-exclusion (C3): an excluded object is monitored
    # nowhere regardless of how many filters match it — no ambiguity, no conflict.
    claims = defaultdict(list)
    for requisition in requisitions:
        for obj in membership[requisition.pk]:
            ct = ContentType.objects.get_for_model(type(obj))
            key = (ct.id, obj.pk)
            override = overrides.get(key)
            if override is not None and override.exclude:
                continue
            claims[key].append(requisition.name)
    conflicted = {key: names for key, names in claims.items() if len(names) >= 2}

    # Pass 2: resolve nodes; conflicted objects become Conflict entries instead.
    resolutions = []
    for requisition in requisitions:
        warnings = req_warnings[requisition.pk]
        nodes, conflicts = [], []
        for obj in membership[requisition.pk]:
            ct = ContentType.objects.get_for_model(type(obj))
            key = (ct.id, obj.pk)
            if key in conflicted:
                conflicts.append(
                    Conflict(
                        label=obj.name or str(obj),
                        foreign_id=foreign_id_for(obj),
                        requisition_names=sorted(conflicted[key]),
                    )
                )
                continue
            node, warning = resolve_node(obj, requisition, overrides.get(key))
            if warning:
                warnings.append(warning)
            if node is not None:
                nodes.append(node)
        nodes.sort(key=lambda n: n.foreign_id)
        conflicts.sort(key=lambda c: c.foreign_id)
        resolutions.append(
            Resolution(
                requisition.name,
                requisition,
                nodes=nodes,
                warnings=warnings,
                conflicts=conflicts,
                rejected=req_rejected[requisition.pk],
            )
        )
    return resolutions


def resolve(foreign_source):
    """Resolve one Foreign Source (Requisition name) to its ``Resolution``, or None.

    Runs the global pass (conflict detection needs every Requisition's member set,
    so a single Requisition cannot be resolved in isolation) and returns the
    matching ``Resolution``. ``None`` when no Requisition has that name.
    """
    for resolution in resolve_all():
        if resolution.foreign_source == foreign_source:
            return resolution
    return None


def matching_requisitions(target):
    """Every Requisition whose filter matches a given Device/VM — no fleet pass.

    Drives the Device/VM observability panel, called on every detail-page render,
    so it tests each Requisition's filterset against just this object —
    O(requisitions), not O(fleet). One match = governed by it; several = the
    object is **conflicted** between them (C1); none = unmonitored.

    Exclusion is deliberately NOT applied here: an excluded object still shows
    which Requisition matches it (review #9); the caller checks the override for
    the excluded/monitored distinction.
    """
    if not isinstance(target, (Device, VirtualMachine)):
        return []
    is_device = isinstance(target, Device)
    matches = []
    for requisition in Requisition.objects.all():
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
            matches.append(requisition)
    return matches


def requisition_conflicts(requisition, warnings=None):
    """Conflicts for ONE Requisition without a fleet pass (the detail-page banner).

    Computes this Requisition's own (deduped, post-exclusion) members, then tests
    every OTHER Requisition's filterset restricted to just those pks — narrow
    queries, no node resolution, no whole-fleet ``resolve_all()`` (review #12).
    Returns the same ``Conflict`` shape ``resolve_all`` produces. Pass a
    ``warnings`` list to collect this requisition's stale-value warnings — the
    detail page is the post-save surface and must not be silent about a filter
    that now matches nothing.
    """
    if filter_errors(requisition):
        return []
    matched = _matched_objects(
        requisition, warnings if warnings is not None else []
    )
    overrides = _overrides_by_object(matched)
    members = {}
    for obj in matched:
        ct = ContentType.objects.get_for_model(type(obj))
        key = (ct.id, obj.pk)
        override = overrides.get(key)
        if override is not None and override.exclude:
            continue
        members.setdefault(key, obj)
    if not members:
        return []

    device_ct = ContentType.objects.get_for_model(Device)
    vm_ct = ContentType.objects.get_for_model(VirtualMachine)
    device_pks = [pk for (ct_id, pk) in members if ct_id == device_ct.id]
    vm_pks = [pk for (ct_id, pk) in members if ct_id == vm_ct.id]

    names_by_key = defaultdict(set)
    for other in Requisition.objects.exclude(pk=requisition.pk):
        if filter_errors(other):
            continue  # a rejected filter contributes nothing (as in resolve_all)
        params = other.filter_params or {}
        if device_pks and _wants_devices(other):
            qs = Device.objects.filter(pk__in=device_pks)
            hits = _apply_filterset(DeviceFilterSet, params, qs, [])
            for pk in hits.values_list("pk", flat=True):
                names_by_key[(device_ct.id, pk)].add(other.name)
        if vm_pks and _wants_vms(other):
            qs = VirtualMachine.objects.filter(pk__in=vm_pks)
            qs, vm_params = _vm_queryset_for_site(params, qs)
            hits = _apply_filterset(VirtualMachineFilterSet, vm_params, qs, [])
            for pk in hits.values_list("pk", flat=True):
                names_by_key[(vm_ct.id, pk)].add(other.name)

    conflicts = []
    for key, names in names_by_key.items():
        obj = members[key]
        conflicts.append(
            Conflict(
                label=obj.name or str(obj),
                foreign_id=foreign_id_for(obj),
                requisition_names=sorted({requisition.name, *names}),
            )
        )
    conflicts.sort(key=lambda c: c.foreign_id)
    return conflicts


def monitored_foreign_sources():
    """Every Foreign Source that is NOT cleanly empty — the reconciler's guard.

    Drives the preview / the drift reconciler. A Foreign Source is subject to
    orphan teardown ONLY when its Requisition resolves **cleanly** to zero
    members: no nodes, no conflicts, AND no warnings. Anything else counts as
    monitored:

    * ≥1 node — actively synced;
    * ≥1 conflict — frozen (C5): the freeze exists to protect the deployed state;
    * a rejected filter — blocked from syncing, so it must be equally blocked
      from teardown;
    * ≥1 warning — a possibly-broken filter (stale value, members skipped):
      tearing down on a warning would let a NetBox rename silently delete live
      OpenNMS nodes (review #1) — the reconciler must never destroy state it
      cannot prove is intentionally empty.

    A requisition whose members were genuinely removed (or deliberately excluded)
    resolves cleanly empty and is reconciled away, as intended.
    """
    return sorted(
        r.foreign_source
        for r in resolve_all()
        if r.nodes or r.conflicts or r.warnings or r.rejected
    )
