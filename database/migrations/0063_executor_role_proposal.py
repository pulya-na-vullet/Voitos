# Generated manually: ExecutorRoleProposal + ROLE_PROPOSAL task + version 0.2.20.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://github.com/pulya-na-vullet/Voitos/raw/"
    "cursor/app-registration-max-e31c/dist/apk/voitos-debug.apk"
)


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        changed = False
        if int(getattr(cfg, "mobile_min_version_code", 0) or 0) < 24:
            cfg.mobile_min_version_code = 24
            changed = True
        if int(getattr(cfg, "mobile_latest_version_code", 0) or 0) < 24:
            cfg.mobile_latest_version_code = 24
            changed = True
        if (getattr(cfg, "mobile_latest_version_name", None) or "").strip() != "0.2.20-kmp":
            cfg.mobile_latest_version_name = "0.2.20-kmp"
            changed = True
        if not (getattr(cfg, "mobile_apk_url", None) or "").strip():
            cfg.mobile_apk_url = DEFAULT_APK_URL
            changed = True
        if changed:
            cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("database", "0062_app_version_0_2_19"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExecutorRoleProposal",
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
                (
                    "proposed_name",
                    models.CharField(max_length=128, verbose_name="Предложенная роль"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "На рассмотрении"),
                            ("approved", "Добавлена"),
                            ("rejected", "Отклонена"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                (
                    "admin_note",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="Комментарий администратора",
                    ),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_role",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="from_proposals",
                        to="database.executorrole",
                        verbose_name="Созданная роль",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_role_proposals",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Рассмотрел",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="role_proposals",
                        to="database.botuser",
                        verbose_name="Житель",
                    ),
                ),
            ],
            options={
                "verbose_name": "Заявка на роль исполнителя",
                "verbose_name_plural": "Заявки на роли исполнителей",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "-created_at"],
                        name="database_ex_status_7c8a0a_idx",
                    ),
                    models.Index(
                        fields=["user", "-created_at"],
                        name="database_ex_user_id_9f2b1c_idx",
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=24, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.20-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=24,
                help_text="Клиенты со меньшим versionCode увидят требование обновить приложение.",
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
