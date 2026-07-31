from django.db import migrations


def forwards(apps, schema_editor):
    # Historical one-time recalc. Intentionally does not import runtime models:
    # AppSettings.load() would break when later migrations add columns.
    pass


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0011_receipt_days_granted"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
