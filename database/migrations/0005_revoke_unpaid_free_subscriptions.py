from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def revoke_unpaid(apps, schema_editor):
    """
    Migration 0004 previously gifted ~30 days of free access to every existing user.
    Revoke that for anyone without an approved payment receipt.
    """
    BotUser = apps.get_model("database", "BotUser")
    PaymentReceipt = apps.get_model("database", "PaymentReceipt")
    AppSettings = apps.get_model("database", "AppSettings")

    cfg = AppSettings.objects.filter(pk=1).first()
    grace_days = (cfg.grace_days if cfg and getattr(cfg, "grace_days", None) else 2) or 2
    now = timezone.now()
    grace_until = now + timedelta(days=grace_days)
    paid_ids = set(
        PaymentReceipt.objects.filter(status="approved").values_list("user_id", flat=True)
    )
    qs = BotUser.objects.exclude(id__in=paid_ids).filter(subscription_until__gt=now)
    for user in qs.iterator():
        user.subscription_until = None
        user.grace_until = grace_until
        user.last_payment_notice_at = None
        user.save(
            update_fields=["subscription_until", "grace_until", "last_payment_notice_at"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0004_seed_subscription_defaults"),
    ]
    operations = [migrations.RunPython(revoke_unpaid, migrations.RunPython.noop)]
