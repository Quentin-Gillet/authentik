# Generated by Django 5.0.9 on 2024-10-02 11:35

import django.db.models.deletion
from django.db import migrations, models

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def migrate_invalidation_flow_default(apps: Apps, schema_editor: BaseDatabaseSchemaEditor):
    from authentik.flows.models import FlowDesignation, FlowAuthenticationRequirement

    db_alias = schema_editor.connection.alias

    Flow = apps.get_model("authentik_flows", "Flow")
    Provider = apps.get_model("authentik_core", "Provider")

    # So this flow is managed via a blueprint, bue we're in a migration so we don't want to rely on that
    # since the blueprint is just an empty flow we can just create it here
    # and let it be managed by the blueprint later
    flow, _ = Flow.objects.using(db_alias).update_or_create(
        slug="default-provider-invalidation-flow",
        defaults={
            "name": "Logged out of application",
            "title": "You've logged out of %(app)s.",
            "authentication": FlowAuthenticationRequirement.NONE,
            "designation": FlowDesignation.INVALIDATION,
        },
    )
    Provider.objects.using(db_alias).filter(invalidation_flow=None).update(invalidation_flow=flow)


class Migration(migrations.Migration):

    dependencies = [
        ("authentik_core", "0039_source_group_matching_mode_alter_group_name_and_more"),
        ("authentik_flows", "0027_auto_20231028_1424"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="invalidation_flow",
            field=models.ForeignKey(
                default=None,
                help_text="Flow used ending the session from a provider.",
                null=True,
                on_delete=django.db.models.deletion.SET_DEFAULT,
                related_name="provider_invalidation",
                to="authentik_flows.flow",
            ),
        ),
        migrations.RunPython(migrate_invalidation_flow_default),
    ]
