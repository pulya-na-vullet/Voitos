from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0032_work_request_executor_earned"),
    ]

    operations = [
        migrations.AddField(
            model_name="executorrole",
            name="accepts_at_home",
            field=models.BooleanField(
                default=False,
                help_text="После принятия заявки согласовываем окна приёма у мастера.",
                verbose_name="Мастер принимает на дому",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="master_address",
            field=models.CharField(
                blank=True, default="", max_length=512, verbose_name="Адрес приёма у мастера"
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="proposed_slots",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Список строк/объектов {"label": "..."} от мастера.',
                verbose_name="Предложенные окна приёма",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="agreed_slot",
            field=models.CharField(
                blank=True, default="", max_length=255, verbose_name="Согласованное окно"
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="schedule_agreed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="rating_asked_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="Запрошена оценка работы",
            ),
        ),
        migrations.AlterField(
            model_name="workrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Новая"),
                    ("offering", "Ищем исполнителя"),
                    ("scheduling", "Согласование времени"),
                    ("in_progress", "В работе"),
                    ("awaiting_client", "Ждём подтверждения клиента"),
                    ("awaiting_commission", "Ждём комиссию 10%"),
                    ("done", "Выполнена"),
                    ("cancelled", "Отменена"),
                ],
                db_index=True,
                default="pending",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="WorkRequestRating",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.PositiveSmallIntegerField(verbose_name="Оценка 1–5")),
                ("comment", models.TextField(blank=True, default="", verbose_name="Комментарий")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="work_ratings_given",
                        to="database.botuser",
                    ),
                ),
                (
                    "contractor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="work_ratings",
                        to="database.contractorprofile",
                    ),
                ),
                (
                    "work_request",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rating",
                        to="database.workrequest",
                    ),
                ),
            ],
            options={
                "verbose_name": "Оценка заявки",
                "verbose_name_plural": "Оценки заявок",
                "ordering": ["-created_at"],
            },
        ),
    ]
