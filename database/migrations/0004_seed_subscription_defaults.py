from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    BotUser = apps.get_model("database", "BotUser")
    cfg, _ = AppSettings.objects.get_or_create(pk=1)
    if not cfg.payment_phone:
        cfg.payment_phone = "89625507832"
    if not cfg.payment_name:
        cfg.payment_name = "Григорьев Дмитрий Вячеславович"
    if not cfg.subscription_price_rub:
        cfg.subscription_price_rub = 100
    if not cfg.grace_days:
        cfg.grace_days = 2
    cfg.allowed_max_user_id = ""
    cfg.save()
    until = timezone.now() + timedelta(days=30)
    BotUser.objects.filter(subscription_until__isnull=True).update(
        subscription_until=until, grace_until=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0003_alter_botuser_options_appsettings_grace_days_and_more"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
