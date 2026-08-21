from __future__ import annotations

import base64
import io
import json

from django.test import Client, TestCase
from PIL import Image

from api.models import MobileAuthToken
from database.models import BotUser, GroupChatMessage, ServiceGroup
from services.group_chat import process_avatar_bytes


class GroupChatAndAvatarTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="chat-u1",
            real_name="ЖительЧат",
            phone="9625507111",
            chat_id="c-chat-1",
        )
        self.other = BotUser.objects.create(
            max_user_id="chat-u2",
            real_name="Сосед",
            phone="9625507112",
            chat_id="c-chat-2",
        )
        self.g1 = ServiceGroup.objects.create(name="Двор А")
        self.g2 = ServiceGroup.objects.create(name="Двор Б")
        self.g1.members.add(self.user, self.other)
        self.g2.members.add(self.user)
        self.client = Client()
        self.tok = MobileAuthToken.objects.create(bot_user=self.user)

    def _auth(self) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tok.token}"}

    def test_groups_list(self):
        resp = self.client.get("/api/v1/groups", **self._auth())
        self.assertEqual(resp.status_code, 200)
        names = {i["name"] for i in resp.json()["items"]}
        self.assertEqual(names, {"Двор А", "Двор Б"})

    def test_post_and_poll_messages(self):
        resp = self.client.post(
            f"/api/v1/groups/{self.g1.id}/messages",
            data=json.dumps({"text": "Привет соседям"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 201)
        mid = resp.json()["message"]["id"]
        self.assertTrue(GroupChatMessage.objects.filter(pk=mid, group=self.g1).exists())

        listed = self.client.get(f"/api/v1/groups/{self.g1.id}/messages", **self._auth())
        self.assertEqual(listed.status_code, 200)
        texts = [m["text"] for m in listed.json()["items"]]
        self.assertIn("Привет соседям", texts)

        after = self.client.get(
            f"/api/v1/groups/{self.g1.id}/messages?after_id={mid}",
            **self._auth(),
        )
        self.assertEqual(after.json()["items"], [])

    def test_foreign_group_forbidden(self):
        g3 = ServiceGroup.objects.create(name="Чужая")
        resp = self.client.post(
            f"/api/v1/groups/{g3.id}/messages",
            data=json.dumps({"text": "хак"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 403)

    def test_avatar_resized_500(self):
        img = Image.new("RGB", (800, 600), color=(20, 120, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        processed = process_avatar_bytes(raw)
        out = Image.open(io.BytesIO(processed))
        self.assertEqual(out.size, (500, 500))

        b64 = base64.b64encode(raw).decode("ascii")
        resp = self.client.post(
            "/api/v1/me/avatar",
            data=json.dumps({"content_base64": b64, "filename": "a.png"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        self.assertTrue(body.get("avatar_url"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)

    def test_unread_badge_and_mark_read(self):
        other_tok = MobileAuthToken.objects.create(bot_user=self.other)
        # сосед пишет
        resp = self.client.post(
            f"/api/v1/groups/{self.g1.id}/messages",
            data=json.dumps({"text": "Привет от соседа, проверьте чат"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {other_tok.token}",
        )
        self.assertEqual(resp.status_code, 201)
        groups = self.client.get("/api/v1/groups", **self._auth())
        self.assertEqual(groups.status_code, 200)
        body = groups.json()
        self.assertGreaterEqual(body["unread_total"], 1)
        g1 = next(i for i in body["items"] if i["id"] == self.g1.id)
        self.assertGreaterEqual(g1["unread_count"], 1)

        # открытие чата помечает прочитанным
        listed = self.client.get(f"/api/v1/groups/{self.g1.id}/messages", **self._auth())
        self.assertEqual(listed.status_code, 200)
        groups2 = self.client.get("/api/v1/groups", **self._auth())
        self.assertEqual(groups2.json()["unread_total"], 0)

