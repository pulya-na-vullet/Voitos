# Generated manually: client books master + free slots calendar + 0.2.26.

from django.db import migrations, models


DEFAULT_APK_URL = (
    "https://github.com/pulya-na-vullet/Voitos/raw/"
    "cursor/master-booking-calendar-e31c/dist/apk/voitos-debug.apk"
)


def configure_booking_flags(apps, schema_editor):
    import re

    ExecutorRole = apps.get_model("database", "ExecutorRole")
    codes = {"tractor", "truck", "computer_master"}
    name_re = re.compile(
        r"(тракторист|трактор[\s\-]?погруз|компьютерн|"
        r"водител\w*\s+грузов|камаз|грузов\w*\s+маш)",
        re.IGNORECASE,
    )
    for role in ExecutorRole.objects.all():
        code = (role.code or "").strip().lower()
        name = (role.name or "").strip().replace("ё", "е")
        dispatch_only = code in codes or bool(name_re.search(name))
        want = not dispatch_only
        if bool(role.client_books_master) != want:
            role.client_books_master = want
            role.save(update_fields=["client_books_master"])


def bump_app_settings_rows(apps, schema_editor):
    AppSettings = apps.get_model("database", "AppSettings")
    for cfg in AppSettings.objects.all():
        changed = False
        if int(getattr(cfg, "mobile_min_version_code", 0) or 0) < 30:
            cfg.mobile_min_version_code = 30
            changed = True
        if int(getattr(cfg, "mobile_latest_version_code", 0) or 0) < 30:
            cfg.mobile_latest_version_code = 30
            changed = True
        if (getattr(cfg, "mobile_latest_version_name", None) or "").strip() != "0.2.26-kmp":
            cfg.mobile_latest_version_name = "0.2.26-kmp"
            changed = True
        cfg.mobile_apk_url = DEFAULT_APK_URL
        changed = True
        if changed:
            cfg.save()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0068_app_version_0_2_25"),
    ]

    operations = [
        migrations.AddField(
            model_name="executorrole",
            name="client_books_master",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "После выбора роли житель выбирает мастера и свободное время. "
                    "Выключите для тракториста, компьютерного мастера и водителя грузовика "
                    "(там остаётся автоподбор)."
                ),
                verbose_name="Клиент записывается к мастеру сам",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="client_prebooked",
            field=models.BooleanField(
                default=False,
                help_text="Предварительная запись: мастер должен подтвердить и взять в работу.",
                verbose_name="Клиент сам выбрал мастера и время",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_code",
            field=models.PositiveIntegerField(
                default=30, verbose_name="Актуальный versionCode"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_latest_version_name",
            field=models.CharField(
                blank=True,
                default="0.2.26-kmp",
                max_length=64,
                verbose_name="Актуальная versionName",
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="mobile_min_version_code",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Клиенты со меньшим versionCode увидят требование обновить приложение.",
                verbose_name="Мин. versionCode приложения",
            ),
        ),
        migrations.RunPython(configure_booking_flags, migrations.RunPython.noop),
        migrations.RunPython(bump_app_settings_rows, migrations.RunPython.noop),
    ]
