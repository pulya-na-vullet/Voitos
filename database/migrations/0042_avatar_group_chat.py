# Generated manually for avatar + group chat

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0041_feedback_ticket"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="avatar",
            field=models.ImageField(
                blank=True,
                help_text="Квадрат 500×500 (приложение/сервер приводят к размеру).",
                null=True,
                upload_to="avatars/%Y/%m/",
                verbose_name="Аватар",
            ),
        ),
        migrations.CreateModel(
            name="GroupChatMessage",
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
                ("text", models.TextField(max_length=4000, verbose_name="Текст")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="group_chat_messages",
                        to="database.botuser",
                        verbose_name="Автор",
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_messages",
                        to="database.servicegroup",
                        verbose_name="Группа",
                    ),
                ),
            ],
            options={
                "verbose_name": "Сообщение группового чата",
                "verbose_name_plural": "Сообщения группового чата",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["group", "-created_at"],
                        name="database_gr_group_i_7a1c2d_idx",
                    ),
                    models.Index(
                        fields=["group", "id"],
                        name="database_gr_group_i_9b4e0f_idx",
                    ),
                ],
            },
        ),
    ]
