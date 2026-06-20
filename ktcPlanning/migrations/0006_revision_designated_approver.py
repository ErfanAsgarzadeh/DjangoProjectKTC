import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0005_project_owner_unit'),
        ('CustomUser', '0002_orgunit_and_user_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='revision',
            name='designated_approver',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='revisions_to_approve',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
