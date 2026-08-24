"""Регистрация техники: стаж + документы; карточка проверки в админке."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    ContractorProfile,
    ContractorStatus,
    ExecutorRole,
)
from services.executor_roles import role_docs_prompt, role_requires_docs


User = get_user_model()


class VehicleDocsAndExperienceTests(TestCase):
    def test_role_requires_docs_for_equipment(self):
        role = ExecutorRole.objects.create(
            code="tractor",
            name="Тракторист",
            is_active=True,
            is_equipment=True,
            requires_qualification_docs=False,
        )
        self.assertTrue(role_requires_docs(role))
        self.assertIn("прав", role_docs_prompt(role).lower())

    def test_api_registration_requires_doc_for_tractor(self):
        from bot.contractor_registration import submit_contractor_registration_api

        role = ExecutorRole.objects.create(
            code="tractor",
            name="Тракторист",
            is_active=True,
            is_equipment=True,
            requires_qualification_docs=False,
        )
        user = BotUser.objects.create(
            max_user_id="reg1", real_name="Дмитрий", phone="89625507832", locality="Куюки"
        )
        with self.assertRaises(ValueError) as ctx:
            submit_contractor_registration_api(
                user,
                {
                    "role_id": role.id,
                    "equipment_label": "МТЗ-82",
                    "plate_number": "A111AA",
                    "experience_text": "5 лет",
                    "phone": "89625507832",
                    "locality": "Куюки",
                    "bank_name": "Сбер",
                    "payout_phone": "89625507832",
                },
            )
        self.assertEqual(str(ctx.exception), "qualification_doc_required")

    def test_api_registration_saves_experience_and_task_url(self):
        import base64

        from bot.contractor_registration import submit_contractor_registration_api

        role = ExecutorRole.objects.create(
            code="tractor",
            name="Тракторист",
            is_active=True,
            is_equipment=True,
            requires_qualification_docs=True,
        )
        user = BotUser.objects.create(
            max_user_id="reg2", real_name="Дмитрий", phone="89625507832", locality="Куюки"
        )
        profile, _msg = submit_contractor_registration_api(
            user,
            {
                "role_id": role.id,
                "equipment_label": "volna 102f",
                "plate_number": "rto654",
                "experience_text": "7 лет",
                "phone": "89625507832",
                "locality": "Куюки",
                "bank_name": "Сбербанк",
                "payout_phone": "89625507832",
                "qual_base64": base64.b64encode(b"fake-image").decode("ascii"),
                "qual_filename": "rights.jpg",
            },
        )
        self.assertEqual(profile.experience_text, "7 лет")
        self.assertEqual(profile.plate_number, "rto654")
        self.assertTrue(bool(profile.qualification_doc))
        task = AdminTask.objects.get(
            kind=AdminTaskKind.CONTRACTOR_REVIEW,
            source_model="ContractorProfile",
            source_id=profile.id,
        )
        self.assertEqual(task.action_url, f"/panel/contractors/{profile.id}/")
        self.assertEqual(task.status, AdminTaskStatus.OPEN)


class ContractorDetailPanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@a.ru", "pass")
        self.client = Client()
        self.client.force_login(self.admin)
        self.role = ExecutorRole.objects.create(
            code="tractor", name="Тракторист", is_active=True, is_equipment=True
        )
        self.user = BotUser.objects.create(
            max_user_id="u1", real_name="Григорьев", locality="Куюки", phone="89625507832"
        )
        self.profile = ContractorProfile.objects.create(
            user=self.user,
            role=self.role,
            equipment_type="tractor",
            equipment_label="volna 102f",
            experience_text="5 лет",
            plate_number="rto654",
            phone="89625507832",
            payout_phone="89625507832",
            bank_name="Сбербанк",
            locality="Куюки",
            status=ContractorStatus.PENDING_REVIEW,
        )

    def test_detail_page_shows_application(self):
        resp = self.client.get(f"/panel/contractors/{self.profile.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "volna 102f")
        self.assertContains(resp, "5 лет")
        self.assertContains(resp, "rto654")
        self.assertContains(resp, "Член команды Voitos")

    def test_verify_with_voitos_flag(self):
        resp = self.client.post(
            f"/panel/contractors/{self.profile.id}/",
            {"action": "verify", "is_voitos_team": "1"},
        )
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, ContractorStatus.VERIFIED)
        self.assertTrue(self.profile.is_voitos_team)
