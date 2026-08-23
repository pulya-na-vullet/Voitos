# Generated manually: WR schedule/contacts flow + version 0.2.22.

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://github.com/pulya-na-vullet/Voitos/raw/"
    "cursor/wr-schedule-contacts-e31c/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        changed = False
        if int(getattr(cfg, "mobile_min_version_code", 0) or 0) < 26:
            cfg.mobile_min_version_code = 26
            changed = True
        if int(getattr(cfg, "mobile_latest_version_code", 0) or 0) < 26:
            cfg.mobile_latest_version_code = 26
            changed = True
        if (getattr(cfg, "mobile_latest_version_name", None) or "").strip() != "0.2.22-kmp":
            cfg.mobile_latest_version_name = "0.2.22-kmp"
            changed = True
        if not (getattr(cfg, "mobile_apk_url", None) or "").strip():
            cfg.mobile_apk_url = DEFAULT_APK_URL
            changed = True
        if changed:
            cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0064_app_version_0_2_21"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=26, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.22-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=26,
                help_text="Клиенты со меньшим versionCode увидят требование обновить приложение.",
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
