# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Ownership record for the drift reconciler (review #4).

Tracks the Foreign Source names NetBox has pushed to OpenNMS, so drift cleanup
can find orphaned user-named requisitions (not just ``netbox.*``) without ever
touching a requisition NetBox did not create.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_opennms", "0002_requisition_redesign"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeployedForeignSource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=100, unique=True)),
            ],
            options={
                "verbose_name": "deployed foreign source",
                "verbose_name_plural": "deployed foreign sources",
                "ordering": ("name",),
            },
        ),
    ]
