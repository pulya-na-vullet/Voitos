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
    seconds = (dt - now).total_seconds()
    return max(1, int((seconds + 86399) // 86400)) if seconds > 0 else 0


def subscription_days_remaining(user: BotUser) -> int:
    user.ensure_grace_period()
    now = timezone.now()
    until = user.effective_subscription_until()
    if until and until > now:
        return _days_until(until)
    if user.grace_until and user.grace_until > now:
        return _days_until(user.grace_until)
    return 0


def subscription_short_line(user: BotUser) -> str:
    """Короткий статус для шапки «помощь»."""
    user.ensure_grace_period()
    state = user.access_state()
    days = subscription_days_remaining(user)
    until = user.effective_subscription_until()
    if state == AccessState.ACTIVE and until:
        until_s = timezone.localtime(until).strftime("%d.%m.%Y")
        payer = user.subscription_paid_by()
        if payer:
            return f"Подписка до {until_s} ({days} дн., оплатил {payer})."
        return f"Подписка до {until_s} ({days} дн.)."
    if state == AccessState.GRACE:
        until_s = (
            timezone.localtime(user.grace_until).strftime("%d.%m.%Y")
            if user.grace_until
            else "скоро"
        )
        if not user.subscription_until and not user.subscription_paid_by():
            return f"Пробный период до {until_s} ({days} дн.)."
        return f"Ждём оплату до {until_s} ({days} дн.)."
    return "Доступ закрыт — нужна оплата. Напишите «подписка»."


def _admin_comments_block(user: BotUser, limit: int = 5) -> str:
    receipts = (
        PaymentReceipt.objects.filter(user=user)
        .exclude(admin_comment="")
        .order_by("-reviewed_at", "-created_at")[:limit]
    )
    if not receipts:
        return "Комментариев пока нет."
    lines = []
    for r in receipts:
        when = r.reviewed_at or r.created_at
        when_s = timezone.localtime(when).strftime("%d.%m.%Y") if when else "—"
        status = r.get_status_display()
        lines.append(f"• {when_s} ({status}): {r.admin_comment.strip()}")
    return "\n".join(lines)


def subscription_detail_message(user: BotUser) -> str:
    """Команда «подписка»: статус и куда платить."""
    user.ensure_grace_period()
    cfg = AppSettings.load()
    state = user.access_state()
    days = subscription_days_remaining(user)
    grace_days = cfg.grace_days or 14
    price = cfg.subscription_price_rub

    if state == AccessState.ACTIVE:
        until_dt = user.effective_subscription_until()
        until = (
            timezone.localtime(until_dt).strftime("%d.%m.%Y")
            if until_dt
            else "—"
        )
        payer = user.subscription_paid_by()
        paid = f"\nОплатил: {payer}" if payer else ""
        status_block = f"Статус: активна\nДо: {until} ({days} дн.){paid}"
    elif state == AccessState.GRACE:
        grace = (
            timezone.localtime(user.grace_until).strftime("%d.%m.%Y")
            if user.grace_until
            else "—"
        )
        if not user.subscription_until and not user.subscription_paid_by():
            status_block = (
                f"Статус: пробный период\n"
                f"До: {grace} ({days} дн.)\n"
                f"Пока можно пользоваться ботом. Потом нужна подписка."
            )
        else:
            status_block = (
                f"Статус: ждём оплату\n"
                f"Оплатить до: {grace} ({days} дн.)\n"
                f"Пока бот ещё работает."
            )
    else:
        status_block = (
            "Статус: доступ закрыт\n"
            "Пришлите чек об оплате — после проверки доступ откроется."
        )

    pending = PaymentReceipt.objects.filter(
        user=user, status=ReceiptStatus.PENDING
    ).count()
    pending_line = (
        f"Чеков на проверке: {pending}" if pending else "Чеков на проверке: нет"
    )

    phone = (cfg.payment_phone or "—").strip() or "—"
    name = (cfg.payment_name or "—").strip() or "—"

    return (
        "Подписка Voitos\n"
        "────────────\n"
        f"{status_block}\n"
        f"{pending_line}\n\n"
        f"Стоимость: {price} ₽ / месяц\n"
        f"Куда переводить: {phone}\n"
        f"Получатель: {name}\n"
        f"Новым — {grace_days} дн. бесплатно, после подписки ещё {grace_days} дн. на оплату.\n"
        "Пришлите фото или PDF чека в этот чат.\n\n"
        "Комментарии по чекам\n"
        f"{_admin_comments_block(user)}\n\n"
        "Список команд — «помощь»."
    )


def help_message(user: BotUser) -> str:
    """Короткая визитка бота."""
    cfg = AppSettings.load()
    grace_days = cfg.grace_days or 14
    return (
        "Привет! Это Voitos.\n\n"
        "Помощник двора: задачи, напоминания, сборы соседей и вызов мастера.\n\n"
        f"Новым — {grace_days} дн. бесплатно.\n"
        f"{subscription_short_line(user)}\n\n"
        "Что умею\n"
        "• Анкета — «регистрация»\n"
        "• Подписка и оплата — «подписка»\n"
        "• Сборы двора — «сборы»\n"
        "• Вызвать мастера — «вызвать мастера» или «нужен …» + фото\n"
        "• Отменить заявку — «отменить заявку» (если ищем мастера слишком долго)\n"
        "• Стать мастером — «стать исполнителем»\n"
        "• Пожелания двора — напишите идею или «пожелания»\n"
        "• Память — «запомни …» / «что ты помнишь …»\n"
        "• Задачи — «нужно …», «мои задачи»\n"
        "• Напоминания — «напомни …», «мои напоминания»\n"
        "• Голос — пришлите голосовое\n\n"
        "Команды: помощь · подписка · сборы · регистрация · "
        "стать исполнителем · мои задачи · мои напоминания"
    )
