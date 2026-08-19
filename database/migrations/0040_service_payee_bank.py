# Generated manually for service payee bank

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0039_botuser_comic_onboarding"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="service_payee_bank",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Например: Сбер, Т-Банк — показывается жителям в сборе.",
                max_length=255,
                verbose_name="Сервис: банк для перевода",
            ),
        ),
    ]
