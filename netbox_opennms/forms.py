# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Forms for plugin models (Epic 5)."""

from dcim.models import Device, DeviceRole, Site
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from ipam.models import IPAddress
from netbox.forms import NetBoxModelForm
from utilities.forms.fields import (
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
)
from virtualization.models import VirtualMachine

from .models import (
    MonitoredService,
    MonitoringAssignment,
    MonitoringDetector,
    MonitoringOverride,
    MonitoringPolicy,
    MonitoringProfile,
)


class MonitoringProfileForm(NetBoxModelForm):
    """Create/edit a Monitoring Profile (a reusable detector/policy template)."""

    class Meta:
        model = MonitoringProfile
        fields = (
            "name",
            "description",
            "scan_interval",
            "default_interfaces",
            "tags",
        )


class MonitoringDetectorForm(NetBoxModelForm):
    """Add/edit a detector on a profile (a preset, or a freeform class)."""

    profile = DynamicModelChoiceField(
        queryset=MonitoringProfile.objects.all(), label=_("Monitoring Profile")
    )

    class Meta:
        model = MonitoringDetector
        fields = ("profile", "name", "preset", "rule_class", "parameters", "tags")


class MonitoringPolicyForm(NetBoxModelForm):
    """Add/edit a policy on a profile (a preset, or a freeform class)."""

    profile = DynamicModelChoiceField(
        queryset=MonitoringProfile.objects.all(), label=_("Monitoring Profile")
    )

    class Meta:
        model = MonitoringPolicy
        fields = ("profile", "name", "preset", "rule_class", "parameters", "tags")


class MonitoringAssignmentForm(NetBoxModelForm):
    """Bind a profile to a (site[, role]) scope."""

    profile = DynamicModelChoiceField(
        queryset=MonitoringProfile.objects.all(), label=_("Monitoring Profile")
    )
    site = DynamicModelChoiceField(queryset=Site.objects.all(), label=_("Site"))
    role = DynamicModelChoiceField(
        queryset=DeviceRole.objects.all(),
        required=False,
        label=_("Role"),
        help_text=_("Leave blank to apply to every role in the site."),
    )

    class Meta:
        model = MonitoringAssignment
        fields = ("profile", "site", "role", "location", "tags")


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

    class Meta:
        model = MonitoringOverride
        fields = (
            "device",
            "virtual_machine",
            "exclude",
            "management_ip",
            "additional_ips",
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
    """Add/edit an explicit service on one of an override's interfaces."""

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
