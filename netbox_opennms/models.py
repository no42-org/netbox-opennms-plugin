# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Plugin data models."""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models
from django.urls import reverse
from netbox.models import NetBoxModel

# A Monitoring Profile may be assigned to a Device or a VirtualMachine.
ASSIGNMENT_MODELS = models.Q(
    models.Q(app_label="dcim", model="device")
    | models.Q(app_label="virtualization", model="virtualmachine")
)


class MonitoringProfile(NetBoxModel):
    """Monitoring intent for a single Device or VirtualMachine.

    Attaches to its target via a GenericForeignKey (AD-7). At most one profile
    may exist per object (enforced by a unique constraint, which also supplies
    the GFK index). ``enabled`` toggles whether the object is rendered into an
    OpenNMS requisition; disabling retains the profile (removal is handled by a
    later story via render-and-replace).
    """

    # The GFK's backing ForeignKey must point to Django's native ContentType,
    # never NetBox's ObjectType proxy.
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
    enabled = models.BooleanField(default=True)
    # The primary interface OpenNMS polls (snmp-primary="P" at render time).
    # Nullable so a missing IP is caught by validation, not a DB error, and so
    # deleting the referenced IP clears it (SET_NULL) rather than being blocked.
    management_ip = models.ForeignKey(
        to="ipam.IPAddress",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # The object's other monitored IPs — rendered as non-primary (snmp-primary="N")
    # interfaces (AD-15). The management IP is NOT stored here; the renderer is the
    # single interface-set authority and excludes it even if mistakenly added.
    additional_ips = models.ManyToManyField(
        to="ipam.IPAddress",
        related_name="+",
        blank=True,
    )

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(
                fields=("assigned_object_type", "assigned_object_id"),
                name="%(app_label)s_%(class)s_unique_object",
            ),
        ]
        verbose_name = "monitoring profile"
        verbose_name_plural = "monitoring profiles"

    def __str__(self):
        if self.assigned_object is not None:
            return f"{self.assigned_object}"
        return "Monitoring profile"

    def get_absolute_url(self):
        return reverse("plugins:netbox_opennms:monitoringprofile", args=[self.pk])
