from django.test import Client, TestCase, override_settings

from database.models import AppSettings


@override_settings(
    MOBILE_OTP_DEBUG=True,
    MOBILE_MIN_VERSION_CODE=15,
    MOBILE_LATEST_VERSION_CODE=15,
    MOBILE_LATEST_VERSION_NAME="0.2.11-kmp",
)
class AppVersionHealthTests(TestCase):
    def setUp(self):
        self.client = Client()
        cfg = AppSettings.load()
        # Устаревшее значение в БД — пол берётся из settings (deploy).
        cfg.mobile_min_version_code = 10
        cfg.mobile_latest_version_code = 10
        cfg.mobile_latest_version_name = "0.2.5-kmp"
        cfg.save()

    def test_health_includes_version_and_update_flag(self):
        old = self.client.get("/api/v1/health?version_code=14")
        self.assertEqual(old.status_code, 200)
        data = old.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["min_app_version_code"], 15)
        self.assertTrue(data["update_required"])

        cur = self.client.get("/api/v1/health?version_code=15")
        self.assertFalse(cur.json()["update_required"])

    def test_panel_higher_min_wins_over_settings(self):
        cfg = AppSettings.load()
        cfg.mobile_min_version_code = 20
        cfg.mobile_latest_version_code = 20
        cfg.save()
        data = self.client.get("/api/v1/health?version_code=15").json()
        self.assertEqual(data["min_app_version_code"], 20)
        self.assertTrue(data["update_required"])
