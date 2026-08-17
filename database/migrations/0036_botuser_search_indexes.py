from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0035_multi_role_contractors"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="botuser",
            index=models.Index(fields=["real_name"], name="botuser_real_name_idx"),
        ),
        migrations.AddIndex(
            model_name="botuser",
            index=models.Index(fields=["phone"], name="botuser_phone_idx"),
        ),
        migrations.AddIndex(
            model_name="botuser",
            index=models.Index(fields=["locality"], name="botuser_locality_idx"),
        ),
        migrations.AddIndex(
            model_name="botuser",
            index=models.Index(
                fields=["display_name"], name="botuser_display_name_idx"
            ),
        ),
    ]
