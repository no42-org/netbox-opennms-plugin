# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Plugin data models (Epic 5 — profile = reusable detector/policy template).

A **MonitoringProfile** is an object-independent template of OpenNMS detectors +
policies + scan-interval (the foreign-source definition). It is **assigned** to a
(site[, role]) scope via **MonitoringAssignment**; membership is a live NetBox
query (the Devices/VMs in that site+role = one Foreign Source). A
**MonitoringOverride** is an optional per-object exception (exclude / override
management IP / override location / explicit services). See the OpenSpec change
``rethink-monitoring-profiles`` for the full design (incl. the AD-11 reversal).
"""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

from .choices import (
    DetectorPresetChoices,
    InterfaceScopeChoices,
    PolicyPresetChoices,
    ServiceChoices,
)
from .derivation import validate_location_name
from .presets import resolve_detector, resolve_policy

# A Monitoring Override may attach to a Device or a VirtualMachine.
ASSIGNMENT_MODELS = models.Q(
    models.Q(app_label="dcim", model="device")
    | models.Q(app_label="virtualization", model="virtualmachine")
)


class MonitoringProfile(NetBoxModel):
    """A reusable OpenNMS provisioning template (detectors + policies + scan).

    Object-independent: a few profiles (e.g. "Network device", "Server") are
    authored once and assigned to many site/role scopes. Renders to a
    foreign-source definition; OpenNMS then discovers services via the detectors
    (reverses v1's AD-11 "explicit services only").
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=200, blank=True)
    # OpenNMS scan interval (a duration string, e.g. "1d", "30m"); how often
    # detectors re-run. Meaningful again now that detection is on.
    scan_interval = models.CharField(max_length=32, default="1d")
    # Which of a node's NetBox IPs become interfaces before per-object overrides.
    default_interfaces = models.CharField(
        max_length=16,
        choices=InterfaceScopeChoices,
        default=InterfaceScopeChoices.PRIMARY,
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "monitoring profile"
        verbose_name_plural = "monitoring profiles"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringprofile", args=[self.pk])


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
    """A detector on a profile's foreign-source definition (auto-discovers services)."""

    profile = models.ForeignKey(
        to=MonitoringProfile, on_delete=models.CASCADE, related_name="detectors"
    )
    preset = models.CharField(max_length=50, choices=DetectorPresetChoices, blank=True)

    class Meta:
        ordering = ("profile", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "name"),
                name="%(app_label)s_%(class)s_unique_name",
            ),
        ]
        verbose_name = "monitoring detector"
        verbose_name_plural = "monitoring detectors"

    def clean(self):
        super().clean()
        # A preset fills the class (and seeds defaults) so the rule is self-contained.
        if self.preset and not self.rule_class:
            cls, defaults = resolve_detector(self.preset)
            self.rule_class = cls or ""
            self.parameters = {**defaults, **(self.parameters or {})}
        if not self.rule_class:
            raise ValidationError(
                {"rule_class": "Choose a preset or enter a detector class."}
            )

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringdetector", args=[self.pk])


class MonitoringPolicy(_ProvisioningRule):
    """A policy on a profile's foreign-source definition (categories, interfaces…)."""

    profile = models.ForeignKey(
        to=MonitoringProfile, on_delete=models.CASCADE, related_name="policies"
    )
    preset = models.CharField(max_length=50, choices=PolicyPresetChoices, blank=True)

    class Meta:
        ordering = ("profile", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "name"),
                name="%(app_label)s_%(class)s_unique_name",
            ),
        ]
        verbose_name = "monitoring policy"
        verbose_name_plural = "monitoring policies"

    def clean(self):
        super().clean()
        if self.preset and not self.rule_class:
            cls, defaults = resolve_policy(self.preset)
            self.rule_class = cls or ""
            self.parameters = {**defaults, **(self.parameters or {})}
        if not self.rule_class:
            raise ValidationError(
                {"rule_class": "Choose a preset or enter a policy class."}
            )

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringpolicy", args=[self.pk])


class MonitoringAssignment(NetBoxModel):
    """Binds a Monitoring Profile to a (site[, role]) scope = one Foreign Source.

    ``role`` NULL means site-level (applies to every role in the site). Exactly
    one assignment may govern a given (site, role) — including the site-level row
    — enforced with a NULLS-NOT-DISTINCT unique constraint. The (site, role) that
    governs an object resolves to the more specific assignment (AD/D9).
    """

    profile = models.ForeignKey(
        to=MonitoringProfile, on_delete=models.PROTECT, related_name="assignments"
    )
    site = models.ForeignKey(to="dcim.Site", on_delete=models.CASCADE, related_name="+")
    role = models.ForeignKey(
        to="dcim.DeviceRole",
        on_delete=models.CASCADE,
        related_name="+",
        null=True,
        blank=True,
    )
    # OpenNMS monitoring location (which Minion polls these nodes). Blank falls
    # back to the configured default location at render time.
    location = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("site", "role")
        constraints = [
            models.UniqueConstraint(
                fields=("site", "role"),
                name="%(app_label)s_%(class)s_unique_scope",
                nulls_distinct=False,
            ),
        ]
        verbose_name = "monitoring assignment"
        verbose_name_plural = "monitoring assignments"

    def __str__(self):
        scope = self.site.name
        if self.role is not None:
            scope = f"{self.site.name} / {self.role}"
        return f"{self.profile} → {scope}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringassignment", args=[self.pk])

    def clean(self):
        super().clean()
        try:
            validate_location_name(self.location)
        except ValueError as exc:
            raise ValidationError({"location": str(exc)}) from exc


class MonitoringOverride(NetBoxModel):
    """An optional per-object exception to its scope's defaults (Epic 5).

    Absent an override, a matching Device/VM is monitored by its scope. One
    override per object (the GFK unique constraint also indexes the GFK).
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
    # Drop this object from monitoring entirely.
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
    # Override the OpenNMS location for just this object; blank = use the scope's.
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


class MonitoredService(NetBoxModel):
    """An explicit service on a Monitoring Override's interface (Epic 5).

    Detectors are the default service source; this is the rare additive exception
    ("also monitor X here"). ``name`` is drawn from the admin-extensible
    ``ServiceChoices``.
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
