from __future__ import annotations

import logging

from django.utils import timezone

from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    PaymentReceipt,
    ProfileStatus,
    ReceiptStatus,
    ServiceReceipt,
)

logger = logging.getLogger(__name__)


def upsert_task(
    *,
    kind: str,
    title: str,
    description: str = "",
    user: BotUser | None = None,
    action_url: str = "",
    source_model: str = "",
    source_id: int | None = None,
    priority: int = 50,
    meta: dict | None = None,
    due_at=None,
) -> AdminTask:
    defaults = {
        "title": title,
        "description": description,
        "user": user,
        "action_url": action_url,
        "priority": priority,
        "meta": meta or {},
        "due_at": due_at or timezone.now(),
        "status": AdminTaskStatus.OPEN,
        "completed_at": None,
    }
    if source_model and source_id is not None:
        task, created = AdminTask.objects.update_or_create(
            kind=kind,
            source_model=source_model,
            source_id=source_id,
            defaults=defaults,
        )
        if not created and task.status != AdminTaskStatus.OPEN:
            # Re-open if source became pending again
            task.status = AdminTaskStatus.OPEN
            task.completed_at = None
            task.title = title
            task.description = description
            task.action_url = action_url
            task.priority = priority
            task.meta = meta or {}
            task.user = user
            task.save()
        return task
    return AdminTask.objects.create(kind=kind, source_model=source_model, **defaults)


def close_task_for_source(kind: str, source_model: str, source_id: int) -> None:
    AdminTask.objects.filter(
        kind=kind,
        source_model=source_model,
        source_id=source_id,
        status=AdminTaskStatus.OPEN,
    ).update(status=AdminTaskStatus.DONE, completed_at=timezone.now())


def task_payment_receipt(receipt: PaymentReceipt) -> AdminTask | None:
    if receipt.status != ReceiptStatus.PENDING:
        close_task_for_source(
            AdminTaskKind.PAYMENT_RECEIPT, "PaymentReceipt", receipt.id
        )
        return None
    dupe_note = ""
    priority = 10
    try:
        from subscriptions.duplicates import find_identical_receipts

        twins = find_identical_receipts(receipt)
        if twins:
            ids = ", ".join(f"#{t.id}" for t in twins[:5])
            dupe_note = f" ВНИМАНИЕ: попиксельный дубль чека {ids}."
            priority = 3
    except Exception:
        logger.exception("Duplicate check failed for receipt #%s", receipt.id)
    return upsert_task(
        kind=AdminTaskKind.PAYMENT_RECEIPT,
        title=f"Чек подписки #{receipt.id}",
        description=(
            f"Сумма OCR: {receipt.amount or '—'} ₽. "
            f"Реквизиты: {'совпали' if receipt.details_match else 'сомнительно'}."
            f"{dupe_note}"
        ),
        user=receipt.user,
        action_url=f"/panel/receipts/?status=pending&find_dupes=1",
        source_model="PaymentReceipt",
        source_id=receipt.id,
        priority=priority,
        meta={"receipt_id": receipt.id, "duplicate": bool(dupe_note)},
    )


def task_service_receipt(receipt: ServiceReceipt) -> AdminTask | None:
    if receipt.status != ReceiptStatus.PENDING:
        close_task_for_source(
            AdminTaskKind.SERVICE_RECEIPT, "ServiceReceipt", receipt.id
        )
        return None
    return upsert_task(
        kind=AdminTaskKind.SERVICE_RECEIPT,
        title=f"Сервис-чек #{receipt.id}",
        description=(
            f"Сбор: {receipt.campaign.title}. "
            f"Сумма OCR: {receipt.amount or '—'} ₽."
        ),
        user=receipt.user,
        action_url=f"/panel/services/campaigns/{receipt.campaign_id}/",
        source_model="ServiceReceipt",
        source_id=receipt.id,
        priority=15,
        meta={"receipt_id": receipt.id, "campaign_id": receipt.campaign_id},
    )


def task_profile_review(user: BotUser) -> AdminTask | None:
    if user.profile_status != ProfileStatus.PENDING_REVIEW:
        close_task_for_source(AdminTaskKind.PROFILE_REVIEW, "BotUser", user.id)
        return None
    return upsert_task(
        kind=AdminTaskKind.PROFILE_REVIEW,
        title=f"Проверить анкету: {user.real_name or user}",
        description=(
            f"Адрес: {user.address or '—'}\n"
            f"Нас. пункт: {user.locality or '—'}\n"
            f"Телефон: {user.phone or '—'}"
        ),
        user=user,
        action_url=f"/panel/users/{user.id}/",
        source_model="BotUser",
        source_id=user.id,
        priority=20,
        meta={"profile_status": user.profile_status},
    )


def task_family_claim(
    user: BotUser,
    *,
    candidate_ids: list[int],
    claimed_family: bool,
    address: str,
) -> AdminTask:
    claim = "утверждает, что член семьи" if claimed_family else "отрицает родство"
    return upsert_task(
        kind=AdminTaskKind.FAMILY_CLAIM,
        title=f"Семейная заявка: {user.real_name or user}",
        description=(
            f"Адрес заявителя: {address}\n"
            f"Ответ пользователя: {claim}.\n"
            f"Возможные жильцы (id): {', '.join(str(i) for i in candidate_ids) or '—'}.\n"
            + (
                "Если подтвердите («Готово») — подписка будет продублирована "
                "с оплатившего члена семьи, и жильцы попадут в одну группу для сборов."
                if claimed_family
                else "Пользователь отрицает родство — «Готово» только закрывает задачу."
            )
        ),
        user=user,
        action_url=f"/panel/users/{user.id}/",
        source_model="FamilyClaim",
        source_id=user.id,
        priority=5,
        meta={
            "candidate_user_ids": candidate_ids,
            "claimed_family": claimed_family,
            "address": address,
        },
    )


def task_address_overlap(
    *,
    group_key: str,
    user_ids: list[int],
    addresses: list[str],
    reason: str,
    source_id: int | None = None,
) -> AdminTask:
    """group_key/source_id used for dedupe of overlap groups."""
    import hashlib

    if source_id is None:
        digest = hashlib.md5(group_key.encode("utf-8")).hexdigest()
        source_id = int(digest[:12], 16) % (10**9)
    names = list(
        BotUser.objects.filter(id__in=user_ids).values_list("real_name", "id")[:20]
    )
    people = ", ".join(f"{n or '—'} (#{i})" for n, i in names)
    return upsert_task(
        kind=AdminTaskKind.ADDRESS_OVERLAP,
        title=f"Совпадение адресов ({len(user_ids)} чел.)",
        description=(
            f"ИИ/эвристика: {reason}\n"
            f"Жители: {people}\n"
            f"Адреса:\n" + "\n".join(f"• {a}" for a in addresses[:10])
        ),
        user=BotUser.objects.filter(id__in=user_ids).order_by("id").first(),
        action_url=f"/panel/users/{sorted(user_ids)[0]}/" if user_ids else "/panel/",
        source_model="AddressOverlap",
        source_id=source_id,
        priority=25,
        meta={"user_ids": user_ids, "addresses": addresses, "reason": reason},
    )


def sync_admin_tasks() -> dict[str, int]:
    """Backfill open admin tasks from current pending sources."""
    counts = {
        "payment_receipts": 0,
        "service_receipts": 0,
        "profiles": 0,
        "closed": 0,
    }
    for receipt in PaymentReceipt.objects.filter(status=ReceiptStatus.PENDING):
        task_payment_receipt(receipt)
        counts["payment_receipts"] += 1
    for receipt in ServiceReceipt.objects.filter(status=ReceiptStatus.PENDING).select_related(
        "campaign", "user"
    ):
        task_service_receipt(receipt)
        counts["service_receipts"] += 1
    for user in BotUser.objects.filter(profile_status=ProfileStatus.PENDING_REVIEW):
        task_profile_review(user)
        counts["profiles"] += 1

    # Close stale source-backed tasks
    for task in AdminTask.objects.filter(
        status=AdminTaskStatus.OPEN,
        kind=AdminTaskKind.PAYMENT_RECEIPT,
        source_model="PaymentReceipt",
    ):
        if not PaymentReceipt.objects.filter(
            id=task.source_id, status=ReceiptStatus.PENDING
        ).exists():
            task.mark_done()
            counts["closed"] += 1
    for task in AdminTask.objects.filter(
        status=AdminTaskStatus.OPEN,
        kind=AdminTaskKind.SERVICE_RECEIPT,
        source_model="ServiceReceipt",
    ):
        if not ServiceReceipt.objects.filter(
            id=task.source_id, status=ReceiptStatus.PENDING
        ).exists():
            task.mark_done()
            counts["closed"] += 1
    for task in AdminTask.objects.filter(
        status=AdminTaskStatus.OPEN,
        kind=AdminTaskKind.PROFILE_REVIEW,
        source_model="BotUser",
    ):
        u = BotUser.objects.filter(id=task.source_id).first()
        if not u or u.profile_status != ProfileStatus.PENDING_REVIEW:
            task.mark_done()
            counts["closed"] += 1
    return counts


def list_today_tasks():
    """All open admin inbox tasks (live panel)."""
    return (
        AdminTask.objects.filter(status=AdminTaskStatus.OPEN)
        .select_related("user")
        .order_by("priority", "created_at")
    )


def tasks_fingerprint() -> str:
    from django.db.models import Count, Max

    agg = AdminTask.objects.filter(status=AdminTaskStatus.OPEN).aggregate(
        n=Count("id"),
        max_id=Max("id"),
        latest=Max("updated_at"),
    )
    latest = agg["latest"].isoformat() if agg["latest"] else "-"
    return f"{agg['n'] or 0}:{agg['max_id'] or 0}:{latest}"


def build_task_sections() -> tuple[list[dict], int, str]:
    from collections import defaultdict

    tasks = list(list_today_tasks())
    grouped: dict[str, list] = defaultdict(list)
    for t in tasks:
        grouped[t.kind].append(t)
    sections = []
    for kind, label in AdminTaskKind.choices:
        if kind in grouped:
            sections.append({"kind": kind, "label": label, "tasks": grouped[kind]})
    return sections, len(tasks), tasks_fingerprint()
