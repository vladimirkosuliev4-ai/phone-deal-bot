from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from bot.telegram import TelegramError
from repair_main import _notifier_from_environment, run
from scrapers.base import Listing
from tests.repair_helpers import repair_config_data
from utils.state import StateStore


class FakeScraper:
    def __init__(self) -> None:
        self.listings: list[Listing] = []
        self.enriched: list[str] = []

    def search(self, query: str, pages: int = 1) -> list[Listing]:
        return list(self.listings)

    def enrich(self, listing: Listing) -> Listing:
        self.enriched.append(listing.listing_id)
        return listing


class FakeNotifier:
    def __init__(self) -> None:
        self.notifications = []
        self.messages = []
        self.health_failures = []
        self.health_recoveries = []

    def send(self, notification) -> None:
        self.notifications.append(notification)

    def send_message(self, text: str, **kwargs) -> None:
        self.messages.append(text)

    def send_health_failure(self, marketplace: str, failures: int, error: str) -> None:
        self.health_failures.append((marketplace, failures, error))

    def send_health_recovery(self, marketplace: str) -> None:
        self.health_recoveries.append(marketplace)


def listing(listing_id: str, title: str, price: int, description: str | None = None) -> Listing:
    return Listing(
        marketplace="bazar",
        listing_id=listing_id,
        title=title,
        price_text=f"€{price}",
        price_amount=Decimal(price),
        currency="EUR",
        url=f"https://example.test/{listing_id}",
        location="София",
        image_url="https://example.test/image.jpg",
        description=description,
    )


class RepairMonitorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_path = root / "repair.json"
        self.state_path = root / "repair_state.json"
        self.config_path.write_text(
            json.dumps(repair_config_data(), ensure_ascii=False), encoding="utf-8"
        )
        self.scraper = FakeScraper()
        self.notifier = FakeNotifier()
        self.scraper.listings = [
            listing(f"w{i}", f"iPhone 14 Pro Max 256GB fully working {i}", price)
            for i, price in enumerate((390, 410, 430, 450, 470), 1)
        ]
        self.scraper.listings.extend(
            listing(f"b{i}", f"iPhone 14 Pro Max 256GB broken screen {i}", price)
            for i, price in enumerate((250, 270, 290), 1)
        )

    def execute(self, *, dry_run: bool = False, send_test: bool = False) -> int:
        return run(
            self.config_path,
            self.state_path,
            dry_run=dry_run,
            send_test=send_test,
            notifier=self.notifier,
            scrapers={"bazar": self.scraper},
        )

    def test_first_run_baselines_then_new_good_flip_alerts_once(self) -> None:
        self.assertEqual(0, self.execute())
        self.assertEqual([], self.notifier.notifications)

        self.scraper.listings.append(
            listing("new", "iPhone 14 Pro Max 256GB broken screen", 200)
        )
        self.assertEqual(0, self.execute())
        self.assertEqual(1, len(self.notifier.notifications))
        self.assertIn("GOOD FLIP", self.notifier.notifications[0].text)
        self.assertIn("Profit", self.notifier.notifications[0].text)

        self.assertEqual(0, self.execute())
        self.assertEqual(1, len(self.notifier.notifications))
        self.assertTrue(StateStore(self.state_path).is_seen("bazar", "new"))

    def test_fatal_lock_is_analyzed_as_skip_without_alert(self) -> None:
        self.execute()
        self.scraper.listings.append(
            listing(
                "locked",
                "iPhone 14 Pro Max 256GB broken screen",
                80,
                "iCloud locked and seller does not know the account",
            )
        )
        self.execute()
        self.assertEqual([], self.notifier.notifications)
        self.assertTrue(StateStore(self.state_path).is_seen("bazar", "locked"))

    def test_dry_run_and_setup_test_are_separate(self) -> None:
        self.assertEqual(0, self.execute(dry_run=True))
        self.assertFalse(self.state_path.exists())

        self.assertEqual(0, self.execute(send_test=True))
        self.assertEqual(1, len(self.notifier.messages))
        self.assertIn("Repair Flip Bot", self.notifier.messages[0])


class RepairTelegramCredentialTests(unittest.TestCase):
    def test_legacy_deal_bot_secrets_are_not_accepted(self) -> None:
        legacy_only = {
            "TELEGRAM_BOT_TOKEN": "ordinary-deal-bot-token",
            "TELEGRAM_CHAT_ID": "12345",
        }
        with patch.dict(os.environ, legacy_only, clear=True):
            with self.assertRaises(TelegramError) as raised:
                _notifier_from_environment(timeout=20)

        message = str(raised.exception)
        self.assertIn("REPAIR_TELEGRAM_BOT_TOKEN", message)
        self.assertIn("REPAIR_TELEGRAM_CHAT_ID", message)

    def test_repair_bot_uses_only_its_own_secrets(self) -> None:
        repair_secrets = {
            "REPAIR_TELEGRAM_BOT_TOKEN": "separate-repair-bot-token",
            "REPAIR_TELEGRAM_CHAT_ID": "67890",
        }
        with patch.dict(os.environ, repair_secrets, clear=True):
            notifier = _notifier_from_environment(timeout=17)

        self.assertEqual("67890", notifier.chat_id)
        self.assertEqual(17, notifier.timeout)
        self.assertTrue(notifier._base_url.endswith("botseparate-repair-bot-token"))


if __name__ == "__main__":
    unittest.main()
