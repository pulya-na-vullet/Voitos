# Generated manually: keep group chat messages when BotUser is deleted.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0060_app_version_0_2_18"),
    ]

    operations = [
        migrations.AlterField(
            model_name="groupchatmessage",
            name="author",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="group_chat_messages",
                to="database.botuser",
                verbose_name="Автор",
            ),
        ),
    ]
