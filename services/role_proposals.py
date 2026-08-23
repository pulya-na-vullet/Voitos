"""Заявки жителей на добавление новой роли исполнителя."""

from __future__ import annotations

import logging

from django.utils import timezone

from database.models import (
    ActivityKind,
    ActivityLog,
    AdminTaskKind,
    BotUser,
    ExecutorRole,
    ExecutorRoleProposal,
    ExecutorRoleProposalStatus,
)
from panel.admin_tasks import close_task_for_source, upsert_task
from services.executor_roles import generate_role_code

logger = logging.getLogger(__name__)

MIN_NAME_LEN = 2
MAX_NAME_LEN = 128


def _day_start():
    return timezone.localtime(timezone.now()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def user_proposed_today(user: BotUser) -> bool:
    start = _day_start()
    return ExecutorRoleProposal.objects.filter(
        user=user, created_at__gte=start
    ).exists()


def propose_role(user: BotUser, name: str) -> ExecutorRoleProposal:
    name = " ".join((name or "").strip().split())
    if len(name) < MIN_NAME_LEN:
        raise ValueError("Укажите название роли (минимум 2 символа).")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(f"Слишком длинное название (макс. {MAX_NAME_LEN}).")

    if user_proposed_today(user):
        raise ValueError(
            "Сегодня вы уже отправили заявку на новую роль. "
            "Можно предложить одну роль в сутки."
        )

    # Уже есть такая активная роль — не плодим дубли в задачах.
    existing = (
        ExecutorRole.objects.filter(is_active=True, name__iexact=name)
        .order_by("id")
        .first()
    )
    if existing is not None:
        raise ValueError(
            f"Роль «{existing.name}» уже есть в каталоге — выберите её в списке."
        )

    proposal = ExecutorRoleProposal.objects.create(
        user=user,
        proposed_name=name,
        status=ExecutorRoleProposalStatus.OPEN,
    )
    ActivityLog.objects.create(
        user=user,
        kind=ActivityKind.OTHER,
        title="Заявка на новую роль",
        detail=name,
        meta={"proposal_id": proposal.id},
    )
    upsert_task(
        kind=AdminTaskKind.ROLE_PROPOSAL,
        title=f"Новая роль: «{name}»",
        description=(
            f"{user.real_name or user}\n"
            f"Просит добавить роль: {name}"
        ),
        user=user,
        action_url=f"/panel/role-proposals/{proposal.id}/",
        source_model="ExecutorRoleProposal",
        source_id=proposal.id,
        priority=22,
        meta={"proposal_id": proposal.id, "proposed_name": name},
    )
    return proposal


def approve_proposal(
    proposal: ExecutorRoleProposal,
    *,
    admin_user=None,
    admin_note: str = "",
    accepts_at_home: bool = False,
    requires_work_photos: bool = True,
) -> ExecutorRole:
    if proposal.status != ExecutorRoleProposalStatus.OPEN:
        raise ValueError("Заявка уже рассмотрена.")

    name = (proposal.proposed_name or "").strip()[:MAX_NAME_LEN]
    if not name:
        raise ValueError("Пустое название роли.")

    role = (
        ExecutorRole.objects.filter(is_active=True, name__iexact=name)
        .order_by("id")
        .first()
    )
    if role is None:
        role = ExecutorRole.objects.create(
            code=generate_role_code(),
            name=name,
            is_active=True,
            sort_order=0,
            requires_work_photos=bool(requires_work_photos),
            accepts_at_home=bool(accepts_at_home),
        )
        flags = []
        if accepts_at_home:
            flags.append(
                {
                    "code": "accepts_at_home",
                    "label": "мастер принимает на дому",
                    "on": True,
                }
            )
        role.flags = flags
        role.save(update_fields=["flags", "updated_at"])
    else:
        # Подтянуть флаги, если роль уже была в каталоге.
        changed = []
        if role.accepts_at_home != bool(accepts_at_home):
            role.accepts_at_home = bool(accepts_at_home)
            changed.append("accepts_at_home")
        if role.requires_work_photos != bool(requires_work_photos):
            role.requires_work_photos = bool(requires_work_photos)
            changed.append("requires_work_photos")
        if changed:
            stored = role.flags if isinstance(role.flags, list) else []
            flags = [
                f
                for f in stored
                if isinstance(f, dict) and f.get("code") != "accepts_at_home"
            ]
            if accepts_at_home:
                flags.append(
                    {
                        "code": "accepts_at_home",
                        "label": "мастер принимает на дому",
                        "on": True,
                    }
                )
            role.flags = flags
            changed.append("flags")
            role.save(update_fields=changed)


    proposal.status = ExecutorRoleProposalStatus.APPROVED
    proposal.created_role = role
    proposal.admin_note = (admin_note or "").strip()
    proposal.reviewed_at = timezone.now()
    proposal.reviewed_by = admin_user
    proposal.save(
        update_fields=[
            "status",
            "created_role",
            "admin_note",
            "reviewed_at",
            "reviewed_by",
            "updated_at",
        ]
    )
    close_task_for_source(
        AdminTaskKind.ROLE_PROPOSAL, "ExecutorRoleProposal", proposal.id
    )

    try:
        from api.emit import emit_app_event

        emit_app_event(
            proposal.user,
            ntype="executor.role_approved",
            title="Роль добавлена",
            body=(
                f"Роль «{role.name}» появилась в каталоге. "
                f"Можете зарегистрироваться исполнителем."
            ),
            entity_type="ExecutorRole",
            entity_id=role.id,
        )
    except Exception:
        logger.exception("Notify role approval failed for proposal #%s", proposal.id)

    return role


def reject_proposal(
    proposal: ExecutorRoleProposal,
    *,
    admin_user=None,
    admin_note: str = "",
) -> ExecutorRoleProposal:
    if proposal.status != ExecutorRoleProposalStatus.OPEN:
        raise ValueError("Заявка уже рассмотрена.")

    proposal.status = ExecutorRoleProposalStatus.REJECTED
    proposal.admin_note = (admin_note or "").strip() or "Отклонено"
    proposal.reviewed_at = timezone.now()
    proposal.reviewed_by = admin_user
    proposal.save(
        update_fields=[
            "status",
            "admin_note",
            "reviewed_at",
            "reviewed_by",
            "updated_at",
        ]
    )
    close_task_for_source(
        AdminTaskKind.ROLE_PROPOSAL, "ExecutorRoleProposal", proposal.id
    )

    try:
        from api.emit import emit_app_event

        emit_app_event(
            proposal.user,
            ntype="executor.role_rejected",
            title="Заявка на роль отклонена",
            body=(
                f"Запрос на роль «{proposal.proposed_name}» отклонён. "
                f"{proposal.admin_note}"
            ).strip(),
            entity_type="ExecutorRoleProposal",
            entity_id=proposal.id,
        )
    except Exception:
        logger.exception("Notify role reject failed for proposal #%s", proposal.id)

    return proposal


def proposal_to_dict(proposal: ExecutorRoleProposal) -> dict:
    return {
        "id": proposal.id,
        "proposed_name": proposal.proposed_name,
        "status": proposal.status,
        "admin_note": proposal.admin_note or "",
        "created_role_id": proposal.created_role_id,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else "",
    }
