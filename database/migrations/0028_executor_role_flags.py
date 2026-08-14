from django.db import migrations, models


SYSTEM_FLAG_DEFS = [
    ("requires_qualification_docs", "нужны подтверждающие документы"),
    ("is_equipment", "техника (госномер)"),
    ("for_snow", "снег"),
    ("for_road", "дорога"),
    ("for_snow_haul", "вывоз снега"),
]


def populate_flags(apps, schema_editor):
    ExecutorRole = apps.get_model("database", "ExecutorRole")
    for role in ExecutorRole.objects.all():
        flags = []
        for code, label in SYSTEM_FLAG_DEFS:
            if getattr(role, code, False):
                flags.append({"code": code, "label": label, "on": True})
        role.flags = flags
        role.save(update_fields=["flags"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0027_seed_executor_roles"),
    ]

    operations = [
        migrations.AddField(
            model_name="executorrole",
            name="flags",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Список признаков роли; админ добавляет и удаляет в панели.",
                verbose_name="Признаки (чекбоксы)",
            ),
        ),
        migrations.AlterField(
            model_name="executorrole",
            name="sort_order",
            field=models.PositiveIntegerField(
                default=100,
                help_text="Сортировка в боте и панели: меньше число — выше в списке (10, 20, 30…).",
                verbose_name="Порядок в списке",
            ),
        ),
        migrations.RunPython(populate_flags, noop_reverse),
    ]
