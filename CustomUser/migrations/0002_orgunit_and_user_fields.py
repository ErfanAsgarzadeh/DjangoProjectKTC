import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('CustomUser', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrgUnit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='نام واحد')),
                ('description', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='managed_units', to='CustomUser.customuser', verbose_name='مدیر واحد')),
            ],
        ),
        migrations.AddField(
            model_name='customuser',
            name='org_role',
            field=models.CharField(choices=[('company_admin', 'مدیر سیستم'), ('company_pm', 'مدیر پروژه شرکت'), ('unit_manager', 'مدیر واحد'), ('member', 'عضو')], default='member', max_length=20, verbose_name='نقش سازمانی'),
        ),
        migrations.AddField(
            model_name='customuser',
            name='unit',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='members', to='CustomUser.orgunit', verbose_name='واحد سازمانی'),
        ),
    ]
