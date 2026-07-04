# Copyright 2026 Ronny Trommer <ronny@no42.org>
# SPDX-License-Identifier: MIT
"""Drop Requisition.priority — overlap is now a user-resolved conflict (C1/C6).

No data migration: the redesign's seeded ``netbox.{site}.{role}`` Requisitions are
pairwise disjoint by construction (each pins site+role), so removing the ordering
changes no membership. Any user-created overlap surfaces as a frozen conflict on
first resolution after upgrade — never as a silent membership change.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_opennms", "0003_deployedforeignsource"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="requisition",
            options={
                "ordering": ("name",),
                "verbose_name": "requisition",
                "verbose_name_plural": "requisitions",
            },
        ),
        migrations.RemoveField(
            model_name="requisition",
            name="priority",
        ),
    ]
