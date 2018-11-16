# Generated by Django 2.1.3 on 2018-11-16 10:21

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('passbook_core', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='SAMLApplication',
            fields=[
                ('application_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='passbook_core.Application')),
                ('acs_url', models.URLField()),
                ('processor_path', models.CharField(max_length=255)),
                ('skip_authorization', models.BooleanField(default=False)),
            ],
            options={
                'abstract': False,
            },
            bases=('passbook_core.application',),
        ),
    ]
