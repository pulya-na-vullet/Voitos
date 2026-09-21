from django.db import migrations


def forwards(apps, schema_editor):
    # Use runtime helper so logic stays in one place.
    from subscriptions.service import recalculate_approved_receipt_periods

    recalculate_approved_receipt_periods()


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0011_receipt_days_granted"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
