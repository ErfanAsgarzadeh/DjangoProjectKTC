from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CustomUser', '0003_planning_unit_and_project_manager_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='allowed_pages',
            field=models.JSONField(
                blank=True, null=True, default=None,
                help_text='null یعنی دسترسی به همهٔ صفحات. در غیر این صورت لیستِ مسیرهای مجاز.',
                verbose_name='صفحاتِ مجاز',
            ),
        ),
    ]
