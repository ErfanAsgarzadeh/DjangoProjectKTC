"""
Ghost migration: registers TaskReportLog and TaskChatMessage in Django's
migration state without touching the database.

Both tables already exist in the live database (created in earlier project
history that was lost when migration files were renumbered/rewritten).
This migration uses SeparateDatabaseAndState with empty database_operations
so Django's state graph knows about these models — required so that 0007
can AddField on TaskReportLog without a KeyError.
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('ktcPlanning', '0006_revision_designated_approver'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],  # tables already exist; do nothing on DB
            state_operations=[
                migrations.CreateModel(
                    name='TaskReportLog',
                    fields=[
                        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ('status', models.CharField(
                            choices=[('on-track', 'On Track'), ('at-risk', 'At Risk'),
                                     ('blocked', 'Blocked'), ('completed', 'Completed')],
                            default='on-track', max_length=20)),
                        ('progress_percent', models.PositiveIntegerField(default=0)),
                        ('time_spent_hours', models.DecimalField(decimal_places=2, default=0.00, max_digits=5)),
                        ('notes', models.TextField(blank=True, verbose_name='Progress Notes')),
                        ('blockers', models.TextField(blank=True, verbose_name='Critical Blockers')),
                        ('timestamp', models.DateTimeField(auto_now_add=True)),
                        ('is_approved', models.BooleanField(default=False, verbose_name='وضعیت تایید')),
                        ('approved_at', models.DateTimeField(blank=True, null=True)),
                        ('approved_by', models.ForeignKey(
                            blank=True, null=True,
                            on_delete=django.db.models.deletion.SET_NULL,
                            related_name='approved_reports', to=settings.AUTH_USER_MODEL)),
                        ('task', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='report_logs', to='ktcPlanning.task')),
                        ('user', models.ForeignKey(
                            on_delete=django.db.models.deletion.PROTECT,
                            related_name='submitted_reports', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={'ordering': ['-timestamp']},
                ),
                migrations.CreateModel(
                    name='TaskChatMessage',
                    fields=[
                        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ('text', models.TextField(blank=True, default='')),
                        ('file', models.FileField(blank=True, null=True, upload_to='chat_attachments/%Y/%m/')),
                        ('file_name', models.CharField(blank=True, default='', max_length=255)),
                        ('file_type', models.CharField(blank=True, default='', max_length=50)),
                        ('timestamp', models.DateTimeField(auto_now_add=True)),
                        ('task', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='chat_messages', to='ktcPlanning.task')),
                        ('user', models.ForeignKey(
                            on_delete=django.db.models.deletion.PROTECT,
                            related_name='chat_messages', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={'ordering': ['timestamp']},
                ),
            ],
        ),
    ]
