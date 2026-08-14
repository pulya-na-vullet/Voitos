"""Tests for panel LAN / public URL helpers."""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from panel.network import (
    WIFI_WORKSHOP_NOTE,
    is_local_lan_mode,
    panel_access_origin,
    panel_login_url,
)
from panel.roles import manager_credentials_max_message


class PanelNetworkTests(SimpleTestCase):
    @override_settings(HOST="0.0.0.0", PORT=18765, PANEL_PUBLIC_URL="")
    def test_local_lan_url_and_message(self):
        self.assertTrue(is_local_lan_mode())
        origin = panel_access_origin()
        self.assertTrue(origin.startswith("http://"))
        self.assertIn(":18765", origin)
        self.assertNotIn("0.0.0.0", origin)
        login = panel_login_url()
        self.assertTrue(login.endswith("/panel/login/"))

        text = manager_credentials_max_message(
            username="mgr1",
            password="secret",
            group_name="9 аллея",
        )
        self.assertIn(login, text)
        self.assertIn("URL:", text)
        self.assertIn(WIFI_WORKSHOP_NOTE, text)
        self.assertIn("Логин: mgr1", text)

    @override_settings(
        HOST="0.0.0.0",
        PORT=18765,
        PANEL_PUBLIC_URL="https://voitos.example.com",
    )
    def test_public_stand_url_and_message(self):
        self.assertFalse(is_local_lan_mode())
        self.assertEqual(panel_access_origin(), "https://voitos.example.com")
        self.assertEqual(
            panel_login_url(), "https://voitos.example.com/panel/login/"
        )
        text = manager_credentials_max_message(
            username="mgr1",
            password="secret",
            group_name="9 аллея",
        )
        self.assertIn("https://voitos.example.com/panel/login/", text)
        self.assertNotIn(WIFI_WORKSHOP_NOTE, text)

    @override_settings(HOST="192.168.1.50", PORT=18765, PANEL_PUBLIC_URL="")
    def test_explicit_bind_host_used(self):
        self.assertEqual(panel_access_origin(), "http://192.168.1.50:18765")
