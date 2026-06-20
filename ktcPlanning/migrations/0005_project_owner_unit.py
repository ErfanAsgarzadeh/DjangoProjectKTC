import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0004_calendar_templates_and_project_calendar'),
        ('CustomUser', '0002_orgunit_and_user_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='owner_unit',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='owned_projects',
                to='CustomUser.orgunit',
            ),
        ),
    ]
