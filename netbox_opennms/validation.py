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


@dataclass
class ValidationResult:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def validate_resolution(resolution):
    """Validate a resolved Foreign Source (``membership.resolve``). Returns a result.

    ``None`` (no such Requisition) is a clean, empty result — nothing to push.
    Member skips (and rejected/stale filters) are warnings; an invalid resolved
    location is an error (it would 400 on import, like the historic ':' bug).
    """
    result = ValidationResult()
    if resolution is None:
        return result

    result.warnings.extend(resolution.warnings)

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
