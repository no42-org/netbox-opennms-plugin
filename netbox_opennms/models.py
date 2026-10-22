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

# Not ``from . import config``: the package's ``config`` attribute is the
# PluginConfig class until the submodule is imported, so that form can bind
# the class. This form always imports (and binds) the module.
import netbox_opennms.config as config

from .choices import (
    DetectorPresetChoices,
    InterfaceRoleChoices,
    InterfaceScopeChoices,
    MetadataScopeChoices,
    NetBoxSourceChoices,
    ObjectTypeChoices,
    PolicyPresetChoices,
    ServerOriginChoices,
    ServiceChoices,
)
from .crypto import Undecryptable
from .derivation import validate_location_name, validate_requisition_name
from .fields import EncryptedCharField, EncryptedJSONField
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


class OpenNMSServer(NetBoxModel):
    """An OpenNMS instance that Requisitions are synced to.

    A **provisioned** server is declared in ``PLUGINS_CONFIG``; the row carries
    only its name and URL and the credentials are read from config at use. A
    **local** server is created in the UI and stores its credentials here,
    Fernet-encrypted. ``password`` and ``headers`` never enter the changelog.
    """

    name = models.CharField(max_length=100, unique=True)
    origin = models.CharField(
        max_length=16,
        choices=ServerOriginChoices,
        default=ServerOriginChoices.LOCAL,
    )
    # Base URL including the context path. A CharField, not a URLField: Django's
    # URLValidator rejects single-label hosts such as ``http://opennms:8980``.
    url = models.CharField(max_length=255)
    username = models.CharField(max_length=100, blank=True)
    password = EncryptedCharField(blank=True)
    # Extra HTTP headers sent with every request (e.g. for an auth proxy).
    headers = EncryptedJSONField(default=dict, blank=True)
    # Monitoring Location used when a Requisition and its member leave it blank.
    default_location = models.CharField(max_length=255, blank=True, default="")
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "OpenNMS server"
        verbose_name_plural = "OpenNMS servers"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:opennmsserver", args=[self.pk])

    def clean(self):
        super().clean()
        errors = {}
        if self.url and not self.url.startswith(("https://", "http://")):
            errors["url"] = "The URL must start with https:// or http://."
        try:
            validate_location_name(self.default_location)
        except ValueError as exc:
            errors["default_location"] = str(exc)
        if not isinstance(self.headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.headers.items()
        ):
            errors["headers"] = "Headers must be a mapping of strings to strings."
        if self.origin == ServerOriginChoices.LOCAL:
            if not self.username:
                errors["username"] = "A local server needs a username."
            if not self.password and not self._stored_password():
                errors["password"] = "A local server needs a password."
        if errors:
            raise ValidationError(errors)

    def _stored_password(self):
        """The saved password ("" for an unsaved row); a blank edit keeps it."""
        if not self.pk:
            return ""
        stored = (
            OpenNMSServer.objects.filter(pk=self.pk)
            .values_list("password", flat=True)
            .first()
        )
        return stored or ""

    def serialize_object(self, exclude=None):
        # Secrets (even as ciphertext) must never land in ObjectChange data.
        exclude = [*(exclude or []), "password", "headers"]
        return super().serialize_object(exclude=exclude)

    @property
    def is_provisioned(self):
        return self.origin == ServerOriginChoices.PROVISIONED

    @property
    def is_missing(self):
        """A provisioned row whose ``PLUGINS_CONFIG`` entry is gone."""
        return self.is_provisioned and config.server_entry(self.name) is None

    @property
    def credentials_ok(self):
        """False when stored secrets cannot be decrypted with the current key."""
        return not isinstance(self.password, Undecryptable) and not isinstance(
            self.headers, Undecryptable
        )

    @property
    def effective_username(self):
        if self.is_provisioned:
            entry = config.server_entry(self.name)
            return entry["username"] if entry else ""
        return self.username


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
    # The OpenNMS server this Foreign Source is synced to.
    server = models.ForeignKey(
        to=OpenNMSServer, on_delete=models.PROTECT, related_name="requisitions"
    )
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
    # The SNMP role of the management interface (RD-5). Primary by default; set to
    # Secondary/Not-eligible to promote an additional interface to Primary instead.
    management_role = models.CharField(
        max_length=1,
        choices=InterfaceRoleChoices,
        default=InterfaceRoleChoices.PRIMARY,
    )
    # Extra interfaces are their own child rows (MonitoredInterface), each with a
    # per-interface SNMP role (RD-5), reachable via ``override.interfaces``.
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
        # At most one Primary per node (RD-5): making the management interface
        # Primary while an additional interface is already Primary is rejected —
        # the other direction is caught in MonitoredInterface.clean().
        if (
            self.pk
            and self.management_role == InterfaceRoleChoices.PRIMARY
            and self.interfaces.filter(role=InterfaceRoleChoices.PRIMARY).exists()
        ):
            raise ValidationError(
                {
                    "management_role": "An additional interface is already Primary; "
                    "at most one Primary interface per node."
                }
            )


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


class MonitoredInterface(NetBoxModel):
    """An additional interface on a Monitoring Override, with its SNMP role (RD-5).

    Beyond the management (primary) interface, an override may add interfaces from
    the object's own NetBox IPs, each with a role — Primary / Secondary /
    Not-eligible (OpenNMS ``snmp-primary`` P/S/N). A node has **at most one**
    Primary (the management interface by default), enforced in ``clean()`` across
    the management interface + these rows.
    """

    override = models.ForeignKey(
        to=MonitoringOverride, on_delete=models.CASCADE, related_name="interfaces"
    )
    ip_address = models.ForeignKey(
        to="ipam.IPAddress", on_delete=models.CASCADE, related_name="+"
    )
    role = models.CharField(
        max_length=1,
        choices=InterfaceRoleChoices,
        default=InterfaceRoleChoices.NOT_ELIGIBLE,
    )

    class Meta:
        ordering = ("override", "ip_address")
        constraints = [
            models.UniqueConstraint(
                fields=("override", "ip_address"),
                name="%(app_label)s_%(class)s_unique_ip",
            ),
        ]
        verbose_name = "monitored interface"
        verbose_name_plural = "monitored interfaces"

    def __str__(self):
        return f"{self.ip_address} ({self.get_role_display()})"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoredinterface", args=[self.pk])

    def clean(self):
        super().clean()
        if not self.override_id or not self.ip_address_id:
            return
        target = self.override.assigned_object
        # An additional interface must be one of the object's own IPs (AD-15).
        if target is not None and self.ip_address_id not in object_ip_pks(target):
            raise ValidationError(
                {"ip_address": "Must be an IP assigned to the override's object."}
            )
        # The management IP is the primary interface, modelled separately.
        management = self.override.management_ip or (
            target.primary_ip if target is not None else None
        )
        if management is not None and self.ip_address_id == management.pk:
            raise ValidationError(
                {"ip_address": "This is the management IP (the primary interface)."}
            )
        # At most one Primary per node (RD-5).
        if self.role == InterfaceRoleChoices.PRIMARY:
            if self.override.management_role == InterfaceRoleChoices.PRIMARY:
                raise ValidationError(
                    {
                        "role": "The management interface is Primary; set the "
                        "override's management role to Secondary/Not-eligible "
                        "before making another interface Primary."
                    }
                )
            others = self.override.interfaces.exclude(pk=self.pk).filter(
                role=InterfaceRoleChoices.PRIMARY
            )
            if others.exists():
                raise ValidationError(
                    {"role": "Another interface is already Primary; at most one "
                     "Primary interface per node."}
                )


class AssetMapping(NetBoxModel):
    """Maps a NetBox attribute to an OpenNMS node **asset** field (RD-2).

    Assets are a *fixed* schema (the ``OnmsAssetRecord`` field set, discovered via
    ``catalog.get_asset_fields``); ``asset_field`` is validated against it. The value
    is resolved per member from ``netbox_source`` at render time (unresolved → the
    ``<asset>`` is omitted). Data with no matching asset field belongs in metadata.
    """

    requisition = models.ForeignKey(
        to=Requisition, on_delete=models.CASCADE, related_name="asset_mappings"
    )
    netbox_source = models.CharField(max_length=100, choices=NetBoxSourceChoices)
    asset_field = models.CharField(max_length=64)

    class Meta:
        ordering = ("requisition", "asset_field")
        constraints = [
            models.UniqueConstraint(
                fields=("requisition", "asset_field"),
                name="%(app_label)s_%(class)s_unique_field",
            ),
        ]
        verbose_name = "asset mapping"
        verbose_name_plural = "asset mappings"

    def __str__(self):
        return f"{self.netbox_source} → {self.asset_field}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:assetmapping", args=[self.pk])

    def clean(self):
        super().clean()
        # Validate against the discovered/known OnmsAssetRecord field set (RD-2).
        from .catalog import get_asset_fields

        if self.asset_field and self.asset_field not in get_asset_fields():
            raise ValidationError(
                {"asset_field": f"Unknown OpenNMS asset field {self.asset_field!r}."}
            )


class MetadataEntry(NetBoxModel):
    """A metadata triad at node / interface / service scope on a Requisition (RD-3).

    Rendered as ``<meta-data context=… key=… value=…/>`` under the matching element.
    ``context`` defaults to ``requisition``; a custom context MUST be ``X-``-prefixed.
    The value is a literal or resolved per member from ``value_source`` (a curated
    attribute or ``cf_<name>``); an unresolved value omits the ``<meta-data>``.
    """

    requisition = models.ForeignKey(
        to=Requisition, on_delete=models.CASCADE, related_name="metadata_entries"
    )
    scope = models.CharField(
        max_length=16, choices=MetadataScopeChoices, default=MetadataScopeChoices.NODE
    )
    context = models.CharField(max_length=64, default="requisition")
    key = models.CharField(max_length=100)
    # A curated attribute key or ``cf_<name>``; blank → use ``literal_value``.
    value_source = models.CharField(max_length=100, blank=True)
    literal_value = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ("requisition", "scope", "context", "key")
        constraints = [
            models.UniqueConstraint(
                fields=("requisition", "scope", "context", "key"),
                name="%(app_label)s_%(class)s_unique_meta",
            ),
        ]
        verbose_name = "metadata entry"
        verbose_name_plural = "metadata entries"

    def __str__(self):
        return f"{self.scope}:{self.context}:{self.key}"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:metadataentry", args=[self.pk])

    def clean(self):
        super().clean()
        if self.context != "requisition" and not self.context.startswith("X-"):
            raise ValidationError(
                {"context": "A custom context must be prefixed 'X-' (or use "
                 "'requisition')."}
            )
        if not self.value_source and not self.literal_value:
            raise ValidationError(
                "Set a value source or a literal value."
            )
        if self.value_source and self.literal_value:
            raise ValidationError(
                "Set either a value source or a literal value, not both."
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

    server = models.ForeignKey(
        to=OpenNMSServer,
        on_delete=models.CASCADE,
        related_name="deployed_foreign_sources",
    )
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("server", "name"),
                name="netbox_opennms_deployedforeignsource_unique_server_name",
            ),
        ]
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
    """PKs of an override's interfaces: management IP + additional interfaces."""
    pks = set()
    if override.management_ip_id:
        pks.add(override.management_ip_id)
    pks.update(override.interfaces.values_list("ip_address_id", flat=True))
    return pks
