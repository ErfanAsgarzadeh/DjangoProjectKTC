"""
Migration: اضافه کردن validators به فیلد file در TaskChatMessage

این migration هیچ تغییری در ساختار دیتابیس ایجاد نمی‌کند (AlterField روی validators
فقط منطق Python را تغییر می‌دهد). اطلاعات موجود دست‌نخورده باقی می‌مانند.
"""

import ktcPlanning.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0008_projectviewer'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskchatmessage',
            name='file',
            field=models.FileField(
                blank=True,
                null=True,
                upload_to='chat_attachments/%Y/%m/',
                validators=[ktcPlanning.validators.validate_chat_file],
            ),
        ),
    ]
