# MAX outbox queue + app 0.2.38.

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/test-app-backend-c5ef/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 42
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 42
        )
        cfg.mobile_latest_version_name = "0.2.38-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0081_executor_busy_slots_0_2_37"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=42, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.38-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=42,
                help_text=(
                    "Клиенты со меньшим versionCode увидят требование "
                    "обновить приложение."
                ),
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
