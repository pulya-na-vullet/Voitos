# Clear seeded executor roles — admin will create the catalog manually.

from django.db import migrations, models


def clear_roles(apps, schema_editor):
    ExecutorRole = apps.get_model("database", "ExecutorRole")
    ContractorProfile = apps.get_model("database", "ContractorProfile")
    WorkRequest = apps.get_model("database", "WorkRequest")
    WorkRequestPhoto = apps.get_model("database", "WorkRequestPhoto")
    AdminTask = apps.get_model("database", "AdminTask")

    ContractorProfile.objects.exclude(role_id=None).update(role_id=None)

    wr_ids = list(WorkRequest.objects.values_list("id", flat=True))
    if wr_ids:
        WorkRequestPhoto.objects.filter(request_id__in=wr_ids).delete()
        AdminTask.objects.filter(
            source_model="WorkRequest", source_id__in=wr_ids
        ).delete()
        WorkRequest.objects.all().delete()

    ExecutorRole.objects.all().delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("database", "0028_executor_role_flags"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="executorrole",
            options={
                "ordering": ["id"],
                "verbose_name": "Роль исполнителя",
                "verbose_name_plural": "Роли исполнителей",
            },
        ),
        migrations.AlterField(
            model_name="executorrole",
            name="sort_order",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Служебное поле; в панели не редактируется — список по порядку создания.",
                verbose_name="Порядок в списке",
            ),
        ),
        migrations.RunPython(clear_roles, noop),
    ]
