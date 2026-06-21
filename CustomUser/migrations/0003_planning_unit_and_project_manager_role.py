from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CustomUser', '0002_orgunit_and_user_fields'),
    ]

    operations = [
        # افزودن نقشِ جدید 'project_manager' (تغییر در choices؛ اسکیمای ستون عوض نمی‌شود)
        migrations.AlterField(
            model_name='customuser',
            name='org_role',
            field=models.CharField(
                choices=[
                    ('company_admin', 'مدیر سیستم'),
                    ('company_pm', 'مدیر پروژه شرکت'),
                    ('unit_manager', 'مدیر واحد'),
                    ('project_manager', 'مدیر پروژه'),
                    ('member', 'عضو'),
                ],
                default='member',
                max_length=20,
                verbose_name='نقش سازمانی',
            ),
        ),
        # افزودن فیلدِ is_planning_unit به OrgUnit
        migrations.AddField(
            model_name='orgunit',
            name='is_planning_unit',
            field=models.BooleanField(
                default=False,
                help_text='فقط یک واحد می\u200cتواند به\u200cعنوان واحدِ برنامه\u200cریزی علامت\u200cگذاری شود.',
                verbose_name='واحدِ برنامه\u200cریزی؟',
            ),
        ),
        # محدودیتِ یکتایی روی ردیف‌هایی که is_planning_unit=True هستند
        migrations.AddConstraint(
            model_name='orgunit',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_planning_unit', True)),
                fields=('is_planning_unit',),
                name='only_one_planning_unit',
            ),
        ),
    ]
