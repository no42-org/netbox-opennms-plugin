# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Forms for plugin models (Requisition redesign)."""

import logging

from dcim.models import Device
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from extras.models import SavedFilter
from ipam.models import IPAddress
from netbox.forms import NetBoxModelForm
from utilities.forms.fields import (
    DynamicModelChoiceField,
    JSONField,
)
from virtualization.models import VirtualMachine

from .catalog import get_detector_catalog, get_policy_catalog
from .choices import ObjectTypeChoices, ServiceChoices
from .membership import filter_errors
from .models import (
    AssetMapping,
    MetadataEntry,
    MonitoredInterface,
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
)

logger = logging.getLogger("netbox_opennms")


class RequisitionForm(NetBoxModelForm):
    """Create/edit a Requisition (one user-named OpenNMS Foreign Source)."""

    import_from_saved_filter = forms.ModelChoiceField(
        queryset=SavedFilter.objects.filter(
            Q(object_types__app_label="dcim", object_types__model="device")
            | Q(
                object_types__app_label="virtualization",
                object_types__model="virtualmachine",
            )
        ).distinct(),
        required=False,
        label=_("Import from Saved Filter"),
        help_text=_(
            "Copy a NetBox Device/VM Saved Filter's parameters into the filter "
            "below — a one-time copy, with no live link to the Saved Filter."
        ),
    )
    filter_params = JSONField(
        required=False,
        label=_("Filter"),
        help_text=_(
            "NetBox filter parameters, e.g. "
            '{"role": ["switch"], "tag": ["critical"]}. Applied to the selected '
            "object types to compute members."
        ),
    )
    services = forms.MultipleChoiceField(
        choices=ServiceChoices,
        required=False,
        label=_("Declared services"),
        help_text=_("Applied to every member's interfaces (overridable per object)."),
    )

    class Meta:
        model = Requisition
        fields = (
            "name",
            "description",
            "object_types",
            "import_from_saved_filter",
            "filter_params",
            "scan_interval",
            "default_interfaces",
            "services",
            "location",
            "tags",
        )

    def clean(self):
        super().clean()
        # A one-shot copy: importing a Saved Filter seeds the filter params (no live
        # link, R2). Done before the guard so the copied params are checked. Refuse
        # to silently discard a filter the user also typed in the same submit.
        saved = self.cleaned_data.get("import_from_saved_filter")
        if saved is not None:
            if self.cleaned_data.get("filter_params"):
                self.add_error(
                    "import_from_saved_filter",
                    _(
                        "Clear the Filter field to import a Saved Filter, or drop "
                        "the import and edit the filter directly — not both."
                    ),
                )
            else:
                self.cleaned_data["filter_params"] = dict(saved.parameters or {})
        # Reject unknown/empty filters here (the same guard the resolver uses), so a
        # typo can't be saved into a fleet-wide catch-all (H1). Read from
        # cleaned_data — self.instance isn't populated until _post_clean(), after this.
        if not self.errors:
            probe = Requisition(
                object_types=self.cleaned_data.get("object_types")
                or ObjectTypeChoices.BOTH,
                filter_params=self.cleaned_data.get("filter_params") or {},
            )
            for error in filter_errors(probe):
                self.add_error("filter_params", error)
        return self.cleaned_data


class _PresetRuleForm(NetBoxModelForm):
    """Shared: the preset owns the rule class, so it isn't user-editable.

    The class is filled from the preset by the model; the form makes ``rule_class``
    optional (a preset provides it) and locks the field once a preset is set —
    freeform entry is only for a rule with no preset.

    When the rule's class is known, the parameter editor is driven by the **live
    OpenNMS catalog** (``catalog.py``, RD-1): one field per catalog parameter —
    enum parameters (with discovered ``options``) render as a dropdown, others as
    text, seeded with the overlay's label/default. The raw ``parameters`` JSON is
    hidden and reassembled from those fields on save. If OpenNMS is unreachable the
    editor degrades to the curated overlay and notes it — the save is never blocked.
    """

    requisition = DynamicModelChoiceField(
        queryset=Requisition.objects.all(), label=_("Requisition")
    )

    def _get_catalog(self):
        """The detector/policy catalog, or ``None``. Overridden by subclasses."""
        return None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["rule_class"]
        field.required = False
        field.help_text = _(
            "Set automatically from the preset and locked; enter a class only for "
            "a freeform rule (no preset selected)."
        )
        # An existing preset-backed rule: the class is fixed to the preset's.
        if self.instance and self.instance.pk and self.instance.preset:
            field.disabled = True
        self._param_fields = []
        self._add_catalog_param_fields()

    def _catalog_entry(self):
        """The catalog entry for this rule's class/preset, and the live-avail flag."""
        rule_class = getattr(self.instance, "rule_class", "") or ""
        preset = getattr(self.instance, "preset", "") or ""
        # Nothing to look up (a blank add form or a freeform rule) — don't fetch the
        # catalog just to compute an entry that is structurally always None.
        if not rule_class and not preset:
            return None, False
        try:
            catalog = self._get_catalog()
        except Exception:  # noqa: BLE001 — the editor must never fail on discovery
            # _get_catalog degrades network errors internally; anything here is a
            # real bug — log it and flag degraded so the UI note fires (never silent).
            logger.exception("detector/policy catalog lookup failed")
            return None, True
        if catalog is None:
            return None, False
        entry = catalog.by_class(rule_class) if rule_class else None
        if entry is None and preset:
            entry = catalog.by_preset(preset)
        return entry, catalog.live_unavailable

    def _add_catalog_param_fields(self):
        entry, live_unavailable = self._catalog_entry()
        if live_unavailable:
            self.fields["rule_class"].help_text += _(
                " Live OpenNMS catalog unavailable — showing curated presets."
            )
        if entry is None or not entry.parameters:
            return
        # Drive parameters from the schema; hide the raw JSON and rebuild it in clean().
        self.fields.pop("parameters", None)
        current = (getattr(self.instance, "parameters", None)) or {}
        for param in entry.parameters:
            name = f"param_{param.key}"
            initial = current.get(param.key, param.default)
            hint = _("required by OpenNMS") if param.required else ""
            if param.options:
                self.fields[name] = forms.ChoiceField(
                    label=param.label or param.key,
                    required=False,
                    choices=[("", "---------")] + [(o, o) for o in param.options],
                    initial=initial if initial in param.options else "",
                    help_text=hint,
                )
            else:
                self.fields[name] = forms.CharField(
                    label=param.label or param.key,
                    required=False,
                    initial=initial,
                    help_text=hint,
                )
            self._param_fields.append((name, param.key))

    def clean(self):
        super().clean()
        # Rebuild parameters from the per-parameter fields, but PRESERVE any stored
        # key the catalog didn't surface as a field (freeform/API-set keys, or keys
        # dropped when the catalog is degraded to the overlay) — only touch the keys
        # we actually rendered. A blank field clears its own key; the model's
        # required-param guard still fires for a genuinely missing required value.
        if self._param_fields:
            params = dict(self.instance.parameters or {})
            for name, key in self._param_fields:
                value = self.cleaned_data.get(name)
                if value in (None, ""):
                    params.pop(str(key), None)
                else:
                    params[str(key)] = str(value)
            self.instance.parameters = params
        return self.cleaned_data


class MonitoringDetectorForm(_PresetRuleForm):
    """Add/edit a detector on a Requisition (a preset, or a freeform class)."""

    def _get_catalog(self):
        return get_detector_catalog()

    class Meta:
        model = MonitoringDetector
        fields = ("requisition", "name", "preset", "rule_class", "parameters", "tags")


class MonitoringPolicyForm(_PresetRuleForm):
    """Add/edit a policy on a Requisition (a preset, or a freeform class)."""

    def _get_catalog(self):
        return get_policy_catalog()

    class Meta:
        model = MonitoringPolicy
        fields = ("requisition", "name", "preset", "rule_class", "parameters", "tags")


class MonitoringOverrideForm(NetBoxModelForm):
    """Per-object exception. The target is one of Device / Virtual Machine."""

    device = DynamicModelChoiceField(
        queryset=Device.objects.all(), required=False, label=_("Device")
    )
    virtual_machine = DynamicModelChoiceField(
        queryset=VirtualMachine.objects.all(),
        required=False,
        label=_("Virtual Machine"),
    )
    management_ip = DynamicModelChoiceField(
        queryset=IPAddress.objects.all(),
        required=False,
        label=_("Management IP"),
        query_params={
            "device_id": "$device",
            "virtual_machine_id": "$virtual_machine",
        },
        help_text=_("Overrides the object's primary IP if set."),
    )
    suppressed_services = forms.MultipleChoiceField(
        choices=ServiceChoices,
        required=False,
        label=_("Suppress declared services"),
        help_text=_("Declared services to remove for this object only."),
    )

    class Meta:
        model = MonitoringOverride
        fields = (
            "device",
            "virtual_machine",
            "exclude",
            "management_ip",
            "management_role",
            "suppressed_services",
            "location",
            "tags",
        )

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        initial = kwargs.get("initial", {}).copy()
        if instance is not None and instance.assigned_object_id:
            obj = instance.assigned_object
            if isinstance(obj, Device):
                initial.setdefault("device", obj)
            elif isinstance(obj, VirtualMachine):
                initial.setdefault("virtual_machine", obj)
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        device = self.cleaned_data.get("device")
        virtual_machine = self.cleaned_data.get("virtual_machine")
        if bool(device) == bool(virtual_machine):
            raise ValidationError(_("Select exactly one of Device or Virtual Machine."))
        target = device or virtual_machine
        self.instance.assigned_object = target

        # The unique constraint references assigned_object_type/_id (not form
        # fields), so surface a clean duplicate error instead of an IntegrityError.
        content_type = ContentType.objects.get_for_model(target)
        duplicate = (
            MonitoringOverride.objects.filter(
                assigned_object_type=content_type,
                assigned_object_id=target.pk,
            )
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if duplicate:
            raise ValidationError(_("This object already has a Monitoring Override."))
        return self.cleaned_data


class MonitoredServiceForm(NetBoxModelForm):
    """Add/edit an explicit added service on one of an override's interfaces."""

    override = DynamicModelChoiceField(
        queryset=MonitoringOverride.objects.all(), label=_("Monitoring Override")
    )
    ip_address = DynamicModelChoiceField(
        queryset=IPAddress.objects.all(),
        label=_("Interface IP"),
        help_text=_("Must be the override's management IP or an additional IP."),
    )

    class Meta:
        model = MonitoredService
        fields = ("override", "ip_address", "name", "tags")


class MonitoredInterfaceForm(NetBoxModelForm):
    """Add/edit an additional interface (with its SNMP role) on an override (RD-5)."""

    override = DynamicModelChoiceField(
        queryset=MonitoringOverride.objects.all(), label=_("Monitoring Override")
    )
    ip_address = DynamicModelChoiceField(
        queryset=IPAddress.objects.all(),
        label=_("Interface IP"),
        help_text=_("An IP of the override's object (not its management IP)."),
    )

    class Meta:
        model = MonitoredInterface
        fields = ("override", "ip_address", "role", "tags")


class AssetMappingForm(NetBoxModelForm):
    """Map a NetBox attribute to an OpenNMS asset field on a Requisition (RD-2)."""

    requisition = DynamicModelChoiceField(
        queryset=Requisition.objects.all(), label=_("Requisition")
    )

    class Meta:
        model = AssetMapping
        fields = ("requisition", "netbox_source", "asset_field", "tags")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .catalog import get_asset_fields

        fields = sorted(get_asset_fields())
        current = getattr(self.instance, "asset_field", "") or ""
        if current and current not in fields:
            fields.append(current)
        self.fields["asset_field"] = forms.ChoiceField(
            choices=[(f, f) for f in fields],
            label=_("OpenNMS asset field"),
            help_text=_("Discovered from OpenNMS (falls back to the known field set)."),
        )


class MetadataEntryForm(NetBoxModelForm):
    """Define a metadata triad at a scope on a Requisition (RD-3)."""

    requisition = DynamicModelChoiceField(
        queryset=Requisition.objects.all(), label=_("Requisition")
    )

    class Meta:
        model = MetadataEntry
        fields = (
            "requisition",
            "scope",
            "context",
            "key",
            "value_source",
            "literal_value",
            "tags",
        )
