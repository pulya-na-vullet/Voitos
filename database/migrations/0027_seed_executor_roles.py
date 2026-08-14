# Seed default executor roles and link existing contractor profiles

from django.db import migrations


DEFAULT_ROLES = [
    # code, name, requires_docs, is_equipment, for_snow, for_road, for_snow_haul, sort
    ("tractor", "Трактор-погрузчик", False, True, True, True, False, 10),
    ("truck", "Камаз / грузовой", False, True, True, True, True, 20),
    ("handyman", "Разнорабочий", False, False, False, False, False, 30),
    ("mason", "Каменщик", False, False, False, False, False, 40),
    ("tiler", "Плиточник", False, False, False, False, False, 50),
    ("electrician", "Электрик", True, False, False, False, False, 60),
    ("welder", "Сварщик", False, False, False, False, False, 70),
    ("loader", "Грузчик", False, False, False, False, False, 80),
    ("computer_master", "Компьютерный мастер", False, False, False, False, False, 90),
    ("manicure", "Мастер по маникюру", False, False, False, False, False, 100),
    ("tutor_math", "Репетитор математики", True, False, False, False, False, 110),
    ("tutor_russian", "Репетитор русского языка", True, False, False, False, False, 120),
    ("tutor_biology", "Репетитор биологии", True, False, False, False, False, 130),
    ("tutor_english", "Репетитор английского языка", True, False, False, False, False, 140),
]


def seed_roles(apps, schema_editor):
    ExecutorRole = apps.get_model("database", "ExecutorRole")
    ContractorProfile = apps.get_model("database", "ContractorProfile")
    for code, name, docs, equip, snow, road, haul, sort in DEFAULT_ROLES:
        ExecutorRole.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "requires_qualification_docs": docs,
                "is_equipment": equip,
                "for_snow": snow,
                "for_road": road,
                "for_snow_haul": haul,
                "is_active": True,
                "sort_order": sort,
            },
        )
    by_code = {r.code: r for r in ExecutorRole.objects.all()}
    for profile in ContractorProfile.objects.all():
        code = (profile.equipment_type or "").strip()
        role = by_code.get(code)
        if role:
            profile.role_id = role.id
            if not profile.equipment_type:
                profile.equipment_type = role.code
            profile.save(update_fields=["role_id", "equipment_type"])


def unseed(apps, schema_editor):
    ExecutorRole = apps.get_model("database", "ExecutorRole")
    ExecutorRole.objects.filter(code__in=[r[0] for r in DEFAULT_ROLES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("database", "0026_executor_roles_work_requests_manager_logs"),
    ]

    operations = [
        migrations.RunPython(seed_roles, unseed),
    ]
