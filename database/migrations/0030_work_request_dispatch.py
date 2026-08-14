# Work request dispatch: offers, locality, 20-min timeout

from django.db import migrations, models
import django.db.models.deletion


def backfill_client_locality(apps, schema_editor):
    WorkRequest = apps.get_model("database", "WorkRequest")
    for req in WorkRequest.objects.select_related("user").iterator():
        loc = (getattr(req.user, "locality", None) or "").strip()
        if loc and not (req.client_locality or "").strip():
            req.client_locality = loc[:255]
            req.save(update_fields=["client_locality"])


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0029_clear_executor_roles"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Новая"),
                    ("offering", "Ищем исполнителя"),
                    ("in_progress", "В работе"),
                    ("done", "Выполнена"),
                    ("cancelled", "Отменена"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="client_locality",
            field=models.CharField(
                blank=True, default="", max_length=255, verbose_name="НП жителя"
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="assigned_contractor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="accepted_work_requests",
                to="database.contractorprofile",
                verbose_name="Назначенный исполнитель",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="no_executor_notified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Клиенту сообщили, что нет исполнителя",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="dispatch_note",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="Заметка подбора (ИИ / система)",
            ),
        ),
        migrations.CreateModel(
            name="WorkRequestOffer",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("offered", "Предложено"),
                            ("accepted", "Принято"),
                            ("declined", "Отказ"),
                            ("expired", "Истекло"),
                            ("cancelled", "Отменено"),
                        ],
                        db_index=True,
                        default="offered",
                        max_length=16,
                    ),
                ),
                ("offered_at", models.DateTimeField(auto_now_add=True)),
                (
                    "respond_deadline",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                        verbose_name="Ответить до",
                    ),
                ),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("rank_score", models.FloatField(default=0)),
                (
                    "rank_reason",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "contractor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="work_offers",
                        to="database.contractorprofile",
                    ),
                ),
                (
                    "work_request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="offers",
                        to="database.workrequest",
                    ),
                ),
            ],
            options={
                "verbose_name": "Предложение по заявке",
                "verbose_name_plural": "Предложения по заявкам",
                "ordering": ["-offered_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="workrequestoffer",
            constraint=models.UniqueConstraint(
                fields=("work_request", "contractor"),
                name="uniq_work_request_contractor_offer",
            ),
        ),
        migrations.RunPython(backfill_client_locality, migrations.RunPython.noop),
    ]
