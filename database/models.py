from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
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
    RECEIPT_SUBMITTED = "receipt_submitted", "Чек отправлен"
    RECEIPT_APPROVED = "receipt_approved", "Чек принят"
    RECEIPT_REJECTED = "receipt_rejected", "Чек отклонён"
    SUBSCRIPTION_EXPIRED = "subscription_expired", "Подписка истекла"
    PROFILE_SUBMITTED = "profile_submitted", "Анкета отправлена"
    PROFILE_VERIFIED = "profile_verified", "Анкета проверена"
    SERVICE_OFFER = "service_offer", "Сервисное предложение"
    SERVICE_RECEIPT = "service_receipt", "Чек сервисного сбора"
    SERVICE_PAID = "service_paid", "Сервисный сбор оплачен"
    SERVICE_NOTICE = "service_notice", "Уведомление по сбору"
    SERVICE_WISH = "service_wish", "Пожелание по группе"
    VOLUNTEER_ASK = "volunteer_ask", "Вопрос о помощи на мероприятии"
    VOLUNTEER_REPLY = "volunteer_reply", "Ответ о помощи на мероприятии"
    CONTRACTOR_REGISTER = "contractor_register", "Регистрация исполнителя"
    CONTRACTOR_OFFER = "contractor_offer", "Предложение исполнителю"
    CONTRACTOR_REPLY = "contractor_reply", "Ответ исполнителя"
    CONTRACTOR_PAYOUT = "contractor_payout", "Оплата исполнителю"
    ERROR = "error", "Ошибка"
    SETTINGS = "settings", "Настройки"
    OTHER = "other", "Прочее"


class WishTopic(models.TextChoices):
    ROAD = "road", "Дороги"
    PLAYGROUND = "playground", "Детская площадка"
    LIGHTING = "lighting", "Освещение"
    SNOW = "snow", "Чистка снега"
    DOGS = "dogs", "Собаки / намордники"
    TRASH = "trash", "Мусор"
    PARKING = "parking", "Парковка"
    SAFETY = "safety", "Безопасность"
    GREEN = "green", "Озеленение"
    OTHER = "other", "Прочее"


class ProfileStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Не заполнена"
    PENDING_REVIEW = "pending_review", "Проверить данные"
    VERIFIED = "verified", "Проверена"
    REJECTED = "rejected", "Отклонена"


class ServiceCategory(models.TextChoices):
    SNOW = "snow", "Чистка снега"
    PLAYGROUND = "playground", "Детская площадка"
    LIGHTING = "lighting", "Освещение"
    ROAD = "road", "Ремонт дороги"


class CampaignStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    ACTIVE = "active", "Активен"
    CLOSED = "closed", "Закрыт"


class WorkStage(models.TextChoices):
    COLLECTING = "collecting", "Сбор денег"
    WORK_STARTED = "work_started", "Начало работ"
    WORK_DONE = "work_done", "Работа выполнена"
    WORK_CLOSED = "work_closed", "Работа закрыта"


class InviteStatus(models.TextChoices):
    OFFERED = "offered", "Предложено"
    PAID = "paid", "Оплачено"
    DECLINED = "declined", "Отказ"
    CANCELLED = "cancelled", "Отменено"


class VolunteerReplyStatus(models.TextChoices):
    PENDING = "pending", "Ожидает ответа"
    YES = "yes", "Поможет"
    NO = "no", "Не поможет"


class ResidentHelperStatus(models.TextChoices):
    ASSIGNED = "assigned", "Назначен"
    CANCELLED = "cancelled", "Снят"


class CampaignNoticeKind(models.TextChoices):
    CLOSED = "closed", "Сбор закрыт"
    SURPLUS = "surplus", "Остаток в бюджет"
    REMIND_3D = "remind_3d", "Напоминание за 3 дня"
    REMIND_1D = "remind_1d", "Напоминание за 1 день"
    REMIND_2H = "remind_2h", "Напоминание за 2 часа"


class ReceiptStatus(models.TextChoices):
    PENDING = "pending", "На проверке"
    APPROVED = "approved", "Принят"
    REJECTED = "rejected", "Отклонён"


class AccessState(models.TextChoices):
    ACTIVE = "active", "Активна"
    GRACE = "grace", "Ожидание оплаты"
    BLOCKED = "blocked", "Доступ закрыт"


class ReminderRepeat(models.TextChoices):
    NONE = "none", "Один раз"
    DAILY = "daily", "Каждый день"


class AdminTaskStatus(models.TextChoices):
    OPEN = "open", "Открыта"
    DONE = "done", "Выполнена"
    DISMISSED = "dismissed", "Скрыта"


class AdminTaskKind(models.TextChoices):
    PAYMENT_RECEIPT = "payment_receipt", "Чек подписки"
    SERVICE_RECEIPT = "service_receipt", "Сервис-чек"
    PROFILE_REVIEW = "profile_review", "Проверка анкеты"
    FAMILY_CLAIM = "family_claim", "Семейная заявка"
    ADDRESS_OVERLAP = "address_overlap", "Совпадение адреса"
    CONTRACTOR_REVIEW = "contractor_review", "Проверка исполнителя"
    CONTRACTOR_COUNTER = "contractor_counter", "Другое время исполнителя"


class EquipmentType(models.TextChoices):
    TRACTOR = "tractor", "Трактор-погрузчик"
    TRUCK = "truck", "Камаз / грузовой"


class ContractorStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "На проверке"
    VERIFIED = "verified", "Проверен"
    REJECTED = "rejected", "Отклонён"
    DISABLED = "disabled", "Отключён"


class AssignmentStatus(models.TextChoices):
    OFFERED = "offered", "Предложено"
    COUNTER_OFFER = "counter_offer", "Другое время"
    ACCEPTED = "accepted", "Согласен"
    DECLINED = "declined", "Отказ"
    REJECTED_TIME = "rejected_time", "Время отклонено"
    CANCELLED = "cancelled", "Отменено"
    EXPIRED = "expired", "Истекло"


class AppSettings(models.Model):
    """Singleton runtime settings editable from the admin panel."""

    max_bot_token = models.CharField("Токен бота MAX", max_length=512, blank=True, default="")
    allowed_max_user_id = models.CharField(
        "Ограничение одного user_id (устарело)",
        max_length=64,
        blank=True,
        default="",
        help_text="Оставьте пустым — бот доступен нескольким пользователям.",
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
    payment_phone = models.CharField(
        "Телефон для оплаты",
        max_length=32,
        default="89625507832",
    )
    payment_name = models.CharField(
        "Получатель оплаты",
        max_length=255,
        default="Григорьев Дмитрий Вячеславович",
    )
    subscription_price_rub = models.PositiveIntegerField("Цена подписки, ₽/мес", default=100)
    grace_days = models.PositiveIntegerField(
        "Пробный / льготный период, дней",
        default=14,
        help_text="Для новых пользователей — пробный доступ с первого входа; "
        "после истечения подписки — столько же дней на оплату.",
    )
    service_payee_name = models.CharField(
        "Сервис: получатель",
        max_length=255,
        default="Григорьев Д.В.",
    )
    service_payee_phone = models.CharField(
        "Сервис: телефон",
        max_length=32,
        default="89625507832",
    )
    service_payee_status = models.CharField(
        "Сервис: статус",
        max_length=64,
        default="Самозанятый",
    )
    service_tax_limit = models.DecimalField(
        "Лимит самозанятого, ₽",
        max_digits=14,
        decimal_places=2,
        default=Decimal("2400000"),
    )
    service_tax_collected = models.DecimalField(
        "Собрано через самозанятого, ₽",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Считается автоматически из принятых чеков подписки и сервисных сборов за текущий год.",
    )
    yandex_llm_rub_per_1k = models.DecimalField(
        "YandexGPT, ₽ / 1000 токенов",
        max_digits=10,
        decimal_places=4,
        default=Decimal("0.40"),
        help_text="Оценка для дашборда оплаты. Уточните по тарифу в Yandex Cloud.",
    )
    yandex_stt_rub_per_request = models.DecimalField(
        "SpeechKit STT, ₽ / запрос",
        max_digits=10,
        decimal_places=4,
        default=Decimal("0.15"),
    )
    yandex_ocr_rub_per_page = models.DecimalField(
        "OCR, ₽ / страница",
        max_digits=10,
        decimal_places=4,
        default=Decimal("0.10"),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def refresh_tax_collected(self) -> Decimal:
        """Sync service_tax_collected from approved receipts (current year)."""
        from services.tax import sync_self_employed_tax_collected

        stats = sync_self_employed_tax_collected(self)
        # keep in-memory field in sync for subsequent reads
        self.service_tax_collected = stats["total"]
        return stats["total"]

    def tax_usage_ratio(self) -> float:
        limit = float(self.service_tax_limit or 0)
        if limit <= 0:
            return 0.0
        collected = float(self.service_tax_collected or 0)
        return collected / limit

    def tax_limit_warning(self) -> str:
        # Always show warning against fresh totals from approved receipts.
        try:
            self.refresh_tax_collected()
        except Exception:
            pass
        ratio = self.tax_usage_ratio()
        year = timezone.localdate().year
        if ratio >= 1.0:
            return (
                f"Лимит самозанятого за {year} исчерпан "
                f"({self.service_tax_collected} / {self.service_tax_limit} ₽). "
                "Смените получателя в настройках сервисных реквизитов."
            )
        if ratio >= 0.85:
            left = Decimal(self.service_tax_limit or 0) - Decimal(self.service_tax_collected or 0)
            return (
                f"Приближение к лимиту самозанятого за {year}: собрано "
                f"{self.service_tax_collected} из {self.service_tax_limit} ₽ "
                f"(осталось ~{left} ₽). Рекомендуется сменить самозанятого."
            )
        return ""

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
    display_name = models.CharField("Имя в MAX", max_length=255, blank=True, default="")
    username = models.CharField("Username", max_length=255, blank=True, default="")
    real_name = models.CharField("Имя (анкета)", max_length=255, blank=True, default="")
    phone = models.CharField("Телефон", max_length=32, blank=True, default="")
    address = models.TextField("Адрес", blank=True, default="")
    locality = models.CharField("Населённый пункт", max_length=255, blank=True, default="")
    profile_status = models.CharField(
        "Статус анкеты",
        max_length=32,
        choices=ProfileStatus.choices,
        default=ProfileStatus.INCOMPLETE,
    )
    profile_admin_note = models.TextField("Заметка по анкете", blank=True, default="")
    profile_submitted_at = models.DateTimeField(null=True, blank=True)
    profile_verified_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    subscription_until = models.DateTimeField(
        "Подписка до",
        null=True,
        blank=True,
        help_text="До этой даты функции бота доступны.",
    )
    grace_until = models.DateTimeField(
        "Льготный период до",
        null=True,
        blank=True,
        help_text="После истечения подписки ждём оплату до этой даты.",
    )
    last_payment_notice_at = models.DateTimeField(null=True, blank=True)
    family_payer = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="family_dependents",
        verbose_name="Подписку оплатил (семья)",
        help_text="Если указан — доступ дублируется с подписки этого члена семьи.",
    )
    citizen_score = models.PositiveSmallIntegerField(
        "Внутренний рейтинг гражданина",
        default=100,
        help_text="0–100. Пользователю не показывается. По умолчанию 100.",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        ordering = ["-last_seen_at"]

    def __str__(self) -> str:
        return self.real_name or self.display_name or self.username or self.max_user_id

    def profile_complete(self) -> bool:
        return bool(self.real_name.strip() and self.phone.strip() and self.address.strip())

    def effective_subscription_until(self):
        """Own date or family payer's date (whichever is later)."""
        own = self.subscription_until
        payer_id = getattr(self, "family_payer_id", None)
        if not payer_id:
            return own
        payer = self.family_payer
        if not payer or not payer.subscription_until:
            return own
        if own and own >= payer.subscription_until:
            return own
        return payer.subscription_until

    def subscription_paid_by(self):
        """
        Member who currently covers access via family link.

        Returns the payer only when their subscription is what grants access
        (payer's until is not worse than own).
        """
        payer_id = getattr(self, "family_payer_id", None)
        if not payer_id:
            return None
        payer = self.family_payer
        if not payer or not payer.subscription_until:
            return None
        own = self.subscription_until
        if own and own >= payer.subscription_until:
            return None
        return payer

    def subscription_label(self) -> str:
        """Human-readable subscription line for admin panel."""
        until = self.effective_subscription_until()
        if not until:
            return "нет"
        date_s = timezone.localtime(until).strftime("%d.%m.%Y")
        payer = self.subscription_paid_by()
        if payer:
            return f"оплачена {payer} до {date_s}"
        return f"до {date_s}"

    def access_state(self) -> str:
        now = timezone.now()
        until = self.effective_subscription_until()
        if until and until > now:
            return AccessState.ACTIVE
        if self.grace_until and self.grace_until > now:
            return AccessState.GRACE
        # New user without subscription: start grace from first_seen
        if not until and not self.grace_until:
            cfg = AppSettings.load()
            grace_end = self.first_seen_at + timedelta(days=cfg.grace_days or 14)
            if grace_end > now:
                return AccessState.GRACE
        return AccessState.BLOCKED

    def has_feature_access(self) -> bool:
        return self.access_state() in {AccessState.ACTIVE, AccessState.GRACE}

    def ensure_grace_period(self) -> None:
        """When subscription ends, open a grace window once."""
        now = timezone.now()
        cfg = AppSettings.load()
        until = self.effective_subscription_until()
        if until and until <= now:
            if not self.grace_until or self.grace_until < until:
                self.grace_until = until + timedelta(days=cfg.grace_days or 14)
                self.save(update_fields=["grace_until"])
        elif not until and not self.grace_until:
            self.grace_until = self.first_seen_at + timedelta(days=cfg.grace_days or 14)
            self.save(update_fields=["grace_until"])

    def extend_subscription(self, months: int = 0, days: int = 0) -> None:
        """Extend access by calendar-ish months (30 days each) plus extra days."""
        months = max(0, int(months))
        days = max(0, int(days))
        total_days = 30 * months + days
        if total_days < 1:
            raise ValueError("Срок продления должен быть больше нуля")
        now = timezone.now()
        base = self.subscription_until if self.subscription_until and self.subscription_until > now else now
        self.subscription_until = base + timedelta(days=total_days)
        self.grace_until = None
        self.save(update_fields=["subscription_until", "grace_until", "last_seen_at"])


class PaymentReceipt(models.Model):
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="receipts")
    image = models.FileField("Файл чека", upload_to="receipts/%Y/%m/", blank=True)
    image_url = models.URLField(blank=True, default="")
    content_hash = models.CharField(
        "SHA-256 файла",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Хеш байтов файла для поиска попиксельно одинаковых чеков.",
    )
    ocr_text = models.TextField("Распознанный текст", blank=True, default="")
    amount = models.DecimalField("Сумма", max_digits=12, decimal_places=2, null=True, blank=True)
    transfer_date = models.DateField("Дата перевода", null=True, blank=True)
    recipient_phone = models.CharField(max_length=64, blank=True, default="")
    recipient_name = models.CharField(max_length=255, blank=True, default="")
    months_granted = models.PositiveIntegerField(default=0)
    days_granted = models.PositiveIntegerField("Дней (остаток)", default=0)
    status = models.CharField(
        max_length=16,
        choices=ReceiptStatus.choices,
        default=ReceiptStatus.PENDING,
    )
    ai_notes = models.TextField(blank=True, default="")
    details_match = models.BooleanField(
        "Реквизиты совпали",
        default=False,
        help_text="Телефон/получатель распознаны и совпали с настройками.",
    )
    admin_comment = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Чек оплаты"
        verbose_name_plural = "Чеки оплаты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Чек #{self.pk} {self.amount or '?'}₽ ({self.status})"

    def calc_months(self, price: int | None = None) -> int:
        price = price or AppSettings.load().subscription_price_rub or 100
        if not self.amount or self.amount <= 0:
            return 0
        return max(0, int(Decimal(self.amount) // Decimal(price)))

    def period_label(self) -> str:
        parts = []
        if self.months_granted:
            parts.append(f"{self.months_granted} мес.")
        if self.days_granted:
            parts.append(f"{self.days_granted} дн.")
        return " ".join(parts) if parts else "0"


class PanelRole(models.TextChoices):
    ADMIN = "admin", "Администратор"
    MANAGER = "manager", "Менеджер"


class PanelProfile(models.Model):
    """Роль пользователя панели (администратор / менеджер)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="panel_profile",
        verbose_name="Учётная запись",
    )
    role = models.CharField(
        "Роль",
        max_length=16,
        choices=PanelRole.choices,
        default=PanelRole.ADMIN,
        db_index=True,
    )
    bot_user = models.OneToOneField(
        "BotUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="panel_account",
        verbose_name="Пользователь бота",
        help_text="Для менеджера — житель, из которого назначена роль.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Профиль панели"
        verbose_name_plural = "Профили панели"

    def __str__(self) -> str:
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_admin(self) -> bool:
        return self.role == PanelRole.ADMIN

    @property
    def is_manager(self) -> bool:
        return self.role == PanelRole.MANAGER


class ServiceGroup(models.Model):
    """Admin-defined group of residents for service campaign broadcasts."""

    name = models.CharField("Название группы", max_length=255)
    description = models.TextField(blank=True, default="")
    members = models.ManyToManyField(
        BotUser, blank=True, related_name="service_groups"
    )
    # Один менеджер (администратор группы) на группу; один менеджер — на несколько групп.
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_service_groups",
        verbose_name="Менеджер группы",
        help_text="Роль менеджера: один на группу, может вести несколько групп.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Группа жителей"
        verbose_name_plural = "Группы жителей"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def member_count(self) -> int:
        return self.members.count()


class NeighborhoodWish(models.Model):
    """Resident idea / vote for improvements in their ServiceGroup."""

    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="wishes")
    group = models.ForeignKey(
        ServiceGroup,
        on_delete=models.CASCADE,
        related_name="wishes",
        verbose_name="Группа",
    )
    text = models.TextField("Пожелание")
    source_message = models.TextField("Исходное сообщение", blank=True, default="")
    topic = models.CharField(
        "Тема",
        max_length=32,
        choices=WishTopic.choices,
        default=WishTopic.OTHER,
        db_index=True,
    )
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Пожелание жителей"
        verbose_name_plural = "Пожелания жителей"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["group", "topic"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_topic_display()}] {self.text[:60]}"


class ServiceCampaign(models.Model):
    category = models.CharField(max_length=32, choices=ServiceCategory.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    locality = models.CharField("Населённый пункт", max_length=255, blank=True, default="")
    group = models.ForeignKey(
        "ServiceGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
        verbose_name="Группа",
    )
    total_amount = models.DecimalField("Общая сумма, ₽", max_digits=12, decimal_places=2)
    amount_per_user = models.DecimalField(
        "Сумма с участника, ₽",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
    )
    event_at = models.DateTimeField(
        "Дата мероприятия",
        null=True,
        blank=True,
        help_text="К этой дате должен быть выполнен сбор; напоминания неоплатившим — за 3 дня, 1 день и 2 часа.",
    )
    needs_snow_haul = models.BooleanField(
        "Нужен вывоз снега",
        default=False,
        help_text="Для чистки снега: подбирать водителей грузовых (камаз) на вывоз.",
    )
    status = models.CharField(
        max_length=16,
        choices=CampaignStatus.choices,
        default=CampaignStatus.DRAFT,
    )
    work_stage = models.CharField(
        "Этап работ",
        max_length=32,
        choices=WorkStage.choices,
        default=WorkStage.COLLECTING,
    )
    work_stage_changed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Сервисное мероприятие"
        verbose_name_plural = "Сервисные мероприятия"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        date_s = timezone.localtime(self.created_at).strftime("%d.%m.%Y") if self.created_at else ""
        return f"{self.get_category_display()} — {self.title or self.get_category_display()} от {date_s}"

    @property
    def collected_amount(self) -> Decimal:
        total = Decimal("0")
        for inv in self.invites.all():
            total += Decimal(inv.amount_paid or 0)
        return total

    @property
    def progress_percent(self) -> int:
        if not self.total_amount or self.total_amount <= 0:
            return 0
        return min(100, int(self.collected_amount * 100 / Decimal(self.total_amount)))


class ServiceCampaignOfferPhoto(models.Model):
    """Optional photos of what needs to be done — sent with the initial offer."""

    campaign = models.ForeignKey(
        ServiceCampaign,
        on_delete=models.CASCADE,
        related_name="offer_photos",
    )
    image = models.ImageField("Фото задачи", upload_to="service_offers/%Y/%m/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Фото задачи сбора"
        verbose_name_plural = "Фото задач сборов"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"Задача-фото #{self.pk} → кампания {self.campaign_id}"


class ServiceCampaignResultPhoto(models.Model):
    campaign = models.ForeignKey(
        ServiceCampaign,
        on_delete=models.CASCADE,
        related_name="result_photos",
    )
    image = models.ImageField("Фото результата", upload_to="service_results/%Y/%m/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Фото результата работ"
        verbose_name_plural = "Фото результатов работ"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"Результат #{self.pk} → кампания {self.campaign_id}"


class ServiceCampaignNotice(models.Model):
    """Idempotent log of campaign notifications (close / surplus / unpaid reminders)."""

    campaign = models.ForeignKey(
        ServiceCampaign, on_delete=models.CASCADE, related_name="notices"
    )
    user = models.ForeignKey(
        BotUser,
        on_delete=models.CASCADE,
        related_name="service_notices",
    )
    kind = models.CharField(max_length=32, choices=CampaignNoticeKind.choices)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление по сбору"
        verbose_name_plural = "Уведомления по сборам"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "user", "kind"],
                name="uniq_campaign_user_notice_kind",
            )
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id}:{self.kind}:{self.user_id}"


class ServiceInvite(models.Model):
    campaign = models.ForeignKey(
        ServiceCampaign, on_delete=models.CASCADE, related_name="invites"
    )
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="service_invites")
    amount_due = models.DecimalField("К оплате, ₽", max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(
        "Оплачено, ₽", max_digits=12, decimal_places=2, default=Decimal("0")
    )
    status = models.CharField(
        max_length=16,
        choices=InviteStatus.choices,
        default=InviteStatus.OFFERED,
    )
    offered_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Предложение сбора"
        verbose_name_plural = "Предложения сборов"
        ordering = ["-offered_at"]
        unique_together = [("campaign", "user")]

    def __str__(self) -> str:
        return f"{self.user} → {self.campaign_id}: {self.amount_due}₽"


class VolunteerHelpAsk(models.Model):
    """Обязательный вопрос жителю: поможет ли на площадке / ремонте дороги."""

    campaign = models.ForeignKey(
        ServiceCampaign, on_delete=models.CASCADE, related_name="volunteer_asks"
    )
    user = models.ForeignKey(
        BotUser, on_delete=models.CASCADE, related_name="volunteer_asks"
    )
    status = models.CharField(
        max_length=16,
        choices=VolunteerReplyStatus.choices,
        default=VolunteerReplyStatus.PENDING,
    )
    score_delta = models.IntegerField("Изменение рейтинга", default=0)
    asked_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Вопрос о помощи"
        verbose_name_plural = "Вопросы о помощи"
        ordering = ["-asked_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "user"],
                name="uniq_volunteer_ask_campaign_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.campaign_id}: {self.status}"


class CampaignResidentHelper(models.Model):
    """Житель группы, назначенный исполнителем на площадку / волонтёрскую задачу."""

    campaign = models.ForeignKey(
        ServiceCampaign, on_delete=models.CASCADE, related_name="resident_helpers"
    )
    user = models.ForeignKey(
        BotUser, on_delete=models.CASCADE, related_name="resident_helper_roles"
    )
    status = models.CharField(
        max_length=16,
        choices=ResidentHelperStatus.choices,
        default=ResidentHelperStatus.ASSIGNED,
    )
    sort_order = models.PositiveIntegerField(default=0)
    assigned_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Исполнитель из группы"
        verbose_name_plural = "Исполнители из группы"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "user"],
                name="uniq_campaign_resident_helper",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.campaign_id} ({self.status})"


class ContractorProfile(models.Model):
    """Владелец техники: тракторист / водитель камаза."""

    user = models.OneToOneField(
        BotUser,
        on_delete=models.CASCADE,
        related_name="contractor_profile",
    )
    equipment_type = models.CharField(
        "Тип техники",
        max_length=32,
        choices=EquipmentType.choices,
    )
    equipment_label = models.CharField(
        "Модель / описание",
        max_length=255,
        blank=True,
        default="",
    )
    plate_number = models.CharField("Госномер", max_length=32, blank=True, default="")
    phone = models.CharField("Телефон для связи", max_length=32, blank=True, default="")
    payout_phone = models.CharField(
        "Телефон для перевода денег",
        max_length=32,
        blank=True,
        default="",
        help_text="Если отличается от телефона для связи — на него переводят оплату за работу.",
    )
    bank_name = models.CharField(
        "Банк для перевода",
        max_length=255,
        blank=True,
        default="",
        help_text="Например: Сбер, Тинькофф, Альфа.",
    )
    locality = models.CharField("Населённый пункт", max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=ContractorStatus.choices,
        default=ContractorStatus.PENDING_REVIEW,
    )
    admin_note = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Исполнитель"
        verbose_name_plural = "Исполнители"
        ordering = ["equipment_type", "user_id"]

    def __str__(self) -> str:
        return f"{self.get_equipment_type_display()}: {self.user}"

    @property
    def display_name(self) -> str:
        return str(self.user)


class CampaignAssignment(models.Model):
    """Назначение исполнителя на сервисную задачу."""

    campaign = models.ForeignKey(
        ServiceCampaign,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    contractor = models.ForeignKey(
        ContractorProfile,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    equipment_type = models.CharField(
        max_length=32,
        choices=EquipmentType.choices,
    )
    status = models.CharField(
        max_length=32,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.OFFERED,
    )
    scheduled_at = models.DateTimeField(
        "Назначенное время",
        null=True,
        blank=True,
        help_text="Время выезда / работ для этого исполнителя.",
    )
    proposed_at = models.DateTimeField(
        "Предложенное исполнителем время",
        null=True,
        blank=True,
    )
    counter_deadline = models.DateTimeField(
        "Дедлайн ответа на другое время",
        null=True,
        blank=True,
        help_text="20 минут с момента предложения другого времени.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    admin_comment = models.TextField(blank=True, default="")
    offered_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    residents_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Назначение исполнителя"
        verbose_name_plural = "Назначения исполнителей"
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "contractor"],
                name="uniq_campaign_contractor_assignment",
            )
        ]

    def __str__(self) -> str:
        return f"{self.contractor} → {self.campaign_id} ({self.status})"


class ContractorPayout(models.Model):
    """Чек перевода денег администратором исполнителю по закрытию работ."""

    campaign = models.ForeignKey(
        ServiceCampaign,
        on_delete=models.CASCADE,
        related_name="contractor_payouts",
    )
    assignment = models.ForeignKey(
        CampaignAssignment,
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    contractor = models.ForeignKey(
        ContractorProfile,
        on_delete=models.CASCADE,
        related_name="payouts",
    )
    amount = models.DecimalField("Сумма перевода, ₽", max_digits=12, decimal_places=2)
    receipt_image = models.FileField(
        "Чек перевода",
        upload_to="contractor_payouts/%Y/%m/",
    )
    bank_name = models.CharField("Банк", max_length=255, blank=True, default="")
    payout_phone = models.CharField(
        "Телефон получателя",
        max_length=32,
        blank=True,
        default="",
    )
    comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    residents_notified_at = models.DateTimeField(null=True, blank=True)
    contractor_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Оплата исполнителю"
        verbose_name_plural = "Оплаты исполнителям"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "assignment"],
                name="uniq_campaign_assignment_payout",
            )
        ]

    def __str__(self) -> str:
        return f"Выплата {self.amount} ₽ → {self.contractor} ({self.campaign_id})"


class ServiceReceipt(models.Model):
    invite = models.ForeignKey(
        ServiceInvite, on_delete=models.CASCADE, related_name="receipts"
    )
    campaign = models.ForeignKey(
        ServiceCampaign, on_delete=models.CASCADE, related_name="receipts"
    )
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="service_receipts")
    image = models.FileField("Файл чека", upload_to="service_receipts/%Y/%m/", blank=True)
    ocr_text = models.TextField(blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    transfer_date = models.DateField(null=True, blank=True)
    recipient_phone = models.CharField(max_length=64, blank=True, default="")
    recipient_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=ReceiptStatus.choices,
        default=ReceiptStatus.PENDING,
    )
    ai_notes = models.TextField(blank=True, default="")
    details_match = models.BooleanField(default=False)
    admin_comment = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Чек сервисного сбора"
        verbose_name_plural = "Чеки сервисных сборов"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Сервис-чек #{self.pk} {self.amount or '?'}₽ ({self.status})"


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


class AdminTask(models.Model):
    """Unified admin inbox item («Задачи на сегодня»)."""

    kind = models.CharField(max_length=32, choices=AdminTaskKind.choices)
    status = models.CharField(
        max_length=16,
        choices=AdminTaskStatus.choices,
        default=AdminTaskStatus.OPEN,
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    user = models.ForeignKey(
        BotUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_tasks",
    )
    priority = models.PositiveSmallIntegerField(default=50)
    due_at = models.DateTimeField(null=True, blank=True)
    action_url = models.CharField(max_length=512, blank=True, default="")
    source_model = models.CharField(max_length=64, blank=True, default="")
    source_id = models.PositiveIntegerField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Админ-задача"
        verbose_name_plural = "Админ-задачи"
        ordering = ["priority", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "source_model", "source_id"],
                name="uniq_admin_task_source",
                condition=models.Q(source_id__isnull=False)
                & ~models.Q(source_model=""),
            )
        ]
        indexes = [
            models.Index(fields=["status", "priority", "created_at"]),
            models.Index(fields=["kind", "status"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_kind_display()}] {self.title}"

    def mark_done(self) -> None:
        self.status = AdminTaskStatus.DONE
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])

    def dismiss(self) -> None:
        self.status = AdminTaskStatus.DISMISSED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])


class Reminder(models.Model):
    user = models.ForeignKey(BotUser, on_delete=models.CASCADE, related_name="reminders")
    text = models.TextField("Текст")
    due_at = models.DateTimeField("Когда напомнить")
    repeat = models.CharField(
        "Повтор",
        max_length=16,
        choices=ReminderRepeat.choices,
        default=ReminderRepeat.NONE,
    )
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
        suffix = " (ежедневно)" if self.repeat == ReminderRepeat.DAILY else ""
        return f"{self.text[:60]} @ {self.due_at}{suffix}"


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
    """Stores last user text and short clarification dialogs (e.g. reminder time)."""

    user = models.OneToOneField(BotUser, on_delete=models.CASCADE, related_name="pending")
    last_user_text = models.TextField(blank=True, default="")
    pending_kind = models.CharField(max_length=64, blank=True, default="")
    pending_payload = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ожидающее действие"
        verbose_name_plural = "Ожидающие действия"

    def clear_pending(self) -> None:
        self.pending_kind = ""
        self.pending_payload = {}
        self.save(update_fields=["pending_kind", "pending_payload", "updated_at"])


class AiUsageKind(models.TextChoices):
    LLM = "llm", "YandexGPT"
    STT = "stt", "SpeechKit"
    OCR = "ocr", "OCR"


class AiUsageLog(models.Model):
    """Estimated Yandex AI usage for subscription finance dashboard."""

    kind = models.CharField(max_length=16, choices=AiUsageKind.choices)
    model_name = models.CharField(max_length=128, blank=True, default="")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    units = models.PositiveIntegerField(
        default=1,
        help_text="Запросы STT / страницы OCR / иные единицы.",
    )
    estimated_cost_rub = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0")
    )
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Расход Yandex AI"
        verbose_name_plural = "Расходы Yandex AI"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.estimated_cost_rub}₽ @ {self.created_at:%Y-%m-%d}"


class YandexBillingEntry(models.Model):
    """Manual fact from Yandex Cloud billing for net-profit calculation."""

    for_date = models.DateField("Дата расхода")
    amount_rub = models.DecimalField("Сумма, ₽", max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Факт расходов Yandex"
        verbose_name_plural = "Факты расходов Yandex"
        ordering = ["-for_date", "-id"]

    def __str__(self) -> str:
        return f"{self.for_date}: {self.amount_rub} ₽"


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
