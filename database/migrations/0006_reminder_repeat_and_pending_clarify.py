from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0005_revoke_unpaid_free_subscriptions"),
    ]

    operations = [
        migrations.AddField(
            model_name="reminder",
            name="repeat",
            field=models.CharField(
                choices=[("none", "Один раз"), ("daily", "Каждый день")],
                default="none",
                max_length=16,
                verbose_name="Повтор",
            ),
        ),
        migrations.AddField(
            model_name="pendingaction",
            name="pending_kind",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pendingaction",
            name="pending_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
