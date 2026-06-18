import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0003_backfill_taskversion_sequence'),
    ]

    operations = [
        # تقویم می‌تواند مستقل از پروژه باشد (قالب)
        migrations.AlterField(
            model_name='calendar',
            name='project',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='calendars',
                to='ktcPlanning.project',
            ),
        ),
        # الصاق تقویم به پروژه
        migrations.AddField(
            model_name='project',
            name='calendar',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='projects',
                to='ktcPlanning.calendar',
            ),
        ),
    ]
