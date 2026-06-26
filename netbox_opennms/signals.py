# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: Apache-2.0
"""Signal handlers.

A GenericForeignKey has no database-level cascade, so deleting a monitored
Device or VirtualMachine would otherwise leave an orphaned MonitoringProfile
pointing at a non-existent object. These handlers clean those up.
"""

from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete
from django.dispatch import receiver
from virtualization.models import VirtualMachine

from .models import MonitoringProfile


@receiver(post_delete, sender=Device)
@receiver(post_delete, sender=VirtualMachine)
def delete_orphaned_monitoring_profiles(sender, instance, **kwargs):
    content_type = ContentType.objects.get_for_model(sender)
    MonitoringProfile.objects.filter(
        assigned_object_type=content_type,
        assigned_object_id=instance.pk,
    ).delete()
