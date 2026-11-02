"""Tests for work requests and executor roles."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from django.utils import timezone

from database.models import (
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
    PanelActionLog,
    PanelProfile,
    PanelRole,
    PendingAction,
    ServiceGroup,
    WorkRequest,
)
from bot.contractor_registration import (
    handle_contractor_registration_step,
    start_contractor_registration,
)
from bot.work_request import handle_work_request_photo, handle_work_request_step, start_work_request
from panel.manager_log import log_manager_action
from panel.roles import assign_group_manager
from services.executor_roles import apply_flags_to_role, format_roles_list, match_role_from_text

User = get_user_model()


class ExecutorRolesPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("adm", "a@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.client = Client()
        self.client.login(username="adm", password="pass")

    def test_roles_page_empty_and_create_without_code(self):
        resp = self.client.get(reverse("panel:executor_roles"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ролей пока нет")
        self.assertContains(resp, "roles-search")
        self.assertNotContains(resp, "Порядок в списке")
        self.assertNotContains(resp, "Код (латиница)")
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {
                "action": "create",
                "name": "Сантехник",
                "requires_qualification_docs": "on",
                "requires_work_photos": "on",
                "flags_json": '[{"code":"x2","label":"нужен допуск","on":true}]',
            },
        )
        self.assertEqual(resp.status_code, 302)
        role = ExecutorRole.objects.get(name="Сантехник")
        self.assertTrue(role.code.startswith("r_"))
        self.assertTrue(role.requires_qualification_docs)
        self.assertTrue(role.requires_work_photos)
        page = self.client.get(reverse("panel:executor_roles"))
        self.assertContains(page, "нужны подтверждающие документы")
        self.assertContains(page, "нужны фото при записи заявки")
        self.assertContains(page, 'class="role-fold"')
        self.assertContains(page, "Сантехник")
        labels = {f["label"] for f in role.flags}
        self.assertIn("нужен допуск", labels)
        self.assertIn("нужны подтверждающие документы", labels)

    def test_create_role_without_work_photos(self):
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {
                "action": "create",
                "name": "Мастер маникюра",
                "flags_json": "[]",
            },
        )
        self.assertEqual(resp.status_code, 302)
        role = ExecutorRole.objects.get(name="Мастер маникюра")
        self.assertFalse(role.requires_work_photos)
        page = self.client.get(reverse("panel:executor_roles"))
        self.assertContains(page, "без фото")

    def test_toggle_requires_work_photos_on_save(self):
        role = ExecutorRole.objects.create(
            code="r_nails", name="Ногти", requires_work_photos=True
        )
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {
                "action": "save",
                "role_id": str(role.id),
                "name": role.name,
                "is_active": "on",
                "flags_json": "[]",
            },
        )
        self.assertEqual(resp.status_code, 302)
        role.refresh_from_db()
        self.assertFalse(role.requires_work_photos)
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {
                "action": "save",
                "role_id": str(role.id),
                "name": role.name,
                "is_active": "on",
                "requires_work_photos": "on",
                "flags_json": "[]",
            },
        )
        self.assertEqual(resp.status_code, 302)
        role.refresh_from_db()
        self.assertTrue(role.requires_work_photos)

    def test_create_starts_without_flags_and_can_remove(self):
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {"action": "create", "name": "Грузчик", "flags_json": "[]"},
        )
        self.assertEqual(resp.status_code, 302)
        role = ExecutorRole.objects.get(name="Грузчик")
        self.assertEqual(role.flags, [])
        apply_flags_to_role(
            role,
            [
                {"code": "is_equipment", "label": "техника (госномер)", "on": True},
                {"code": "for_road", "label": "дорога", "on": True},
            ],
        )
        role.save()
        resp = self.client.post(
            reverse("panel:executor_roles"),
            {
                "action": "save",
                "role_id": str(role.id),
                "name": role.name,
                "is_active": "on",
                "is_equipment": "on",
                "flags_json": '[{"code":"for_road","label":"дорога","on":true}]',
            },
        )
        self.assertEqual(resp.status_code, 302)
        role.refresh_from_db()
        self.assertTrue(role.is_equipment)
        self.assertFalse(role.requires_qualification_docs)
        self.assertTrue(role.for_road)


class ContractorRegistrationByNumberTests(TestCase):
    def setUp(self):
        self.r1 = ExecutorRole.objects.create(
            code="r_aaa111bbb222", name="Тракторист", is_equipment=True, is_active=True
        )
        self.r2 = ExecutorRole.objects.create(
            code="r_ccc333ddd444", name="Сантехник", is_active=True
        )
        self.r3 = ExecutorRole.objects.create(
            code="r_eee555fff666", name="Скрытая", is_active=False
        )
        self.user = BotUser.objects.create(max_user_id="cr1", real_name="Иван", phone="89625507832")
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)

    def test_list_shows_only_active_numbered(self):
        text = format_roles_list()
        self.assertIn("1. Тракторист", text)
        self.assertIn("2. Сантехник", text)
        self.assertNotIn("Скрытая", text)

    def test_match_by_number(self):
        self.assertEqual(match_role_from_text("1").id, self.r1.id)
        self.assertEqual(match_role_from_text("2.").id, self.r2.id)
        self.assertEqual(match_role_from_text("тракторист").id, self.r1.id)
        self.assertIsNone(match_role_from_text("9"))

    def test_registration_picks_role_by_digit(self):
        msg = start_contractor_registration(self.user, self.pending)
        self.assertIn("Напишите номер", msg)
        self.assertIn("1. Тракторист", msg)
        self.assertIn("2. Сантехник", msg)
        msg = handle_contractor_registration_step(self.user, "2", self.pending)
        self.assertIn("Сантехник", msg)
        self.assertEqual(self.pending.pending_payload.get("role_id"), self.r2.id)


class WorkRequestBotTests(TestCase):
    def setUp(self):
        role = ExecutorRole.objects.create(
            code="electrician",
            name="Электрик",
            requires_qualification_docs=True,
        )
        apply_flags_to_role(
            role,
            [
                {
                    "code": "requires_qualification_docs",
                    "label": "нужны подтверждающие документы",
                    "on": True,
                }
            ],
        )
        role.save()
        self.user = BotUser.objects.create(max_user_id="wr1", real_name="Аня")
        self.pending, _ = PendingAction.objects.get_or_create(user=self.user)
        master = BotUser.objects.create(max_user_id="wr-m1", real_name="Мастер")
        ContractorProfile.objects.create(
            user=master,
            role=role,
            equipment_type=role.code,
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )

    def test_work_request_flow(self):
        msg = start_work_request(self.user, self.pending, text="нужен электрик")
        self.assertIn("электрик", msg.lower())
        msg = handle_work_request_step(self.user, "Починить розетку на кухне", self.pending)
        self.assertIn("фото", msg.lower())
        msg = handle_work_request_photo(
            self.user,
            self.pending,
            image_bytes=b"fakepng",
            filename="a.jpg",
        )
        self.assertIn("добавлено", msg.lower())
        msg = handle_work_request_step(self.user, "готово", self.pending)
        self.assertIn("отправлена", msg.lower())
        self.assertEqual(WorkRequest.objects.filter(user=self.user).count(), 1)
        req = WorkRequest.objects.get(user=self.user)
        self.assertEqual(req.role.code, "electrician")
        self.assertEqual(req.photos.count(), 1)

    def test_work_request_without_photos_when_role_allows(self):
        nails = ExecutorRole.objects.create(
            code="nails",
            name="Мастер маникюра",
            requires_work_photos=False,
        )
        nails_master = BotUser.objects.create(max_user_id="wr-m2", real_name="Нейл-мастер")
        ContractorProfile.objects.create(
            user=nails_master,
            role=nails,
            equipment_type=nails.code,
            status=ContractorStatus.VERIFIED,
            verified_at=timezone.now(),
        )
        msg = start_work_request(self.user, self.pending, role=nails)
        self.assertIn("маникюра", msg.lower())
        msg = handle_work_request_step(
            self.user, "Хочу покрыть гель-лаком", self.pending
        )
        self.assertIn("отправлена", msg.lower())
        self.assertNotIn("фото", msg.lower())
        req = WorkRequest.objects.get(user=self.user)
        self.assertEqual(req.role.code, "nails")
        self.assertEqual(req.photos.count(), 0)
        self.assertIn(req.status, {"pending", "offering"})


class ManagerLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("adm2", "b@t.com", "pass")
        PanelProfile.objects.create(user=self.admin, role=PanelRole.ADMIN)
        self.bot = BotUser.objects.create(max_user_id="ml1", real_name="Менеджер")
        self.g = ServiceGroup.objects.create(name="Г1")
        self.g.members.add(self.bot)
        self.mgr, _ = assign_group_manager(self.g, self.bot, password="MgrPass1!")
        self.client = Client()

    def test_log_only_for_manager_and_admin_sees(self):
        log_manager_action(self.mgr, action="test", title="Тест действие")
        self.assertEqual(PanelActionLog.objects.filter(actor=self.mgr).count(), 1)
        # admin action not logged as manager
        log_manager_action(self.admin, action="test", title="Админ")
        self.assertEqual(PanelActionLog.objects.filter(actor=self.admin).count(), 0)

        self.client.login(username="adm2", password="pass")
        resp = self.client.get(reverse("panel:manager_logs"))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("panel:manager_log_detail", args=[self.mgr.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Тест действие")

        self.client.logout()
        self.client.login(username=self.mgr.username, password="MgrPass1!")
        resp = self.client.get(reverse("panel:manager_logs"))
        self.assertEqual(resp.status_code, 302)
