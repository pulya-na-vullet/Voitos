from django.apps import AppConfig


class DatabaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "database"
    verbose_name = "Данные Voitos"

    def ready(self) -> None:
        from database import signals  # noqa: F401
