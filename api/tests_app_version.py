from django.test import Client, TestCase, override_settings

from database.models import AppSettings
from services.app_version import _normalize_apk_url, mobile_version_payload


@override_settings(
    MOBILE_OTP_DEBUG=True,
    MOBILE_MIN_VERSION_CODE=31,
    MOBILE_LATEST_VERSION_CODE=31,
    MOBILE_LATEST_VERSION_NAME="0.2.27-kmp",
    VOITOS_BACKEND_VERSION="0.2.27",
    MOBILE_APK_URL=(
        "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
        "cursor/app-update-fix-e31c/dist/apk/voitos-debug.apk"
    ),
)
class AppVersionHealthTests(TestCase):
    def setUp(self):
        self.client = Client()
        cfg = AppSettings.load()
        cfg.mobile_min_version_code = 10
        cfg.mobile_latest_version_code = 10
        cfg.mobile_latest_version_name = "0.2.5-kmp"
        cfg.mobile_apk_url = (
            "https://github.com/pulya-na-vullet/Voitos/raw/"
            "cursor/old-branch-e31c/dist/apk/voitos-debug.apk"
        )
        cfg.save()

    def test_health_includes_version_and_update_flag(self):
        old = self.client.get("/api/v1/health?version_code=30")
        self.assertEqual(old.status_code, 200)
        data = old.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["backend_version"], "0.2.27")
        self.assertEqual(data["min_app_version_code"], 31)
        self.assertTrue(data["update_required"])
        # Deploy newer than panel → settings APK URL (normalized).
        self.assertIn("raw.githubusercontent.com", data["apk_url"])
        self.assertIn("app-update-fix-e31c", data["apk_url"])

        cur = self.client.get("/api/v1/health?version_code=31")
        self.assertFalse(cur.json()["update_required"])

        # Заголовок X-Voitos-App-Version тоже учитывается.
        via_hdr = self.client.get(
            "/api/v1/health",
            HTTP_X_VOITOS_APP_VERSION="30",
        )
        self.assertTrue(via_hdr.json()["update_required"])
        ok_hdr = self.client.get(
            "/api/v1/health",
            HTTP_X_VOITOS_APP_VERSION="31",
        )
        self.assertFalse(ok_hdr.json()["update_required"])

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
            data=__import__("json").dumps({"phone": "89625501111", "pin": "1234", "version_code": 30}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 426)
        ok = self.client.post(
            "/api/v1/auth/pin/login",
            data=__import__("json").dumps({"phone": "89625501111", "pin": "1234", "version_code": 31}),
            content_type="application/json",
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["access_token"])

    def test_normalize_github_raw_url(self):
        src = (
            "https://github.com/pulya-na-vullet/Voitos/raw/"
            "cursor/foo/dist/apk/voitos-debug.apk"
        )
        self.assertEqual(
            _normalize_apk_url(src),
            "https://raw.githubusercontent.com/pulya-na-vullet/Voitos/"
            "cursor/foo/dist/apk/voitos-debug.apk",
        )

    def test_stale_panel_url_yields_to_newer_settings(self):
        payload = mobile_version_payload()
        self.assertEqual(payload["latest_app_version_code"], 31)
        self.assertIn("app-update-fix-e31c", payload["apk_url"])
        self.assertTrue(payload["apk_url"].startswith("https://raw.githubusercontent.com/"))
