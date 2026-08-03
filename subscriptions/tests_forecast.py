from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from database.models import AppSettings, BotUser, ServiceGroup
from subscriptions.family import link_family_members
from subscriptions.forecast import build_earnings_forecast, next_month_bounds


class EarningsForecastTests(TestCase):
    def setUp(self) -> None:
        cfg = AppSettings.load()
        cfg.subscription_price_rub = 100
        cfg.save()
        start, end, _ = next_month_bounds()
        mid_next = start + timedelta(days=10)

        self.dmitry = BotUser.objects.create(
            max_user_id="fc-dm",
            real_name="Дмитрий",
            subscription_until=mid_next,
        )
        self.elena = BotUser.objects.create(
            max_user_id="fc-el",
            real_name="Елена",
        )
        self.later = BotUser.objects.create(
            max_user_id="fc-lt",
            real_name="Позже",
            subscription_until=end + timedelta(days=40),
        )
        group = ServiceGroup.objects.create(name="9 аллея")
        group.members.add(self.dmitry, self.elena, self.later)
        link_family_members([self.elena, self.dmitry], payer=self.dmitry)

        self.admin = User.objects.create_user("fcadm", password="pass")
        self.client = Client()
        self.client.login(username="fcadm", password="pass")

    def test_forecast_counts_payers_and_next_month_renewals(self):
        forecast = build_earnings_forecast()
        # Дмитрий (семья) + Позже = 2 платящих; Елена не отдельная вершина
        self.assertEqual(forecast["payer_count"], 2)
        self.assertEqual(forecast["full_renewal_forecast"], Decimal("200.00"))
        # Только Дмитрий истекает в следующем месяце
        self.assertEqual(forecast["renew_count"], 1)
        self.assertEqual(forecast["next_month_renewal_forecast"], Decimal("100.00"))
        alley = next(g for g in forecast["groups"] if g["name"] == "9 аллея")
        self.assertEqual(alley["payers"], 2)
        self.assertEqual(alley["renew_next_month"], 1)

    def test_panel_page(self):
        resp = self.client.get("/panel/earnings-forecast/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Прогноз заработка", body)
        self.assertIn("Дмитрий", body)
        self.assertIn("продление", body)
