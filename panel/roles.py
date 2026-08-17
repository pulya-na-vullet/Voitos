"""Ролевая модель панели: администратор и менеджер групп."""

from __future__ import annotations

import re
import secrets
import string
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from database.models import BotUser, PanelProfile, PanelRole, ServiceGroup

User = get_user_model()

# Разделы, доступные менеджеру (по url_name приложения panel).
MANAGER_ALLOWED_URL_NAMES = frozenset(
    {
        "logout",
        "admin_tasks_today",
        "admin_tasks_feed",
        "admin_task_done",
        "admin_task_dismiss",
        "admin_address_scan",
        "users",
        "dashboard",
        "user_dashboard",
        "user_profile_verify",
        "user_family_link",
        "user_messages",
        "user_memories",
        "user_tasks",
        "user_reminders",
        "user_logs",
        "delete_memory",
        "delete_task",
        "delete_reminder",
        "delete_message",
        "services",
        "services_archive",
        "services_wishes",
        "services_groups",
        "service_group_edit",
        "services_category",
        "service_campaign_create",
        "service_campaign_detail",
        "service_receipt_approve",
        "service_receipt_reject",
        "services_ranking",
        "clients_map",
        "work_requests",
        "work_request_detail",
    }
)

# Исполнители / заявки / роли / лог менеджеров — только администратор.


def ensure_panel_profile(user: AbstractBaseUser) -> PanelProfile | None:
    """Создаёт профиль панели при необходимости. Superuser → администратор."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.panel_profile  # type: ignore[attr-defined]
    except PanelProfile.DoesNotExist:
        role = (
            PanelRole.ADMIN
            if (getattr(user, "is_superuser", False) or getattr(user, "is_staff", False))
            else PanelRole.MANAGER
        )
        return PanelProfile.objects.create(user=user, role=role)


def is_panel_admin(user: AbstractBaseUser | None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    profile = ensure_panel_profile(user)
    return bool(profile and profile.is_admin)


def is_panel_manager(user: AbstractBaseUser | None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if is_panel_admin(user):
        return False
    profile = ensure_panel_profile(user)
    return bool(profile and profile.is_manager)


def manager_group_ids(user: AbstractBaseUser) -> list[int]:
    if is_panel_admin(user):
        return list(ServiceGroup.objects.values_list("id", flat=True))
    return list(
        ServiceGroup.objects.filter(manager=user).values_list("id", flat=True)
    )


def manager_groups_qs(user: AbstractBaseUser) -> QuerySet[ServiceGroup]:
    if is_panel_admin(user):
        return ServiceGroup.objects.all()
    return ServiceGroup.objects.filter(manager=user)


def scoped_bot_user_ids(user: AbstractBaseUser) -> set[int] | None:
    """None = без ограничения (админ). Иначе id жителей закреплённых групп."""
    if is_panel_admin(user):
        return None
    ids = set(
        BotUser.objects.filter(service_groups__manager=user)
        .values_list("id", flat=True)
        .distinct()
    )
    return ids


def scoped_bot_users_qs(user: AbstractBaseUser) -> QuerySet[BotUser]:
    ids = scoped_bot_user_ids(user)
    if ids is None:
        return BotUser.objects.all()
    return BotUser.objects.filter(id__in=ids)


def can_access_bot_user(user: AbstractBaseUser, bot_user: BotUser) -> bool:
    ids = scoped_bot_user_ids(user)
    if ids is None:
        return True
    return int(bot_user.id) in ids


def can_access_group(user: AbstractBaseUser, group: ServiceGroup) -> bool:
    if is_panel_admin(user):
        return True
    return group.manager_id == user.id


def panel_home_url_name(user: AbstractBaseUser) -> str:
    return "panel:admin_tasks_today" if is_panel_manager(user) else "panel:users"


def suggest_manager_username(bot_user: BotUser) -> str:
    phone_digits = re.sub(r"\D+", "", bot_user.phone or "")
    if len(phone_digits) >= 10:
        base = f"mgr{phone_digits[-10:]}"
    else:
        base = f"mgr{bot_user.id}"
    candidate = base
    n = 1
    while User.objects.filter(username=candidate).exists():
        # Если логин занят другим пользователем — добавить суффикс.
        existing = User.objects.filter(username=candidate).first()
        profile = getattr(existing, "panel_profile", None) if existing else None
        if profile and profile.bot_user_id == bot_user.id:
            return candidate
        n += 1
        candidate = f"{base}_{n}"
    return candidate


def generate_temp_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_or_create_manager_account(
    bot_user: BotUser,
    *,
    password: str | None = None,
) -> tuple[AbstractBaseUser, PanelProfile, str | None]:
    """
    Учётная запись менеджера для жителя бота.
    Возвращает (user, profile, plaintext_password_or_None если пароль не меняли).
    """
    existing_profile = (
        PanelProfile.objects.select_related("user")
        .filter(bot_user=bot_user, role=PanelRole.MANAGER)
        .first()
    )
    if existing_profile:
        plain = None
        if password:
            existing_profile.user.set_password(password)
            existing_profile.user.save(update_fields=["password"])
            plain = password
        return existing_profile.user, existing_profile, plain

    username = suggest_manager_username(bot_user)
    plain = password or generate_temp_password()
    user = User.objects.create_user(
        username=username,
        password=plain,
        is_staff=False,
        is_superuser=False,
    )
    # Имя для удобства в админке Django
    display = (bot_user.real_name or bot_user.display_name or "").strip()
    if display:
        parts = display.split(None, 1)
        user.first_name = parts[0][:150]
        if len(parts) > 1:
            user.last_name = parts[1][:150]
        user.save(update_fields=["first_name", "last_name"])
    profile = PanelProfile.objects.create(
        user=user,
        role=PanelRole.MANAGER,
        bot_user=bot_user,
    )
    return user, profile, plain


def assign_group_manager(
    group: ServiceGroup,
    bot_user: BotUser,
    *,
    password: str | None = None,
    ensure_password: bool = True,
) -> tuple[AbstractBaseUser, str]:
    """
    Назначить менеджера группы — любого жителя из системы.
    Один менеджер на группу; один менеджер может вести несколько групп.

    Всегда возвращает plaintext-пароль (для отправки в MAX): если пароль
    не задали и учётка уже была — генерируем новый и сбрасываем.
    """
    user, _profile, plain = get_or_create_manager_account(bot_user, password=password)
    if not plain and ensure_password:
        plain = generate_temp_password()
        user.set_password(plain)
        user.save(update_fields=["password"])
    if not plain:
        raise ValueError("Не удалось подготовить пароль менеджера")
    group.manager = user
    group.save(update_fields=["manager", "updated_at"])
    return user, plain


def manager_credentials_max_message(
    *,
    username: str,
    password: str,
    group_name: str,
    login_url: str | None = None,
    access_hint: str | None = None,
) -> str:
    """Текст для MAX: доступы к панели менеджера с полным URL."""
    from panel.network import panel_access_hint, panel_login_url

    url = (login_url or panel_login_url()).strip()
    hint = access_hint if access_hint is not None else panel_access_hint()
    lines = [
        "Вам назначена роль менеджера в панели Voitos.",
        f"Группа: {group_name}",
        "",
        "Данные для входа в веб-панель:",
        f"Логин: {username}",
        f"Пароль: {password}",
        "",
        f"URL: {url}",
    ]
    if hint:
        lines.append(hint)
    lines.extend(
        [
            "",
            "Смените пароль после первого входа, если передавали его другим людям.",
        ]
    )
    return "\n".join(lines)


def clear_group_manager(group: ServiceGroup) -> None:
    group.manager = None
    group.save(update_fields=["manager", "updated_at"])


def admin_required(view_func):
    """Только администратор панели."""

    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ensure_panel_profile(request.user)
        if not is_panel_admin(request.user):
            messages.error(request, "Раздел доступен только администратору.")
            return redirect(panel_home_url_name(request.user))
        return view_func(request, *args, **kwargs)

    return _wrapped


def admin_or_manager_required(view_func):
    """Администратор или менеджер панели."""

    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        ensure_panel_profile(request.user)
        if not (is_panel_admin(request.user) or is_panel_manager(request.user)):
            messages.error(request, "Недостаточно прав.")
            return redirect(panel_home_url_name(request.user))
        return view_func(request, *args, **kwargs)

    return _wrapped


def manager_url_allowed(url_name: str | None) -> bool:
    return bool(url_name) and url_name in MANAGER_ALLOWED_URL_NAMES


def filter_tasks_for_user(qs: QuerySet, user: AbstractBaseUser) -> QuerySet:
    ids = scoped_bot_user_ids(user)
    if ids is None:
        return qs
    # Задачи без пользователя (системные) менеджеру не показываем;
    # сервис-чеки и пр. — только по жителям его групп.
    return qs.filter(user_id__in=ids)
