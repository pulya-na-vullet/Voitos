from decimal import Decimal

from django.db import migrations, models
from django.db.models import F


def backfill_executor_earned(apps, schema_editor):
    WorkRequest = apps.get_model("database", "WorkRequest")
    qs = WorkRequest.objects.filter(
        confirmed_amount__isnull=False,
        commission_amount__isnull=False,
        executor_earned_amount__isnull=True,
    ).exclude(confirmed_amount__lt=0)
    for wr in qs.iterator():
        confirmed = wr.confirmed_amount or Decimal("0")
        commission = wr.commission_amount or Decimal("0")
        net = confirmed - commission
        if net < 0:
            net = Decimal("0.00")
        wr.executor_earned_amount = net.quantize(Decimal("0.01"))
        wr.save(update_fields=["executor_earned_amount"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0031_work_request_completion_commission"),
    ]

    operations = [
        migrations.AddField(
            model_name="workrequest",
            name="executor_earned_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=12,
                null=True,
                verbose_name="Заработок исполнителя (сумма клиента − комиссия), ₽",
            ),
        ),
        migrations.RunPython(backfill_executor_earned, noop_reverse),
    ]
