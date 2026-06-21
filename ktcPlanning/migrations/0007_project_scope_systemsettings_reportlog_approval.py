"""
PR3: Approval governance schema changes.
- Project.scope (CharField, default 'intra_unit')
- SystemSettings singleton model
- TaskReportLog: approval_status, reviewer_approved_by/at, final_approved_by/at
- Data migration: existing is_approved=True reports → approval_status='final_approved'
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_approval_status(apps, schema_editor):
    """Migrate existing reports: is_approved=True → final_approved, else pending."""
    TaskReportLog = apps.get_model('ktcPlanning', 'TaskReportLog')
    TaskReportLog.objects.filter(is_approved=True).update(approval_status='final_approved')
    TaskReportLog.objects.filter(is_approved=False).update(approval_status='pending')


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('ktcPlanning', '0006_revision_designated_approver'),
    ]

    operations = [
        # ─── Project.scope ───────────────────────────────────────────────
        migrations.AddField(
            model_name='project',
            name='scope',
            field=models.CharField(
                choices=[('intra_unit', 'پروژهٔ درون\u200cواحدی'), ('company', 'پروژهٔ شرکتی')],
                default='intra_unit',
                help_text='پروژه\u200cهای شرکتی نیازمندِ تاییدِ نهاییِ گزارش توسطِ مدیرِ برنامه\u200cریزی هستند.',
                max_length=16,
                verbose_name='دامنهٔ پروژه',
            ),
        ),

        # ─── SystemSettings singleton ────────────────────────────────────
        migrations.CreateModel(
            name='SystemSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('allow_planning_manager_bypass_reviewer', models.BooleanField(
                    default=False,
                    help_text='اگر فعال باشد، مدیرِ برنامه\u200cریزی می\u200cتواند گزارش\u200cهای پروژهٔ شرکتی را بدون تاییدِ بررسی\u200cکننده مستقیماً تایید نهایی کند.',
                    verbose_name='اجازهٔ bypass تایید بررسی\u200cکننده توسط مدیر برنامه\u200cریزی',
                )),
            ],
            options={
                'verbose_name': 'تنظیمات سیستم',
                'verbose_name_plural': 'تنظیمات سیستم',
            },
        ),

        # ─── TaskReportLog: approval state machine fields ────────────────
        migrations.AddField(
            model_name='taskreportlog',
            name='approval_status',
            field=models.CharField(
                choices=[
                    ('pending', 'در انتظار تایید'),
                    ('reviewer_approved', 'تاییدشده توسط بررسی\u200cکننده'),
                    ('final_approved', 'تایید نهایی'),
                    ('rejected', 'رد شده'),
                ],
                default='pending',
                max_length=24,
                verbose_name='وضعیت تایید',
            ),
        ),
        migrations.AddField(
            model_name='taskreportlog',
            name='reviewer_approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reviewer_approved_reports',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='taskreportlog',
            name='reviewer_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='taskreportlog',
            name='final_approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='final_approved_reports',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='taskreportlog',
            name='final_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ─── Data migration: backfill approval_status from legacy is_approved ──
        migrations.RunPython(backfill_approval_status, migrations.RunPython.noop),
    ]
