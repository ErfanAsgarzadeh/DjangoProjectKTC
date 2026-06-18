from django.db import migrations


def backfill_sequence(apps, schema_editor):
    """
    تسک‌های موجود که sequence آنها صفر است را بر اساس ترتیب ساخت (id)
    در هر گروه (revision, wbs_node) شماره‌گذاری می‌کند تا ترتیب پیش‌فرض
    نمایش، ترتیب ایجاد تسک‌ها باشد.
    """
    TaskVersion = apps.get_model('ktcPlanning', 'TaskVersion')

    # گروه‌بندی بر اساس (revision, wbs_node)
    groups = {}
    for tv in TaskVersion.objects.all().order_by('id'):
        key = (tv.revision_id, tv.wbs_node_id)
        groups.setdefault(key, []).append(tv)

    for key, versions in groups.items():
        # فقط وقتی همه صفر هستند بازنشانی کن تا ترتیب دستی قبلی خراب نشود
        if all((v.sequence or 0) == 0 for v in versions):
            for idx, v in enumerate(versions, start=1):
                v.sequence = idx
                v.save(update_fields=['sequence'])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ktcPlanning', '0002_remove_wbsnode_level_remove_wbsnode_lft_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_sequence, reverse_noop),
    ]
