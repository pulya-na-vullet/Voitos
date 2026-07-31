from __future__ import annotations

from django.test import SimpleTestCase

from bot.handler import _find_audio_url, _find_receipt_file
from subscriptions.receipts import guess_ocr_mime


def _msg(*attachments):
    return {"body": {"attachments": list(attachments), "text": ""}}


class ReceiptAttachmentRoutingTests(SimpleTestCase):
    def test_pdf_file_is_receipt_not_voice(self):
        message = _msg(
            {
                "type": "file",
                "payload": {
                    "url": "https://example.com/doc.pdf",
                    "filename": "document31.07.26 12_17_17.724.pdf",
                },
            }
        )
        url, name = _find_receipt_file(message)
        self.assertEqual(url, "https://example.com/doc.pdf")
        self.assertTrue(name.lower().endswith(".pdf"))
        self.assertIsNone(_find_audio_url(message))

    def test_pdf_with_name_field(self):
        message = _msg(
            {
                "type": "file",
                "payload": {
                    "url": "https://example.com/x",
                    "name": "cheque.pdf",
                },
            }
        )
        url, name = _find_receipt_file(message)
        self.assertEqual(url, "https://example.com/x")
        self.assertEqual(name, "cheque.pdf")
        self.assertIsNone(_find_audio_url(message))

    def test_image_still_receipt(self):
        message = _msg(
            {
                "type": "image",
                "payload": {"url": "https://example.com/r.jpg"},
            }
        )
        url, name = _find_receipt_file(message)
        self.assertEqual(url, "https://example.com/r.jpg")
        self.assertEqual(name, "receipt.jpg")

    def test_voice_file_still_audio(self):
        message = _msg(
            {
                "type": "file",
                "payload": {
                    "url": "https://example.com/voice.ogg",
                    "filename": "voice.ogg",
                },
            }
        )
        self.assertIsNone(_find_receipt_file(message)[0])
        self.assertEqual(_find_audio_url(message), "https://example.com/voice.ogg")

    def test_native_voice_type(self):
        message = _msg(
            {
                "type": "audio",
                "payload": {"url": "https://example.com/a.opus"},
            }
        )
        self.assertEqual(_find_audio_url(message), "https://example.com/a.opus")
        self.assertIsNone(_find_receipt_file(message)[0])

    def test_guess_ocr_mime_pdf(self):
        self.assertEqual(guess_ocr_mime(b"%PDF-1.4...", "cheque.pdf"), "PDF")
        self.assertEqual(guess_ocr_mime(b"\x89PNG\r\n\x1a\nxxxx", "a.png"), "PNG")
        self.assertEqual(guess_ocr_mime(b"\xff\xd8\xff", "a.jpg"), "JPEG")
