from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase

from database.models import BotUser, ProfileStatus


class RankingLocalityDropdownTests(TestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create_user("adm", password="pass")
        self.client = Client()
        self.client.login(username="adm", password="pass")
        BotUser.objects.create(
            max_user_id="r1",
            real_name="Анна",
            locality="Куюки",
            profile_status=ProfileStatus.VERIFIED,
        )
        BotUser.objects.create(
            max_user_id="r2",
            real_name="Борис",
            locality="Казань",
            profile_status=ProfileStatus.VERIFIED,
        )

    def test_ranking_uses_select_from_db_not_datalist(self):
        resp = self.client.get("/panel/services/ranking/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<select name="locality"')
        self.assertContains(resp, "Куюки")
        self.assertContains(resp, "Казань")
        self.assertNotContains(resp, 'list="locs"')
        self.assertNotContains(resp, "<datalist")

    def test_ranking_filter_by_locality(self):
        resp = self.client.get("/panel/services/ranking/", {"locality": "Куюки"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Анна")
        self.assertNotContains(resp, "Борис")
        self.assertContains(resp, 'selected">Куюки</option>')
