from __future__ import annotations

import base64
import io
import json

from django.test import Client, TestCase
from PIL import Image

from decimal import Decimal

from api.models import MobileAuthToken
from database.models import (
    BotUser,
    CampaignStatus,
    GroupChatMessage,
    InviteStatus,
    ServiceCampaign,
    ServiceCategory,
    ServiceGroup,
    ServiceInvite,
)
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

        poll = self.client.get(
            f"/api/v1/groups/{self.g1.id}/messages",
            **self._auth(),
        )
        self.assertEqual(poll.status_code, 200)
        texts = [m["text"] for m in poll.json()["items"]]
        self.assertIn("Привет соседям", texts)

    def test_messages_kept_when_author_deleted(self):
        msg = GroupChatMessage.objects.create(
            group=self.g1, author=self.other, text="Я ещё здесь"
        )
        other_id = self.other.id
        self.other.delete()
        msg.refresh_from_db()
        self.assertIsNone(msg.author_id)
        self.assertEqual(msg.text, "Я ещё здесь")
        self.assertFalse(BotUser.objects.filter(pk=other_id).exists())

        poll = self.client.get(
            f"/api/v1/groups/{self.g1.id}/messages",
            **self._auth(),
        )
        self.assertEqual(poll.status_code, 200)
        row = next(m for m in poll.json()["items"] if m["id"] == msg.id)
        self.assertEqual(row["text"], "Я ещё здесь")
        self.assertEqual(row["author_name"], "Удалённый пользователь")
        self.assertIsNone(row["author_id"])

    def test_messages_include_collection_payment_dots(self):
        empty = self.client.get(f"/api/v1/groups/{self.g1.id}/messages", **self._auth())
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["active_collections"], [])
        self.assertEqual(empty.json()["author_paid"], {})

        c1 = ServiceCampaign.objects.create(
            category=ServiceCategory.SNOW,
            title="Снег",
            group=self.g1,
            total_amount=Decimal("1000"),
            amount_per_user=Decimal("500"),
            status=CampaignStatus.ACTIVE,
        )
        c2 = ServiceCampaign.objects.create(
            category=ServiceCategory.ROAD,
            title="Дорога",
            group=self.g1,
            total_amount=Decimal("2000"),
            amount_per_user=Decimal("1000"),
            status=CampaignStatus.ACTIVE,
        )
        ServiceInvite.objects.create(
            campaign=c1,
            user=self.user,
            amount_due=Decimal("500"),
            amount_paid=Decimal("500"),
            status=InviteStatus.PAID,
        )
        ServiceInvite.objects.create(
            campaign=c1,
            user=self.other,
            amount_due=Decimal("500"),
            status=InviteStatus.OFFERED,
        )
        ServiceInvite.objects.create(
            campaign=c2,
            user=self.user,
            amount_due=Decimal("1000"),
            status=InviteStatus.OFFERED,
        )
        ServiceInvite.objects.create(
            campaign=c2,
            user=self.other,
            amount_due=Decimal("1000"),
            amount_paid=Decimal("1000"),
            status=InviteStatus.PAID,
        )
        GroupChatMessage.objects.create(group=self.g1, author=self.user, text="я оплатил снег")

        poll = self.client.get(f"/api/v1/groups/{self.g1.id}/messages", **self._auth())
        self.assertEqual(poll.status_code, 200)
        body = poll.json()
        titles = [c["title"] for c in body["active_collections"]]
        self.assertEqual(titles, ["Снег", "Дорога"])
        self.assertEqual(body["author_paid"][str(self.user.id)], [True, False])
        self.assertEqual(body["author_paid"][str(self.other.id)], [False, True])

        c1.status = CampaignStatus.CLOSED
        c1.save(update_fields=["status"])
        after_close = self.client.get(
            f"/api/v1/groups/{self.g1.id}/messages", **self._auth()
        ).json()
        self.assertEqual(
            [c["title"] for c in after_close["active_collections"]],
            ["Дорога"],
        )
        self.assertEqual(after_close["author_paid"][str(self.user.id)], [False])
        self.assertEqual(after_close["author_paid"][str(self.other.id)], [True])

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

