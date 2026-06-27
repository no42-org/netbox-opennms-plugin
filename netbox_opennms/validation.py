# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Pre-push intent validation (FR-8) — shared by the Sync view and the job.

One routine so every consumer validates the same way: errors block a sync,
warnings are informational. Orchestration-layer (it reads the ORM) but does no
writes/network; it reuses the derivation/model authorities rather than
re-implementing any rule.
"""

from dataclasses import dataclass, field

from .derivation import foreign_source_for, validate_location_name
from .models import object_ip_pks, profile_ip_pks


@dataclass
class ValidationResult:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def validate_profile(profile):
    """Validate one MonitoringProfile's intent (AC2/AC3). Returns a result."""
    result = ValidationResult()
    target = profile.assigned_object
    if target is None:
        result.errors.append(f"Profile #{profile.pk} has no assigned object.")
        return result

    label = str(target)

    # Target type + Foreign Source name (the single owner signals both, AD-14/AD-9).
    foreign_source = None
    try:
        foreign_source = foreign_source_for(target)
    except TypeError:
        result.errors.append(f"{label}: target is not a Device or VirtualMachine.")
    except ValueError as exc:
        result.errors.append(f"{label}: invalid Foreign Source name — {exc}")

    # Missing site/role → non-blocking warning (groups under no-site/no-role).
    if foreign_source is not None:
        if ":no-site:" in foreign_source:
            result.warnings.append(
                f"{label}: no site — will group under a 'no-site' Foreign Source."
            )
        if foreign_source.endswith(":no-role"):
            result.warnings.append(
                f"{label}: no role — will group under a 'no-role' Foreign Source."
            )

    owned = object_ip_pks(target)

    # Resolvable management IP, constrained to the object's own addresses (AD-15).
    if profile.management_ip_id is None:
        result.errors.append(f"{label}: no resolvable management IP.")
    elif profile.management_ip_id not in owned:
        result.errors.append(
            f"{label}: management IP {profile.management_ip} is not assigned "
            "to the object."
        )

    # Location name (AD-9).
    try:
        validate_location_name(profile.location)
    except ValueError as exc:
        result.errors.append(f"{label}: invalid location — {exc}")

    # Additional IPs must be the object's own addresses (AD-15). The management IP
    # is validated above; the renderer treats it as the lone primary, so skip it
    # here even if it was also stored in additional_ips (raw-ORM path).
    for ip in profile.additional_ips.all():
        if ip.pk == profile.management_ip_id:
            continue
        if ip.pk not in owned:
            result.errors.append(
                f"{label}: additional IP {ip} is not assigned to the object."
            )

    # Services must sit on a monitored interface of the profile (AD-15).
    monitored = profile_ip_pks(profile)
    for service in profile.services.all():
        if service.ip_address_id not in monitored:
            result.errors.append(
                f"{label}: service {service.name} on {service.ip_address} is not "
                "on a monitored IP."
            )

    return result


def validate_foreign_source(foreign_source, profiles):
    """Aggregate validation over the enabled profiles of a Foreign Source (AC1)."""
    result = ValidationResult()
    for profile in profiles:
        profile_result = validate_profile(profile)
        result.errors.extend(profile_result.errors)
        result.warnings.extend(profile_result.warnings)
    return result
