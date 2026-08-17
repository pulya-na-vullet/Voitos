from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from bot.client import MaxApiError, MaxClient


class MaxClientUploadTests(SimpleTestCase):
    def test_safe_upload_filename_strips_non_ascii(self):
        self.assertEqual(MaxClient._safe_upload_filename("фото дороги.jpg"), "photo.jpg")
        self.assertEqual(MaxClient._safe_upload_filename("дорога_ремонт.png"), "photo.png")
        self.assertEqual(MaxClient._safe_upload_filename("road_fix.png"), "road_fix.png")
        self.assertEqual(MaxClient._safe_upload_filename("task photo (1).JPG"), "task_photo_1.jpg")
        self.assertEqual(MaxClient._safe_upload_filename(""), "photo.jpg")

    def test_prepare_image_upload_normalizes_to_jpeg(self):
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGBA", (2, 2), (255, 0, 0, 128)).save(buf, format="PNG")
        data, name, content_type = MaxClient._prepare_image_upload(buf.getvalue(), "задача.png")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))
        self.assertEqual(name, "photo.jpg")
        self.assertEqual(content_type, "image/jpeg")

    @patch("bot.client.requests.post")
    def test_upload_image_uses_bare_multipart_without_auth(self, mock_post):
        client = MaxClient("test-token")
        client.get_upload_url = MagicMock(return_value={"url": "https://iu.oneme.ru/upload.do?x=1"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"photos":{"1":{"token":"tok-abc"}}}'
        mock_response.json.return_value = {"photos": {"1": {"token": "tok-abc"}}}
        mock_response.text = mock_response.content.decode()
        mock_post.return_value = mock_response

        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, format="PNG")
        token = client.upload_image(buf.getvalue(), "фото.jpg")
        self.assertEqual(token, "tok-abc")
        self.assertTrue(mock_post.called)
        kwargs = mock_post.call_args.kwargs
        headers = kwargs.get("headers") or {}
        self.assertNotIn("Authorization", headers)
        self.assertNotEqual(headers.get("Content-Type"), "application/json")
        files = kwargs.get("files") or {}
        self.assertIn("data", files)
        filename = files["data"][0]
        self.assertRegex(filename, r"^[A-Za-z0-9._-]+$")
        self.assertTrue(filename.endswith(".jpg"))

    @patch("bot.client.requests.post")
    def test_upload_image_rejects_cdn_error_body(self, mock_post):
        client = MaxClient("test-token")
        client.get_upload_url = MagicMock(return_value={"url": "https://iu.oneme.ru/upload.do?x=1"})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"error_code":"4","error_data":"BAD_REQUEST"}'
        mock_response.json.return_value = {"error_code": "4", "error_data": "BAD_REQUEST"}
        mock_response.text = mock_response.content.decode()
        mock_post.return_value = mock_response

        with self.assertRaises(MaxApiError) as ctx:
            client.upload_image(b"not-an-image", "bad.bin")
        self.assertIn("BAD_REQUEST", str(ctx.exception))


class MaxClientRateLimitTests(SimpleTestCase):
    @patch("bot.client.time.sleep")
    def test_retries_on_429_then_succeeds(self, mock_sleep):
        client = MaxClient("test-token")
        bad = MagicMock()
        bad.status_code = 429
        bad.text = '{"code":"too.many.requests"}'
        bad.headers = {"Retry-After": "1"}
        bad.content = bad.text.encode()
        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'{"ok":true}'
        ok.json.return_value = {"ok": True}
        ok.text = '{"ok":true}'
        client.session.request = MagicMock(side_effect=[bad, ok])

        data = client._request("GET", "/me", timeout=5, retries_429=2)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(client.session.request.call_count, 2)
        mock_sleep.assert_called()

    @patch("bot.client.time.sleep")
    def test_get_updates_uses_tuple_timeout(self, mock_sleep):
        client = MaxClient("test-token")
        ok = MagicMock()
        ok.status_code = 200
        ok.content = b'{"updates":[]}'
        ok.json.return_value = {"updates": []}
        ok.text = '{"updates":[]}'
        client.session.request = MagicMock(return_value=ok)

        client.get_updates(marker=1, timeout=30)
        kwargs = client.session.request.call_args.kwargs
        timeout = kwargs.get("timeout")
        self.assertIsInstance(timeout, tuple)
        self.assertEqual(timeout[0], 15)
        self.assertGreaterEqual(timeout[1], 75)
