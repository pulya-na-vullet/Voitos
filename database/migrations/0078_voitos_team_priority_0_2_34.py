# Voitos team priority dispatch + campaign peers in app (0.2.34).

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/voitos-team-priority-dispatch-e31c/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 38
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 38
        )
        cfg.mobile_latest_version_name = "0.2.34-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0077_app_version_0_2_33"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractorprofile",
            name="is_voitos_team",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text=(
                    "Высокий приоритет на заявках для ролей тракторист / водитель "
                    "грузовика / компьютерный мастер: сначала им, и только если "
                    "слоты на сегодня заняты — остальным."
                ),
                verbose_name="Член команды Voitos",
            ),
        ),
        migrations.AddField(
            model_name="servicecampaign",
            name="show_executor_peers_in_app",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Если на задаче несколько исполнителей — их контакты видны "
                    "коллегам в мобильном приложении."
                ),
                verbose_name="Показывать исполнителей друг другу в приложении",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=38, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.34-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=38,
                help_text=(
                    "Клиенты со меньшим versionCode увидят требование "
                    "обновить приложение."
                ),
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
