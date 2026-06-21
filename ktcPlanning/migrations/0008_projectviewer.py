import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('ktcPlanning', '0007_project_scope_systemsettings_reportlog_approval'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectViewer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('added_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='added_project_viewers',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('project', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='viewers',
                    to='ktcPlanning.project',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='viewable_projects',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'unique_together': {('project', 'user')},
            },
        ),
    ]
