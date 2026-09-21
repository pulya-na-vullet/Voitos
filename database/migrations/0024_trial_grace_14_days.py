from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def bump_grace_to_14(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    BotUser = apps.get_model("database", "BotUser")
    now = timezone.now()
    for cfg in AppSettings.objects.all():
        if int(getattr(cfg, "grace_days", 0) or 0) == 2:
            cfg.grace_days = 14
            cfg.save(update_fields=["grace_days"])
    # Продлить активный пробный период у пользователей без подписки.
    for user in BotUser.objects.filter(
        subscription_until__isnull=True,
        grace_until__gt=now,
        first_seen_at__isnull=False,
    ).iterator():
        new_end = user.first_seen_at + timedelta(days=14)
        if new_end > user.grace_until:
            user.grace_until = new_end
            user.save(update_fields=["grace_until"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0023_resident_helpers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsettings",
            name="grace_days",
            field=models.PositiveIntegerField(
                default=14,
                help_text=(
                    "Для новых пользователей — пробный доступ с первого входа; "
                    "после истечения подписки — столько же дней на оплату."
                ),
                verbose_name="Пробный / льготный период, дней",
            ),
        ),
        migrations.RunPython(bump_grace_to_14, noop_reverse),
    ]
