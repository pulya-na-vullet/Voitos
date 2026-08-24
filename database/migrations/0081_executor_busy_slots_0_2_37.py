# Executor personal busy slots + app 0.2.37.

import django.db.models.deletion
from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/executor-busy-slots-e31c/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 41
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 41
        )
        cfg.mobile_latest_version_name = "0.2.37-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0080_app_version_0_2_36"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExecutorBusySlot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("start_at", models.DateTimeField(verbose_name="Начало")),
                ("end_at", models.DateTimeField(verbose_name="Конец")),
                (
                    "note",
                    models.CharField(
                        blank=True,
                        default="Внешняя работа",
                        max_length=255,
                        verbose_name="Комментарий",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="busy_slots",
                        to="database.botuser",
                        verbose_name="Исполнитель",
                    ),
                ),
            ],
            options={
                "verbose_name": "Занятый слот исполнителя",
                "verbose_name_plural": "Занятые слоты исполнителей",
                "ordering": ["start_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["user", "start_at", "end_at"],
                        name="database_ex_user_id_0ed261_idx",
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=41, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.37-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=41,
                help_text=(
                    "Клиенты со меньшим versionCode увидят требование "
                    "обновить приложение."
                ),
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
