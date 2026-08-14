from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0033_ratings_home_scheduling"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Черновик"),
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
        migrations.AlterField(
            model_name="appsettings",
            name="payment_phone",
            field=models.CharField(
                blank=True, default="", max_length=32, verbose_name="Телефон для оплаты"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="payment_name",
            field=models.CharField(
                blank=True, default="", max_length=255, verbose_name="Получатель оплаты"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="service_payee_name",
            field=models.CharField(
                blank=True, default="", max_length=255, verbose_name="Сервис: получатель"
            ),
        ),
        migrations.AlterField(
            model_name="appsettings",
            name="service_payee_phone",
            field=models.CharField(
                blank=True, default="", max_length=32, verbose_name="Сервис: телефон"
            ),
        ),
        migrations.AlterField(
            model_name="panelprofile",
            name="role",
            field=models.CharField(
                choices=[("admin", "Администратор"), ("manager", "Менеджер")],
                db_index=True,
                default="manager",
                max_length=16,
                verbose_name="Роль",
            ),
        ),
    ]
