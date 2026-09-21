from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase

from database.models import BotUser, NeighborhoodWish, ServiceGroup, WishTopic
from services.wishes import capture_wish, detect_topic, looks_like_wish, topic_stats


class WishHelpersTests(TestCase):
    def test_detect_topics(self):
        self.assertEqual(detect_topic("Почините дорогу и ямы"), WishTopic.ROAD)
        self.assertEqual(detect_topic("Нужна детская площадка"), WishTopic.PLAYGROUND)
        self.assertEqual(detect_topic("Собаки без намордников"), WishTopic.DOGS)
        self.assertEqual(detect_topic("Поставьте фонари"), WishTopic.LIGHTING)

    def test_looks_like_wish(self):
        self.assertTrue(looks_like_wish("Хочу чтобы во дворе починили дорогу"))
        self.assertTrue(looks_like_wish("Пожелание: детская площадка"))
        self.assertTrue(looks_like_wish("Собаки без намордников бегают"))
        self.assertFalse(looks_like_wish("привет"))


class WishCaptureTests(TestCase):
    def setUp(self) -> None:
        self.user = BotUser.objects.create(
            max_user_id="w1",
            real_name="Анна",
            phone="89001112233",
            address="ул. Лесная 1",
            locality="Посёлок",
            profile_status="verified",
        )
        self.group = ServiceGroup.objects.create(name="ул. Лесная")
        self.group.members.add(self.user)
        self.admin = User.objects.create_user("adm", password="pass")
        self.client = Client()
        self.client.login(username="adm", password="pass")

    def test_capture_and_stats(self):
        capture_wish(self.user, "Хочу чтобы починили дорогу", group=self.group)
        capture_wish(self.user, "Собаки без намордников", group=self.group)
        capture_wish(self.user, "Пожелание: ещё одна яма на дороге", group=self.group)
        stats = {s["topic"]: s["count"] for s in topic_stats(self.group)}
        self.assertEqual(stats[WishTopic.ROAD], 2)
        self.assertEqual(stats[WishTopic.DOGS], 1)

    def test_pipeline_saves_wish(self):
        from bot.pipeline import MessagePipeline

        reply = MessagePipeline().handle(
            self.user, "Хочу чтобы во дворе сделали детскую площадку"
        )
        self.assertIn("Принято", reply)
        self.assertIn("площадк", reply.lower())
        self.assertEqual(NeighborhoodWish.objects.filter(user=self.user).count(), 1)
        wish = NeighborhoodWish.objects.get(user=self.user)
        self.assertEqual(wish.topic, WishTopic.PLAYGROUND)
        self.assertEqual(wish.group_id, self.group.id)

    def test_list_wishes_command(self):
        from bot.pipeline import MessagePipeline

        capture_wish(self.user, "Собаки без намордников", group=self.group)
        reply = MessagePipeline().handle(self.user, "пожелания")
        self.assertIn("Собаки", reply)
        self.assertIn(self.group.name, reply)

    def test_panel_group_shows_stats(self):
        capture_wish(self.user, "Нужно освещение у подъезда", group=self.group)
        resp = self.client.get(f"/panel/services/groups/{self.group.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Освещение")
        self.assertContains(resp, "Пожелания группы")

    def test_services_home_shows_overview(self):
        capture_wish(self.user, "Почините дорогу", group=self.group)
        resp = self.client.get("/panel/services/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Пожелания жителей по темам")
        self.assertContains(resp, "Дороги")
