from django.test import Client, TestCase, override_settings

from database.models import AppSettings


@override_settings(MOBILE_OTP_DEBUG=True)
class AppVersionHealthTests(TestCase):
    def setUp(self):
        self.client = Client()
        cfg = AppSettings.load()
        cfg.mobile_min_version_code = 14
        cfg.mobile_latest_version_code = 14
        cfg.mobile_latest_version_name = "0.2.10-kmp"
        cfg.save()

    def test_health_includes_version_and_update_flag(self):
        old = self.client.get("/api/v1/health?version_code=13")
        self.assertEqual(old.status_code, 200)
        data = old.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["min_app_version_code"], 14)
        self.assertTrue(data["update_required"])

        cur = self.client.get("/api/v1/health?version_code=14")
        self.assertFalse(cur.json()["update_required"])
