# Canonical locality grouping + app 0.2.39.

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/test-app-backend-c5ef/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 43
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 43
        )
        cfg.mobile_latest_version_name = "0.2.39-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


def backfill_canonical_localities(apps, schema_editor):
    from services.locality import canonicalize_locality

    ContractorProfile = apps.get_model("database", "ContractorProfile")
    BotUser = apps.get_model("database", "BotUser")
    for p in ContractorProfile.objects.exclude(locality="").iterator():
        canon = canonicalize_locality(p.locality, use_ai=False)
        if canon and canon != p.locality:
            p.locality = canon[:255]
            p.save(update_fields=["locality"])
    for u in BotUser.objects.exclude(locality="").iterator():
        extra = getattr(u, "address", "") or ""
        canon = canonicalize_locality(u.locality, extra=extra, use_ai=False)
        if canon and canon != u.locality:
            u.locality = canon[:255]
            u.save(update_fields=["locality"])


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0082_app_version_0_2_38"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=43, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.39-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=43,
                help_text=(
                    "Клиенты со меньшим versionCode увидят требование "
                    "обновить приложение."
                ),
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
        migrations.RunPython(backfill_canonical_localities, migrations.RunPython.noop),
    ]
