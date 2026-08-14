from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0035_multi_role_contractors"),
    ]

    operations = [
        migrations.AddField(
            model_name="workrequest",
            name="agreed_slot_end_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Когда опросить клиента: оказана ли услуга.",
                null=True,
                verbose_name="Конец согласованного окна",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="service_done_asked_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Спросили клиента об оказании услуги",
            ),
        ),
        migrations.AddField(
            model_name="workrequest",
            name="service_provided_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Клиент подтвердил оказание услуги",
            ),
        ),
    ]
