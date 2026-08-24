"""Tractor client notice + role helpers."""

from django.test import TestCase

from database.models import ExecutorRole
from services.executor_roles import (
    TRACTOR_MIN_HOURS_NOTICE,
    role_client_notice,
    role_is_tractor,
)


class TractorClientNoticeTests(TestCase):
    def test_tractor_code(self):
        role = ExecutorRole.objects.create(
            code="tractor", name="Тракторист", is_active=True
        )
        self.assertTrue(role_is_tractor(role))
        self.assertEqual(role_client_notice(role), TRACTOR_MIN_HOURS_NOTICE)

    def test_non_tractor(self):
        role = ExecutorRole.objects.create(
            code="plumber", name="Сантехник", is_active=True
        )
        self.assertFalse(role_is_tractor(role))
        self.assertEqual(role_client_notice(role), "")
