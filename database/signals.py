from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def ensure_settings_singleton(sender, **kwargs) -> None:
    if sender.name != "database":
        return
    from database.models import AppSettings, BotRuntimeStatus

    AppSettings.load()
    BotRuntimeStatus.load()
