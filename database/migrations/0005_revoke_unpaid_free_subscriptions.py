from django.db import migrations


def revoke_unpaid(apps, schema_editor):
    """
    Migration 0004 previously gifted ~30 days of free access to every existing user.
    Revoke that for anyone without an approved payment receipt.
    """
    # Import after apps are ready so we use the real helper + current models.
    from subscriptions.service import revoke_unpaid_subscriptions

    revoke_unpaid_subscriptions()


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0004_seed_subscription_defaults"),
    ]
    operations = [migrations.RunPython(revoke_unpaid, migrations.RunPython.noop)]
