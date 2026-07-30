from __future__ import annotations

from django.db import models
from django.utils import timezone


class MemoryCategory(models.TextChoices):
    PURCHASES = "purchases", "Покупки"
    HOME = "home", "Дом"
    CAR = "car", "Автомобиль"
    FINANCE = "finance", "Финансы"
    HEALTH = "health", "Здоровье"
    PREFERENCES = "preferences", "Предпочтения"
    PEOPLE = "people", "Люди"
    IDEAS = "ideas", "Идеи"
    OTHER = "other", "Прочее"


class TaskStatus(models.TextChoices):
    OPEN = "open", "Открыта"
    DONE = "done", "Выполнена"


class MessageRole(models.TextChoices):
    USER = "user", "Пользователь"
    ASSISTANT = "assistant", "Помощник"
    SYSTEM = "system", "Система"


class ActivityKind(models.TextChoices):
    MESSAGE_IN = "message_in", "Входящее сообщение"
    MESSAGE_OUT = "message_out", "Исходящее сообщение"
    MEMORY_SAVE = "memory_save", "Сохранение памяти"
    MEMORY_DELETE = "memory_delete", "Удаление памяти"
    TASK_CREATE = "task_create", "Создание задачи"
    TASK_DONE = "task_done", "Задача выполнена"
    TASK_DELETE = "task_delete", "Удаление задачи"
    REMINDER_CREATE = "reminder_create", "Создание напоминания"
    REMINDER_SENT = "reminder_sent", "Отправка напоминания"
    REMINDER_DELETE = "reminder_delete", "Удаление напоминания"
    ACCESS_DENIED = "access_denied", "Отказ в доступе"
    ERROR = "error", "Ошибка"
    SETTINGS = "settings", "Настройки"
    OTHER = "other", "Прочее"


class AppSettings(models.Model):
    """Singleton runtime settings editable from the admin panel."""

    max_bot_token = models.CharField("Токен бота MAX", max_length=512, blank=True, default="")
    allowed_max_user_id = models.CharField(
        "ID разрешённого пользователя MAX",
        max_length=64,
        blank=True,
        default="",
        help_text="Только этот user_id сможет пользоваться ботом. Оставьте пустым, чтобы запомнить первого.",
    )
    yandex_api_key = models.CharField("Yandex API Key", max_length=512, blank=True, default="")
    yandex_folder_id = models.CharField("Yandex Folder ID", max_length=128, blank=True, default="")
    yandex_model = models.CharField(
        "Модель YandexGPT",
        max_length=128,
        blank=True,
        default="yandexgpt-lite",
        help_text="Например: yandexgpt-lite, yandexgpt, yandexgpt-5-pro",
    )
    bot_display_name = models.CharField("Имя помощника", max_length=64, default="Voitos")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки"
        verbose_name_plural = "Настройки"

    def __str__(self) -> str:
        return "Настройки Voitos"

    @classmethod
    def load(cls) -> "AppSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def is_ai_configured(self) -> bool:
        return bool(self.yandex_api_key and self.yandex_folder_id)

    def is_bot_configured(self) -> bool:
        return bool(self.max_bot_token)


class BotUser(models.Model):
    max_user_id = models.CharField("MAX user id", max_length=64, unique=True)
    chat_id = models.CharField("MAX chat id", max_length=64, blank=True, default="")
    display_name = models.CharField("Имя", max_length=255, blank=True, default="")
    username = models.CharField("Username", max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.display_name or self.username or self.max_user_id


class MemoryItem(models.Model):
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="memories")
    text = models.TextField("Текст")
    category = models.CharField(
        "Категория",
        max_length=32,
        choices=MemoryCategory.choices,
        default=MemoryCategory.OTHER,
    )
    source_message = models.TextField("Исходное сообщение", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Память"
        verbose_name_plural = "Память"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_category_display()}] {self.text[:80]}"


class TaskItem(models.Model):
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="tasks")
    text = models.TextField("Задача")
    status = models.CharField(
        max_length=16,
        choices=TaskStatus.choices,
        default=TaskStatus.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.text[:80]

    def mark_done(self) -> None:
        self.status = TaskStatus.DONE
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at"])


class Reminder(models.Model):
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="reminders")
    text = models.TextField("Текст")
    due_at = models.DateTimeField("Когда напомнить")
    is_done = models.BooleanField("Выполнено", default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Напоминание"
        verbose_name_plural = "Напоминания"
        ordering = ["due_at"]
        indexes = [
            models.Index(fields=["is_done", "due_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.text[:60]} @ {self.due_at}"


class ChatMessage(models.Model):
    user = models.ForeignKey(
        BotUser,
        on_delete=models.CASCADE,
        related_name="messages",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=16, choices=MessageRole.choices)
    text = models.TextField()
    is_voice = models.BooleanField(default=False)
    voice_transcript = models.TextField(blank=True, default="")
    max_message_id = models.CharField(max_length=128, blank=True, default="")
    intent = models.CharField(max_length=64, blank=True, default="")
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["role"]),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.text[:80]}"


class ActivityLog(models.Model):
    user = models.ForeignKey(
        BotUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    kind = models.CharField(max_length=32, choices=ActivityKind.choices, default=ActivityKind.OTHER)
    title = models.CharField(max_length=255)
    detail = models.TextField(blank=True, default="")
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Лог активности"
        verbose_name_plural = "Логи активности"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.title}"


class PendingAction(models.Model):
    """Stores last user text for explicit 'Запомни это' / 'Не запоминай'."""

    user = models.OneToOneField(BotUser, on_delete=models.CASCADE, related_name="pending")
    last_user_text = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ожидающее действие"
        verbose_name_plural = "Ожидающие действия"


class BotRuntimeStatus(models.Model):
    """Live status of the MAX long-polling worker (singleton)."""

    state = models.CharField(max_length=64, default="stopped")
    detail = models.TextField(blank=True, default="")
    bot_name = models.CharField(max_length=255, blank=True, default="")
    bot_username = models.CharField(max_length=255, blank=True, default="")
    last_marker = models.BigIntegerField(null=True, blank=True)
    last_poll_at = models.DateTimeField(null=True, blank=True)
    last_update_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Статус бота"
        verbose_name_plural = "Статус бота"

    def __str__(self) -> str:
        return f"{self.state}: {self.detail[:60]}"

    @classmethod
    def load(cls) -> "BotRuntimeStatus":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
