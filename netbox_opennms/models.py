# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Plugin data models (Requisition redesign).

A **Requisition** is one user-named OpenNMS Foreign Source: it owns the
foreign-source *definition* (inline detectors + policies + scan-interval) and the
*requisition* (a live NetBox **filter** over Devices/VMs → nodes → interfaces →
services). Filters must be **disjoint**: an object matching two Requisitions is a
blocking *conflict* the user resolves (a node lives in exactly one Foreign
Source), so membership is deterministic and order-free. A **MonitoringOverride**
is an optional per-object exception (exclude / override management IP / add
interfaces / add-or-suppress services / override location). See the OpenSpec
changes ``requisition-redesign`` (R1–R8) and ``replace-priority-with-conflicts``
(C1–C7) for the full design.
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from .choices import (
    DetectorPresetChoices,
    InterfaceScopeChoices,
    ObjectTypeChoices,
    PolicyPresetChoices,
    ServiceChoices,
)
from .derivation import validate_location_name, validate_requisition_name
from .presets import (
    detector_required_params,
    policy_required_params,
    resolve_detector,
    resolve_policy,
)

# A Monitoring Override may attach to a Device or a VirtualMachine.
ASSIGNMENT_MODELS = models.Q(
    models.Q(app_label="dcim", model="device")
    | models.Q(app_label="virtualization", model="virtualmachine")
)


def _validate_service_names(names, field):
    """Raise if any entry in *names* is not a known ``ServiceChoices`` value."""
    valid = {value for value, _label in ServiceChoices()}
    bad = [name for name in (names or []) if name not in valid]
    if bad:
        raise ValidationError({field: f"Unknown service name(s): {', '.join(bad)}."})


def _require_preset_params(rule, required):
    """Raise if a preset's class-required params are unset (e.g. tcp ``port``).

    Some preset classes have no sensible default for a parameter (TcpDetector's
    port, NodeCategorySettingPolicy's category), so a user who picks the preset
    and skips the field would render a no-op/server-rejected rule. Caught here
    rather than at push time.
    """
    params = rule.parameters or {}
    missing = [key for key in required if not str(params.get(key, "")).strip()]
    if missing:
        raise ValidationError(
            {
                "parameters": f"The {rule.preset!r} preset requires: "
                f"{', '.join(missing)}."
            }
        )


class Requisition(NetBoxModel):
    """A user-named OpenNMS Foreign Source (definition + filter-scoped requisition).

    The **name** is the Foreign Source name (URL-path-safe, R1/H7). Membership is a
    live NetBox **filter** over the selected ``object_types``; an object matching
    two Requisitions' filters is a blocking **conflict** the user resolves (C1) —
    there is no automatic precedence. It owns its detectors/policies/scan-interval
    (the definition) and a set of declared ``services`` applied to every member's
    interfaces (R5).
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)
    # Which NetBox object types this Requisition's filter draws from.
    object_types = models.CharField(
        max_length=10,
        choices=ObjectTypeChoices,
        default=ObjectTypeChoices.BOTH,
    )
    # NetBox FilterSet query params (e.g. {"role": ["switch"], "tag": ["critical"]})
    # applied to the Device/VM filtersets to compute members (R2).
    filter_params = models.JSONField(default=dict, blank=True)
    # OpenNMS scan interval (a duration string, e.g. "1d", "30m").
    scan_interval = models.CharField(max_length=32, default="1d")
    # Which of a node's NetBox IPs become interfaces before per-object overrides.
    default_interfaces = models.CharField(
        max_length=16,
        choices=InterfaceScopeChoices,
        default=InterfaceScopeChoices.PRIMARY,
    )
    # Declared service names applied to every member's interfaces (R5); a
    # per-object override may add extra or suppress one of these.
    services = models.JSONField(default=list, blank=True)
    # OpenNMS monitoring location (which Minion polls these nodes). Blank falls
    # back to the configured default location at render time.
    location = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("name",)
        verbose_name = "requisition"
        verbose_name_plural = "requisitions"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:requisition", args=[self.pk])

    def clean(self):
        super().clean()
        try:
            validate_requisition_name(self.name)
        except ValueError as exc:
            raise ValidationError({"name": str(exc)}) from exc
        try:
            validate_location_name(self.location)
        except ValueError as exc:
            raise ValidationError({"location": str(exc)}) from exc
        if not isinstance(self.filter_params, dict):
            raise ValidationError({"filter_params": "Filter must be a mapping."})
        # Empty / no-effective-key filters are rejected in the resolution layer
        # (which knows the filtersets' keys); the model only guards the shape.
        if not isinstance(self.services, list):
            raise ValidationError({"services": "Services must be a list."})
        _validate_service_names(self.services, "services")


class _ProvisioningRule(NetBoxModel):
    """Shared base for a detector or policy: name + (preset|class) + parameters."""

    name = models.CharField(max_length=100)
    # The OpenNMS class; filled from the preset when one is chosen, or entered
    # directly for a freeform rule.
    rule_class = models.CharField(max_length=255, blank=True)
    parameters = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class MonitoringDetector(_ProvisioningRule):
    """A detector on a Requisition's definition (OpenNMS auto-discovers services)."""

    requisition = models.ForeignKey(
        to=Requisition, on_delete=models.CASCADE, related_name="detectors"
    )
    preset = models.CharField(max_length=50, choices=DetectorPresetChoices, blank=True)

    class Meta:
        ordering = ("requisition", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("requisition", "name"),
                name="%(app_label)s_%(class)s_unique_name",
            ),
        ]
        verbose_name = "monitoring detector"
        verbose_name_plural = "monitoring detectors"

    def _apply_preset(self):
        # A KNOWN preset owns the class: it is (re)derived from the preset and the
        # user can't override it. An unknown preset (admin-extended via FIELD_CHOICES
        # with no registry entry) leaves any existing class untouched — never blanked.
        # Defaults are seeded only when parameters are empty, so a user-tuned/-deleted
        # parameter is not resurrected. Applied in both clean() and save() so it holds
        # on every path — the API/bulk paths don't run clean().
        if self.preset:
            cls, defaults = resolve_detector(self.preset)
            if cls:
                self.rule_class = cls
                if not self.parameters:
                    self.parameters = dict(defaults)

    def clean(self):
        super().clean()
        self._apply_preset()
        if not self.rule_class:
            raise ValidationError(
                {"rule_class": "Choose a preset or enter a detector class."}
            )
        _require_preset_params(self, detector_required_params(self.preset))

    def save(self, *args, **kwargs):
        self._apply_preset()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringdetector", args=[self.pk])


class MonitoringPolicy(_ProvisioningRule):
    """A policy on a Requisition's definition (categories, interface management…)."""

    requisition = models.ForeignKey(
        to=Requisition, on_delete=models.CASCADE, related_name="policies"
    )
    preset = models.CharField(max_length=50, choices=PolicyPresetChoices, blank=True)

    class Meta:
        ordering = ("requisition", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("requisition", "name"),
                name="%(app_label)s_%(class)s_unique_name",
            ),
        ]
        verbose_name = "monitoring policy"
        verbose_name_plural = "monitoring policies"

    def _apply_preset(self):
        # A known preset owns the class (see MonitoringDetector._apply_preset).
        if self.preset:
            cls, defaults = resolve_policy(self.preset)
            if cls:
                self.rule_class = cls
                if not self.parameters:
                    self.parameters = dict(defaults)

    def clean(self):
        super().clean()
        self._apply_preset()
        if not self.rule_class:
            raise ValidationError(
                {"rule_class": "Choose a preset or enter a policy class."}
            )
        _require_preset_params(self, policy_required_params(self.preset))

    def save(self, *args, **kwargs):
        self._apply_preset()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringpolicy", args=[self.pk])


class MonitoringOverride(NetBoxModel):
    """An optional per-object exception to its Requisition's defaults (R5/R6/H3).

    Absent an override, a matching Device/VM is monitored by the Requisition that
    claims it. One override per object (the GFK unique constraint also indexes the
    GFK). Resolution applies an override by object (via the GFK), so an override
    on an object that no Requisition currently claims is simply never applied.
    """

    assigned_object_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.PROTECT,
        limit_choices_to=ASSIGNMENT_MODELS,
        related_name="+",
    )
    assigned_object_id = models.PositiveBigIntegerField()
    assigned_object = GenericForeignKey(
        ct_field="assigned_object_type",
        fk_field="assigned_object_id",
    )
    # Drop this object from monitoring entirely (monitored nowhere, M2; an
    # excluded object also never counts as a filter conflict, C3).
    exclude = models.BooleanField(default=False)
    # Override the management (primary) interface; null = use the object's primary_ip.
    management_ip = models.ForeignKey(
        to="ipam.IPAddress",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Extra interfaces to monitor beyond the scope default (AD-15).
    additional_ips = models.ManyToManyField(
        to="ipam.IPAddress", related_name="+", blank=True
    )
    # Declared-service names to suppress for this object (R5); effective services =
    # (requisition.services ∪ added MonitoredService) − suppressed_services.
    suppressed_services = models.JSONField(default=list, blank=True)
    # Override the OpenNMS location for just this object; blank = use the Requisition's.
    location = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(
                fields=("assigned_object_type", "assigned_object_id"),
                name="%(app_label)s_%(class)s_unique_object",
            ),
        ]
        verbose_name = "monitoring override"
        verbose_name_plural = "monitoring overrides"

    def __str__(self):
        if self.assigned_object is not None:
            return f"Override: {self.assigned_object}"
        return "Monitoring override"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringoverride", args=[self.pk])

    def clean(self):
        super().clean()
        try:
            validate_location_name(self.location)
        except ValueError as exc:
            raise ValidationError({"location": str(exc)}) from exc
        if not isinstance(self.suppressed_services, list):
            raise ValidationError(
                {"suppressed_services": "Suppressed services must be a list."}
            )
        _validate_service_names(self.suppressed_services, "suppressed_services")


class MonitoredService(NetBoxModel):
    """An explicit service ADDED on a Monitoring Override's interface (R5).

    The Requisition's declared services are the default; this is the additive
    per-object exception ("also monitor X on this IP"). ``name`` is drawn from the
    admin-extensible ``ServiceChoices``.
    """

    override = models.ForeignKey(
        to=MonitoringOverride, on_delete=models.CASCADE, related_name="services"
    )
    ip_address = models.ForeignKey(
        to="ipam.IPAddress", on_delete=models.CASCADE, related_name="+"
    )
    name = models.CharField(max_length=100, choices=ServiceChoices)

    class Meta:
        ordering = ("override", "ip_address", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("override", "ip_address", "name"),
                name="%(app_label)s_%(class)s_unique_service",
            ),
        ]
        verbose_name = "monitored service"
        verbose_name_plural = "monitored services"

    def __str__(self):
        return f"{self.name} on {self.ip_address}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoredservice", args=[self.pk])

    def clean(self):
        super().clean()
        if (
            self.override_id
            and self.ip_address_id
            and self.ip_address_id not in override_ip_pks(self.override)
        ):
            raise ValidationError(
                {"ip_address": "Must be one of the override's IPs."}
            )


class DeployedForeignSource(models.Model):
    """A Foreign Source name NetBox has pushed to OpenNMS — the reconciler's
    ownership record (review #4).

    Requisition names are user-chosen, so the drift reconciler can't use a
    ``netbox.`` prefix to tell ours from foreign requisitions. A row is written when
    a sync succeeds and removed when the Foreign Source's shell is deleted, so
    ``orphaned_foreign_sources`` scopes cleanup to exactly the names we manage and
    never touches a requisition NetBox didn't create.
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "deployed foreign source"
        verbose_name_plural = "deployed foreign sources"

    def __str__(self):
        return self.name


def object_ip_pks(target):
    """PKs of the IPs assigned to a Device/VM's interfaces (its own addresses)."""
    pks = set()
    interfaces = getattr(target, "interfaces", None)
    if interfaces is None:
        return pks
    for interface in interfaces.all():
        pks.update(interface.ip_addresses.values_list("pk", flat=True))
    return pks


def override_ip_pks(override):
    """PKs of an override's interfaces: its management IP + its additional IPs."""
    pks = set()
    if override.management_ip_id:
        pks.add(override.management_ip_id)
    pks.update(override.additional_ips.values_list("pk", flat=True))
    return pks
