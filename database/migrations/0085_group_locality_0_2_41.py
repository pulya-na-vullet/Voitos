# Group locality field + app 0.2.41.

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/test-app-backend-c5ef/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 45
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 45
        )
        cfg.mobile_latest_version_name = "0.2.41-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


def backfill_group_localities(apps, schema_editor):
    ServiceGroup = apps.get_model("database", "ServiceGroup")
    from services.locality import canonicalize_locality, settlement_for_group

    for g in ServiceGroup.objects.all().prefetch_related("members"):
        raw = (getattr(g, "locality", None) or "").strip()
        if raw:
            canon = canonicalize_locality(raw, use_ai=False)[:255]
            if canon != g.locality:
                g.locality = canon
                g.save(update_fields=["locality"])
            continue
        inferred = settlement_for_group(g)
        if inferred:
            g.locality = inferred[:255]
            g.save(update_fields=["locality"])


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0084_app_version_0_2_40"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicegroup",
            name="locality",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Куюки, Чебоксары… По нему жители в приложении видят мастеров."
                ),
                max_length=255,
                verbose_name="Населённый пункт",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=45, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.41-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=45,
                help_text=(
                    "Клиенты со меньшим versionCode увидят требование "
                    "обновить приложение."
                ),
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
        migrations.RunPython(backfill_group_localities, migrations.RunPython.noop),
    ]
