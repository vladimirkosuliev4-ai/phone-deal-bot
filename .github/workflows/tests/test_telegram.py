"""Offline Telegram formatting and transport tests."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

import requests

from bot.telegram import (
    Notification,
    TelegramError,
    TelegramNotifier,
    build_deal_notification,
)
from scrapers.base import Listing
from utils.config import MarketReference
from utils.filters import calculate_deal


class FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"ok": status_code < 400}

    def json(self) -> dict:
        return self._body


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None, error: Exception | None = None):
        self.responses = list(responses or [FakeResponse()])
        self.error = error
        self.calls: list[tuple[str, dict, int]] = []

    def post(self, url: str, *, data: dict, timeout: int) -> FakeResponse:
        self.calls.append((url, data, timeout))
        if self.error:
            raise self.error
        return self.responses.pop(0)


def example_listing(*, image_url: str | None = "https://img.example/phone.jpg") -> Listing:
    return Listing(
        marketplace="olx",
        listing_id="abc123",
        title='iPhone 14 Pro Max & accessories <new>',
        price_text="780 лв.",
        price_amount=Decimal("780"),
        currency="BGN",
        url='https://www.olx.bg/d/obyava/example?from="test"&x=1',
        location="София & област",
        image_url=image_url,
    )


class NotificationFormattingTests(unittest.TestCase):
    @staticmethod
    def market_reference(**overrides) -> MarketReference:
        values = {
            "median_price_eur": Decimal("490"),
            "sample_size": 45,
            "scope": "mixed storage",
            "as_of": "2026-08-25",
            "source": "OLX.bg cleaned asking-price sample",
            "resale_demand": "very high",
        }
        values.update(overrides)
        return MarketReference(**values)

    def test_builds_complete_safe_html_notification(self) -> None:
        deal = calculate_deal(Decimal("398.81"), Decimal("500"))
        self.assertIsNotNone(deal)
        notification = build_deal_notification(
            example_listing(), "iPhone 14 Pro Max", Decimal("500"), deal
        )

        self.assertEqual("deal:olx:abc123", notification.key)
        self.assertEqual("https://img.example/phone.jpg", notification.image_url)
        self.assertIn("GREAT DEAL", notification.text)
        self.assertIn("iPhone 14 Pro Max", notification.text)
        self.assertIn("780", notification.text)
        self.assertIn("398", notification.text)
        self.assertIn("OLX", notification.text)
        self.assertIn("София &amp; област", notification.text)
        self.assertIn("My maximum", notification.text)
        self.assertIn("below my limit", notification.text)
        self.assertIn("OPEN LISTING", notification.text)
        self.assertIn("&amp;x=1", notification.text)
        self.assertNotIn("<new>", notification.text)

    def test_notification_round_trip_for_pending_state(self) -> None:
        original = Notification("deal:bazar:1", "<b>deal</b>", "https://img.example/1.jpg")
        self.assertEqual(original, Notification.from_dict(original.to_dict()))

    def test_market_snapshot_compares_bgn_listing_below_median(self) -> None:
        deal = calculate_deal(Decimal("398.81"), Decimal("500"))
        assert deal is not None
        notification = build_deal_notification(
            example_listing(),
            "iPhone 15 Pro",
            Decimal("500"),
            deal,
            market_reference=self.market_reference(),
        )

        self.assertIn("Typical market asking price: <b>€490", notification.text)
        self.assertIn("<b>€91.19</b> below market (18.6%)", notification.text)
        self.assertIn("Estimated resale demand: <b>VERY HIGH</b>", notification.text)
        self.assertIn(
            "Market snapshot: OLX.bg cleaned asking-price sample; 45 ads; "
            "mixed storage; as of 2026-08-25",
            notification.text,
        )

    def test_market_snapshot_reports_above_and_equal_prices(self) -> None:
        above_listing = replace(
            example_listing(),
            price_text="€525",
            price_amount=Decimal("525"),
            currency="EUR",
        )
        deal = calculate_deal(Decimal("525"), Decimal("600"))
        assert deal is not None
        above = build_deal_notification(
            above_listing,
            "iPhone 15 Pro",
            Decimal("600"),
            deal,
            market_reference=self.market_reference(),
        )
        self.assertIn("<b>€35</b> above market (7.1%)", above.text)

        equal_listing = replace(
            example_listing(),
            price_text="€490",
            price_amount=Decimal("490"),
            currency="EUR",
        )
        equal_deal = calculate_deal(Decimal("490"), Decimal("600"))
        assert equal_deal is not None
        equal = build_deal_notification(
            equal_listing,
            "iPhone 15 Pro",
            Decimal("600"),
            equal_deal,
            market_reference=self.market_reference(),
        )
        self.assertIn("at the typical market asking price", equal.text)

    def test_market_snapshot_escapes_context_and_uses_singular_ad(self) -> None:
        listing_value = replace(
            example_listing(), price_amount=Decimal("450"), currency="EUR"
        )
        deal = calculate_deal(Decimal("450"), Decimal("500"))
        assert deal is not None
        notification = build_deal_notification(
            listing_value,
            "Phone",
            Decimal("500"),
            deal,
            market_reference=self.market_reference(
                sample_size=1,
                scope="128GB & unlocked",
                source="OLX <sample>",
            ),
        )
        self.assertIn("OLX &lt;sample&gt;; 1 ad; 128GB &amp; unlocked", notification.text)
        self.assertNotIn("OLX <sample>", notification.text)


class TelegramTransportTests(unittest.TestCase):
    def test_rejects_empty_secrets_without_network_access(self) -> None:
        with self.assertRaises(TelegramError):
            TelegramNotifier("", "chat")
        with self.assertRaises(TelegramError):
            TelegramNotifier("token", "   ")

    def test_image_notification_uses_send_photo(self) -> None:
        session = FakeSession()
        notifier = TelegramNotifier("secret-token", "123", session=session, timeout=7)
        notifier.send(Notification("key", "<b>deal</b>", "https://img.example/1.jpg"))

        self.assertEqual(1, len(session.calls))
        url, payload, timeout = session.calls[0]
        self.assertTrue(url.endswith("/sendPhoto"))
        self.assertNotIn("secret-token", repr(payload))
        self.assertEqual("123", payload["chat_id"])
        self.assertEqual("HTML", payload["parse_mode"])
        self.assertEqual(7, timeout)

    def test_failed_remote_image_falls_back_to_text_message(self) -> None:
        session = FakeSession(
            [
                FakeResponse(400, {"ok": False, "description": "failed to get HTTP URL"}),
                FakeResponse(200, {"ok": True}),
            ]
        )
        notifier = TelegramNotifier("token", "123", session=session)
        notifier.send(Notification("key", "deal text", "https://bad.example/image.jpg"))

        self.assertEqual(2, len(session.calls))
        self.assertTrue(session.calls[0][0].endswith("/sendPhoto"))
        self.assertTrue(session.calls[1][0].endswith("/sendMessage"))
        self.assertEqual("deal text", session.calls[1][1]["text"])

    def test_long_caption_skips_photo_and_sends_message(self) -> None:
        session = FakeSession()
        notifier = TelegramNotifier("token", "123", session=session)
        notifier.send(Notification("key", "x" * 1025, "https://img.example/1.jpg"))
        self.assertEqual(1, len(session.calls))
        self.assertTrue(session.calls[0][0].endswith("/sendMessage"))

    def test_api_and_network_failures_raise_clear_error(self) -> None:
        api_session = FakeSession([FakeResponse(401, {"ok": False, "description": "Unauthorized"})])
        with self.assertRaisesRegex(TelegramError, "Unauthorized"):
            TelegramNotifier("bad", "123", session=api_session).send_message("test")

        network_session = FakeSession(error=requests.ConnectionError("offline"))
        with self.assertRaisesRegex(TelegramError, "network error"):
            TelegramNotifier("token", "123", session=network_session).send_message("test")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
