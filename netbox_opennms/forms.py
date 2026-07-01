# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Forms for plugin models (Requisition redesign)."""

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
    DynamicModelMultipleChoiceField,
    JSONField,
)
from virtualization.models import VirtualMachine

from .choices import ObjectTypeChoices, ServiceChoices
from .membership import filter_errors
from .models import (
    MonitoredService,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    Requisition,
    object_ip_pks,
)


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
            "priority",
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
        # A one-shot copy: importing a Saved Filter overwrites the filter params
        # (no live link, R2). Done before the guard so the copied params are checked.
        saved = self.cleaned_data.get("import_from_saved_filter")
        if saved is not None:
            self.cleaned_data["filter_params"] = dict(saved.parameters or {})
        # Reject unknown/empty filters here (the same guard the resolver uses), so a
        # typo can't be saved into a priority-1 catch-all (C1/H1). Read from
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


class MonitoringDetectorForm(NetBoxModelForm):
    """Add/edit a detector on a Requisition (a preset, or a freeform class)."""

    requisition = DynamicModelChoiceField(
        queryset=Requisition.objects.all(), label=_("Requisition")
    )

    class Meta:
        model = MonitoringDetector
        fields = ("requisition", "name", "preset", "rule_class", "parameters", "tags")


class MonitoringPolicyForm(NetBoxModelForm):
    """Add/edit a policy on a Requisition (a preset, or a freeform class)."""

    requisition = DynamicModelChoiceField(
        queryset=Requisition.objects.all(), label=_("Requisition")
    )

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
    additional_ips = DynamicModelMultipleChoiceField(
        queryset=IPAddress.objects.all(),
        required=False,
        label=_("Additional IPs"),
        query_params={
            "device_id": "$device",
            "virtual_machine_id": "$virtual_machine",
        },
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
            "additional_ips",
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

        # Additional IPs are extra interfaces of THIS object (AD-15); the
        # management IP may legitimately be off-interface, so it is not checked.
        additional = self.cleaned_data.get("additional_ips")
        if additional:
            owned = object_ip_pks(target)
            foreign = [ip for ip in additional if ip.pk not in owned]
            if foreign:
                raise ValidationError(
                    {
                        "additional_ips": _(
                            "These IPs are not assigned to the selected object: "
                            "%(ips)s"
                        )
                        % {"ips": ", ".join(str(ip) for ip in foreign)}
                    }
                )
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
