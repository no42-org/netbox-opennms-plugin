# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Pre-push intent validation (FR-8) — shared by the Sync view and the job.

Epic 5 moves field-level rules (location syntax, service-on-monitored-IP, IP
ownership) onto the model ``clean()`` methods, so this layer validates the
*resolved* Foreign Source: it forwards the membership layer's skip warnings
(no name / no management IP / excluded) and re-checks the resolved location
names as a safety net before a push. Orchestration-layer (it reads the ORM) but
does no writes/network.
"""

from dataclasses import dataclass, field

from .derivation import validate_location_name

# Cap the per-object conflict errors surfaced at once — a broad overlap (e.g. a
# fresh duplicate) can conflict on hundreds of objects, and one message per
# object would flood Django messages / the UI (review #5).
MAX_CONFLICT_ERRORS = 5


@dataclass
class ValidationResult:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def validate_resolution(resolution, removing=False):
    """Validate a resolved Foreign Source (``membership.resolve``). Returns a result.

    ``None`` (no such Requisition) is a clean, empty result — nothing to push.
    Member skips and stale filter values are warnings; conflicts, rejected
    filters, and an invalid resolved location are errors (blocking). With
    ``removing=True`` (a deliberate Remove/teardown) a **rejected filter** does
    not block — the filter has no bearing on an empty push, and Remove is the
    escape hatch for tearing down a broken-filter Foreign Source. Conflicts
    still block a Remove (the accepted C1 coarseness): teardown of a frozen FS
    goes through deleting the Requisition.
    """
    result = ValidationResult()
    if resolution is None:
        return result

    result.warnings.extend(resolution.warnings)

    # A REJECTED filter (unknown key / no effective constraint) blocks Sync
    # loudly (review #8) — a quiet skip would let a broken requisition report a
    # green job that pushed nothing. Carried on Resolution.rejected (kept apart
    # from warnings so the same text isn't reported twice).
    if not removing:
        for error in resolution.rejected:
            result.errors.append(
                f"{resolution.foreign_source}: rejected filter — {error}"
            )

    # A conflict FREEZES the Requisition (C1): blocking error, never a warning —
    # pushing would either mis-place the object or delete it from a sibling FS.
    # Bounded output: first MAX_CONFLICT_ERRORS + a summary line (review #5).
    for conflict in resolution.conflicts[:MAX_CONFLICT_ERRORS]:
        result.errors.append(
            f"{resolution.foreign_source}: {conflict} — resolve the overlap "
            "(make the filters disjoint, e.g. with a negated filter such as "
            "tag__n, or exclude the object) before syncing."
        )
    extra = len(resolution.conflicts) - MAX_CONFLICT_ERRORS
    if extra > 0:
        result.errors.append(
            f"{resolution.foreign_source}: … and {extra} more conflicted "
            "object(s) — see the requisition page for the full list."
        )

    try:
        validate_location_name(resolution.requisition.location)
    except ValueError as exc:
        result.errors.append(
            f"{resolution.foreign_source}: invalid requisition location — {exc}"
        )

    for node in resolution.nodes:
        try:
            validate_location_name(node.location)
        except ValueError as exc:
            result.errors.append(f"{node.node_label}: invalid location — {exc}")

    return result
