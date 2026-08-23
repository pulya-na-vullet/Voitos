# Generated manually: app 0.2.29 — panel WR archive/feedback UX + client archive.

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/panel-wr-archive-feedback-e31c/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 33
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 33
        )
        cfg.mobile_latest_version_name = "0.2.29-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0072_app_version_0_2_28"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Черновик"),
                    ("pending", "Новая"),
                    ("offering", "Ищем исполнителя"),
                    ("scheduling", "Согласование времени"),
                    ("in_progress", "В работе"),
                    ("awaiting_client", "Ждём подтверждения клиента"),
                    (
                        "awaiting_commission",
                        "Ждём комиссию от мастера в 10%",
                    ),
                    ("done", "Выполнена"),
                    ("cancelled", "Отменена"),
                ],
                db_index=True,
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=33, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.29-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=33,
                help_text="Клиенты со меньшим versionCode увидят требование обновить приложение.",
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
