from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("database", "0040_service_payee_bank"),
    ]

    operations = [
        migrations.CreateModel(
            name="FeedbackTicket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("bug", "Баг в приложении"),
                            ("feedback", "Обратная связь"),
                            ("manager", "ОС по менеджеру"),
                        ],
                        db_index=True,
                        default="feedback",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "На рассмотрении"),
                            ("answered", "Рассмотрено"),
                            ("closed", "Закрыто"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=16,
                    ),
                ),
                ("subject", models.CharField(blank=True, default="", max_length=200, verbose_name="Тема")),
                ("body", models.TextField(verbose_name="Текст обращения")),
                ("score", models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Оценка менеджера (1–5)")),
                ("admin_reply", models.TextField(blank=True, default="", verbose_name="Ответ администратора")),
                ("admin_replied_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "admin_replied_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="answered_feedback_tickets",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Ответил",
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="feedback_tickets",
                        to="database.servicegroup",
                        verbose_name="Группа / район",
                    ),
                ),
                (
                    "manager",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="manager_feedback_tickets",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Менеджер",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="feedback_tickets",
                        to="database.botuser",
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Обращение (ОС)",
                "verbose_name_plural": "Обращения (ОС)",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="feedbackticket",
            index=models.Index(fields=["status", "-created_at"], name="database_fe_status_9a1b2c_idx"),
        ),
        migrations.AddIndex(
            model_name="feedbackticket",
            index=models.Index(fields=["kind", "status"], name="database_fe_kind_9a1b2c_idx"),
        ),
        migrations.AddIndex(
            model_name="feedbackticket",
            index=models.Index(fields=["user", "-created_at"], name="database_fe_user_9a1b2c_idx"),
        ),
    ]
