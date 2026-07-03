# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
# Interface roles (RD-5): additional interfaces become MonitoredInterface child
# rows carrying a per-interface SNMP role, and the management interface gains a
# management_role. Existing additional_ips links seed as role='N' (Not-eligible)
# BEFORE the M2M is dropped, so every node renders byte-identically after upgrade
# (management -> 'P', additional -> 'N', exactly as the prior renderer emitted).

import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


def copy_additional_ips(apps, schema_editor):
    """Seed a MonitoredInterface (role=Not-eligible) for each existing additional IP."""
    MonitoringOverride = apps.get_model("netbox_opennms", "MonitoringOverride")
    MonitoredInterface = apps.get_model("netbox_opennms", "MonitoredInterface")
    for override in MonitoringOverride.objects.all():
        for ip in override.additional_ips.all():
            MonitoredInterface.objects.create(
                override_id=override.pk, ip_address_id=ip.pk, role="N"
            )


def restore_additional_ips(apps, schema_editor):
    """Reverse: copy MonitoredInterface rows back onto the additional_ips M2M."""
    MonitoringOverride = apps.get_model("netbox_opennms", "MonitoringOverride")
    MonitoredInterface = apps.get_model("netbox_opennms", "MonitoredInterface")
    for interface in MonitoredInterface.objects.all():
        override = MonitoringOverride.objects.get(pk=interface.override_id)
        override.additional_ips.add(interface.ip_address_id)


class Migration(migrations.Migration):

    dependencies = [
        ('extras', '0140_imageattachment_image_size'),
        ('ipam', '0092_iprange_host_indexes'),
        ('netbox_opennms', '0004_remove_requisition_priority'),
    ]

    operations = [
        migrations.AddField(
            model_name='monitoringoverride',
            name='management_role',
            field=models.CharField(default='P', max_length=1),
        ),
        migrations.CreateModel(
            name='MonitoredInterface',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
                ('role', models.CharField(default='N', max_length=1)),
                ('ip_address', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='ipam.ipaddress')),
                ('override', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='interfaces', to='netbox_opennms.monitoringoverride')),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
            ],
            options={
                'verbose_name': 'monitored interface',
                'verbose_name_plural': 'monitored interfaces',
                'ordering': ('override', 'ip_address'),
                'constraints': [models.UniqueConstraint(fields=('override', 'ip_address'), name='netbox_opennms_monitoredinterface_unique_ip')],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.RunPython(copy_additional_ips, restore_additional_ips),
        migrations.RemoveField(
            model_name='monitoringoverride',
            name='additional_ips',
        ),
    ]
