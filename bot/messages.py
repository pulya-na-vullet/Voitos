from __future__ import annotations

from django.utils import timezone

from database.models import AccessState, AppSettings, BotUser, PaymentReceipt, ReceiptStatus
from subscriptions.service import payment_help_text


def _days_until(dt) -> int:
    if not dt:
        return 0
    now = timezone.now()
    if dt <= now:
        return 0
    # Round up partial days so "23h left" still shows as 1 day
    seconds = (dt - now).total_seconds()
    return max(1, int((seconds + 86399) // 86400)) if seconds > 0 else 0


def subscription_days_remaining(user: BotUser) -> int:
    user.ensure_grace_period()
    now = timezone.now()
    if user.subscription_until and user.subscription_until > now:
        return _days_until(user.subscription_until)
    if user.grace_until and user.grace_until > now:
        return _days_until(user.grace_until)
    return 0


def subscription_short_line(user: BotUser) -> str:
    """One-line status for the help message footer/header."""
    user.ensure_grace_period()
    state = user.access_state()
    days = subscription_days_remaining(user)
    if state == AccessState.ACTIVE and user.subscription_until:
        until = timezone.localtime(user.subscription_until).strftime("%d.%m.%Y")
        return f"Подписка активна: осталось {days} дн. (до {until})."
    if state == AccessState.GRACE:
        until = (
            timezone.localtime(user.grace_until).strftime("%d.%m.%Y")
            if user.grace_until
            else "скоро"
        )
        return (
            f"Подписка истекла — ждём оплату: осталось {days} дн. льготного периода "
            f"(до {until}). Подробнее: «подписка»."
        )
    return "Доступ закрыт — нужна оплата. Подробнее: напишите «подписка»."


def _admin_comments_block(user: BotUser, limit: int = 5) -> str:
    receipts = (
        PaymentReceipt.objects.filter(user=user)
        .exclude(admin_comment="")
        .order_by("-reviewed_at", "-created_at")[:limit]
    )
    if not receipts:
        return "Комментариев администратора пока нет."
    lines = []
    for r in receipts:
        when = r.reviewed_at or r.created_at
        when_s = timezone.localtime(when).strftime("%d.%m.%Y") if when else "—"
        status = r.get_status_display()
        lines.append(f"• {when_s} ({status}): {r.admin_comment.strip()}")
    return "\n".join(lines)


def subscription_detail_message(user: BotUser) -> str:
    """Detailed subscription / payment conditions for the «подписка» command."""
    user.ensure_grace_period()
    cfg = AppSettings.load()
    state = user.access_state()
    days = subscription_days_remaining(user)

    if state == AccessState.ACTIVE:
        until = (
            timezone.localtime(user.subscription_until).strftime("%d.%m.%Y %H:%M")
            if user.subscription_until
            else "—"
        )
        status_block = (
            f"Статус: активна\n"
            f"Осталось дней: {days}\n"
            f"Действует до: {until}"
        )
    elif state == AccessState.GRACE:
        grace = (
            timezone.localtime(user.grace_until).strftime("%d.%m.%Y %H:%M")
            if user.grace_until
            else "—"
        )
        sub_until = (
            timezone.localtime(user.subscription_until).strftime("%d.%m.%Y %H:%M")
            if user.subscription_until
            else "не оформлена / истекла"
        )
        status_block = (
            f"Статус: ожидание оплаты (льготный период)\n"
            f"Осталось дней на оплату: {days}\n"
            f"Льготный период до: {grace}\n"
            f"Подписка была до: {sub_until}\n"
            f"Базовые функции пока доступны."
        )
    else:
        status_block = (
            f"Статус: доступ закрыт\n"
            f"Осталось дней: 0\n"
            f"Функции бота недоступны до принятия чека администратором."
        )

    pending = PaymentReceipt.objects.filter(user=user, status=ReceiptStatus.PENDING).count()
    pending_line = (
        f"Чеков на проверке: {pending}" if pending else "Чеков на проверке: нет"
    )

    return (
        "Подписка Voitos\n"
        "────────────\n"
        f"{status_block}\n"
        f"{pending_line}\n\n"
        "Условия пользования\n"
        f"• Стоимость: {cfg.subscription_price_rub} ₽ / месяц\n"
        f"• Перевод на номер: {cfg.payment_phone}\n"
        f"• Получатель: {cfg.payment_name}\n"
        f"• Если сумма больше {cfg.subscription_price_rub} ₽ — срок считается "
        f"как сумма ÷ {cfg.subscription_price_rub} (целые месяцы)\n"
        f"• После истечения — {cfg.grace_days} дн. на оплату, затем доступ закрывается\n"
        "• Пришлите фото или скрин чека прямо в этот чат\n"
        "• Администратор принимает или отклоняет чек — вам придёт сообщение\n\n"
        "Комментарии администратора\n"
        f"{_admin_comments_block(user)}\n\n"
        f"{payment_help_text()}\n\n"
        "Список возможностей бота — команда «помощь»."
    )


def help_message(user: BotUser) -> str:
    """Full feature hint + short subscription days line."""
    return (
        "Я Voitos — личный помощник в MAX.\n\n"
        f"{subscription_short_line(user)}\n\n"
        "Что умею\n"
        "• Память — напишите факт («Купила холодильник Bosch»). "
        "Явно: «Запомни это» / «Не запоминай».\n"
        "• Поиск памяти — «Что ты помнишь про холодильник?» / «Что ты помнишь обо мне?»\n"
        "• Задачи — «Нужно купить подарок маме», «Что мне нужно сделать?», "
        "«Выполнил подарок», «Удали задачу …»\n"
        "• Напоминания — «Напомни завтра в 10:00 …», "
        "«Каждое утро напоминай: Доброе утро!», «Мои напоминания»\n"
        "• Голос — отправьте голосовое, разберу и отвечу так же\n"
        "• Оплата — пришлите фото чека о переводе; детали: «подписка»\n\n"
        "Команды\n"
        "• помощь — эта подсказка\n"
        "• подписка — статус, дни, условия и комментарии администратора\n"
        "• мои задачи / мои напоминания — списки\n"
    )
