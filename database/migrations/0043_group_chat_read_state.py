# Generated manually for group chat unread tracking

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0042_avatar_group_chat"),
    ]

    operations = [
        migrations.CreateModel(
            name="GroupChatReadState",
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
                    "last_read_message_id",
                    models.BigIntegerField(
                        default=0,
                        verbose_name="Последнее прочитанное сообщение",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_read_states",
                        to="database.servicegroup",
                        verbose_name="Группа",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="group_chat_reads",
                        to="database.botuser",
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Прочитанность чата группы",
                "verbose_name_plural": "Прочитанность чатов групп",
                "indexes": [
                    models.Index(
                        fields=["user", "group"],
                        name="database_gr_user_id_chatrd_idx",
                    ),
                ],
                "unique_together": {("user", "group")},
            },
        ),
    ]
