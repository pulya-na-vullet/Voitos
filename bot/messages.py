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
    until = user.effective_subscription_until()
    if until and until > now:
        return _days_until(until)
    if user.grace_until and user.grace_until > now:
        return _days_until(user.grace_until)
    return 0


def subscription_short_line(user: BotUser) -> str:
    """One-line status for the help message footer/header."""
    user.ensure_grace_period()
    state = user.access_state()
    days = subscription_days_remaining(user)
    until = user.effective_subscription_until()
    if state == AccessState.ACTIVE and until:
        until_s = timezone.localtime(until).strftime("%d.%m.%Y")
        payer = user.subscription_paid_by()
        if payer:
            return (
                f"Подписка активна (оплачена {payer}): осталось {days} дн. "
                f"(до {until_s})."
            )
        return f"Подписка активна: осталось {days} дн. (до {until_s})."
    if state == AccessState.GRACE:
        until = (
            timezone.localtime(user.grace_until).strftime("%d.%m.%Y")
            if user.grace_until
            else "скоро"
        )
        # Новый пользователь без оплаты — пробный период; иначе льготный после подписки.
        if not user.subscription_until and not user.subscription_paid_by():
            return (
                f"Пробный период: осталось {days} дн. (до {until}). "
                f"Подробнее: «подписка»."
            )
        return (
            f"Подписка истекла — ждём оплату: осталось {days} дн. "
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
    grace_days = cfg.grace_days or 14

    if state == AccessState.ACTIVE:
        until_dt = user.effective_subscription_until()
        until = (
            timezone.localtime(until_dt).strftime("%d.%m.%Y %H:%M")
            if until_dt
            else "—"
        )
        payer = user.subscription_paid_by()
        paid_line = f"\nОплачена: {payer}" if payer else ""
        status_block = (
            f"Статус: активна\n"
            f"Осталось дней: {days}\n"
            f"Действует до: {until}"
            f"{paid_line}"
        )
    elif state == AccessState.GRACE:
        grace = (
            timezone.localtime(user.grace_until).strftime("%d.%m.%Y %H:%M")
            if user.grace_until
            else "—"
        )
        if not user.subscription_until and not user.subscription_paid_by():
            status_block = (
                f"Статус: пробный период\n"
                f"Осталось дней: {days}\n"
                f"Пробный доступ до: {grace}\n"
                f"Базовые функции доступны. Чтобы продолжить — оформите подписку."
            )
        else:
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
        f"• Срок: сумма ÷ {cfg.subscription_price_rub} ₽ = месяцы, "
        f"остаток переводится в дни (месяц = 30 дней)\n"
        f"• Пробный период для новых пользователей: {grace_days} дн.\n"
        f"• После истечения подписки — ещё {grace_days} дн. на оплату, "
        f"затем доступ закрывается\n"
        "• Пришлите фото, скрин или PDF чека прямо в этот чат\n"
        "• Администратор принимает или отклоняет чек — вам придёт сообщение\n\n"
        "Комментарии администратора\n"
        f"{_admin_comments_block(user)}\n\n"
        f"{payment_help_text()}\n\n"
        "Список возможностей бота — команда «описание» или «помощь»."
    )


def help_message(user: BotUser) -> str:
    """Friendly bot card: purpose + available features."""
    cfg = AppSettings.load()
    grace_days = cfg.grace_days or 14
    return (
        "Привет! Это Voitos.\n\n"
        "Личный ассистент и помощник двора: запоминает важное для вас "
        "(задачи, напоминания, факты) и помогает соседям решать общие дела у дома. "
        "Через самозанятого можно собирать деньги и организовывать работы — "
        "прозрачно, без лишней бюрократии.\n\n"
        f"Новым пользователям доступен пробный период {grace_days} дн.\n"
        f"{subscription_short_line(user)}\n\n"
        "Что умеет бот\n"
        "• Анкета — «регистрация»: имя, телефон и адрес, чтобы знать, "
        "кто из какого двора\n"
        "• Семейная подписка — если живёте вместе, оплату можно оформить "
        "на одного человека, доступ будет и у остальных членов семьи\n"
        "• Подписка на бота — перевод самозанятому и фото/PDF чека в чат; "
        "подробности: «подписка»\n"
        "• Сборы на работы у дома — снег, двор, свет, дорога и другие "
        "мероприятия: смотрите «сборы», оплачивайте чеком по предложению\n"
        "• Исполнители — владельцы трактора-погрузчика или камаза: "
        "«регистрация техники» / «я тракторист» / «я на камазе», "
        "чтобы получать заказы на чистку и вывоз снега\n"
        "• Пожелания двора — просто напишите, что важно соседям "
        "(«Хочу чтобы починили дорогу», «Собаки без намордников»). "
        "Итог по темам: «пожелания»\n"
        "• Память — напишите факт («Купила холодильник Bosch») или "
        "скажите «Запомни это» / «Не запоминай»\n"
        "• Поиск по памяти — «Что ты помнишь про холодильник?» / "
        "«Что ты помнишь обо мне?»\n"
        "• Задачи — «Нужно купить подарок маме», «Что мне нужно сделать?», "
        "«Выполнил подарок», «Удали задачу …»\n"
        "• Напоминания — «Напомни завтра в 10:00 …», "
        "«Каждое утро напоминай: Доброе утро!», «Мои напоминания»\n"
        "• Голос — отправьте голосовое, разберу и отвечу\n\n"
        "Коротко по командам\n"
        "• описание / помощь — эта визитка\n"
        "• подписка — статус, срок и куда переводить\n"
        "• сборы — текущие работы и сколько уже собрали\n"
        "• пожелания — статистика идей вашей группы по темам\n"
        "• регистрация — заполнить или обновить анкету\n"
        "• регистрация техники — стать исполнителем (трактор / камаз)\n"
        "• мои задачи / мои напоминания — ваши списки\n"
    )
