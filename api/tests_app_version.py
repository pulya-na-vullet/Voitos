from django.test import Client, TestCase, override_settings

from database.models import AppSettings


@override_settings(
    MOBILE_OTP_DEBUG=True,
    MOBILE_MIN_VERSION_CODE=28,
    MOBILE_LATEST_VERSION_CODE=28,
    MOBILE_LATEST_VERSION_NAME="0.2.24-kmp",
    VOITOS_BACKEND_VERSION="0.2.24",
)
class AppVersionHealthTests(TestCase):
    def setUp(self):
        self.client = Client()
        cfg = AppSettings.load()
        cfg.mobile_min_version_code = 10
        cfg.mobile_latest_version_code = 10
        cfg.mobile_latest_version_name = "0.2.5-kmp"
        cfg.save()

    def test_health_includes_version_and_update_flag(self):
        old = self.client.get("/api/v1/health?version_code=27")
        self.assertEqual(old.status_code, 200)
        data = old.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["backend_version"], "0.2.24")
        self.assertEqual(data["min_app_version_code"], 28)
        self.assertTrue(data["update_required"])

        cur = self.client.get("/api/v1/health?version_code=28")
        self.assertFalse(cur.json()["update_required"])

    def test_pin_login_rejects_old_version(self):
        from django.contrib.auth.hashers import make_password

        from database.models import BotUser, ProfileStatus

        BotUser.objects.create(
            max_user_id="vu_pin",
            phone="89625501111",
            chat_id="c1",
            real_name="Pin",
            profile_status=ProfileStatus.VERIFIED,
            pin_hash=make_password("1234"),
        )
        resp = self.client.post(
            "/api/v1/auth/pin/login",
            data=__import__("json").dumps({"phone": "89625501111", "pin": "1234", "version_code": 27}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 426)
        ok = self.client.post(
            "/api/v1/auth/pin/login",
            data=__import__("json").dumps({"phone": "89625501111", "pin": "1234", "version_code": 28}),
            content_type="application/json",
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["access_token"])
