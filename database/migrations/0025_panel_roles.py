# Generated manually for panel admin/manager roles

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("database", "0024_trial_grace_14_days"),
    ]

    operations = [
        migrations.CreateModel(
            name="PanelProfile",
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
                    "role",
                    models.CharField(
                        choices=[("admin", "Администратор"), ("manager", "Менеджер")],
                        db_index=True,
                        default="admin",
                        max_length=16,
                        verbose_name="Роль",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "bot_user",
                    models.OneToOneField(
                        blank=True,
                        help_text="Для менеджера — житель, из которого назначена роль.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="panel_account",
                        to="database.botuser",
                        verbose_name="Пользователь бота",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="panel_profile",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Учётная запись",
                    ),
                ),
            ],
            options={
                "verbose_name": "Профиль панели",
                "verbose_name_plural": "Профили панели",
            },
        ),
        migrations.AddField(
            model_name="servicegroup",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                help_text="Роль менеджера: один на группу, может вести несколько групп.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="managed_service_groups",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Менеджер группы",
            ),
        ),
    ]
