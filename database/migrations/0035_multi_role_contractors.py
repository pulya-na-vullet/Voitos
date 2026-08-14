# Generated manually for multi-role contractors

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0034_security_draft_defaults"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contractorprofile",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="contractor_profiles",
                to="database.botuser",
            ),
        ),
        migrations.AddConstraint(
            model_name="contractorprofile",
            constraint=models.UniqueConstraint(
                fields=("user", "equipment_type"),
                name="uniq_contractor_user_equipment_type",
            ),
        ),
    ]
