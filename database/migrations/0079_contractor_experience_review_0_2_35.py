# Contractor experience + vehicle docs + review detail (0.2.35).

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
    "cursor/voitos-team-priority-dispatch-e31c/dist/apk/voitos-debug.apk"
)

VEHICLE_CODES = {"tractor", "truck"}


def harden_vehicle_roles_and_task_urls(apps, schema_editor):
    ExecutorRole = apps.get_model("database", "ExecutorRole")
    AdminTask = apps.get_model("database", "AdminTask")
    for role in ExecutorRole.objects.filter(code__in=VEHICLE_CODES):
        changed = []
        if not role.is_equipment:
            role.is_equipment = True
            changed.append("is_equipment")
        if not role.requires_qualification_docs:
            role.requires_qualification_docs = True
            changed.append("requires_qualification_docs")
        if changed:
            role.save(update_fields=changed + ["updated_at"] if hasattr(role, "updated_at") else changed)
    for task in AdminTask.objects.filter(
        kind="contractor_review",
        source_model="ContractorProfile",
        status="open",
    ):
        if task.source_id:
            want = f"/panel/contractors/{task.source_id}/"
            if task.action_url != want:
                task.action_url = want
                task.save(update_fields=["action_url"])


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        cfg.mobile_min_version_code = max(
            int(getattr(cfg, "mobile_min_version_code", 0) or 0), 39
        )
        cfg.mobile_latest_version_code = max(
            int(getattr(cfg, "mobile_latest_version_code", 0) or 0), 39
        )
        cfg.mobile_latest_version_name = "0.2.35-kmp"
        cfg.mobile_apk_url = DEFAULT_APK_URL
        cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0078_voitos_team_priority_0_2_34"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractorprofile",
            name="experience_text",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Например: 5 лет. Для тракториста и водителя — стаж вождения техники."
                ),
                max_length=255,
                verbose_name="Стаж / опыт",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=39, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.35-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=39,
                help_text=(
                    "Клиенты со меньшим versionCode увидят требование "
                    "обновить приложение."
                ),
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(
            harden_vehicle_roles_and_task_urls, migrations.RunPython.noop
        ),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
