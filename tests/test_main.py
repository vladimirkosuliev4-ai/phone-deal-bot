"""End-to-end orchestration tests using fake scrapers and Telegram transport."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from bot.telegram import TelegramError
from main import run
from scrapers.base import Listing
from utils.state import StateStore


class FakeScraper:
    def __init__(self, marketplace: str = "bazar") -> None:
        self.marketplace = marketplace
        self.listings: list[Listing] = []
        self.error: Exception | None = None
        self.search_calls: list[tuple[str, int]] = []
        self.enriched: list[str] = []

    def search(self, query: str, pages: int = 1) -> list[Listing]:
        self.search_calls.append((query, pages))
        if self.error:
            raise self.error
        return list(self.listings)

    def enrich(self, listing: Listing) -> Listing:
        self.enriched.append(listing.listing_id)
        return listing


class FakeNotifier:
    def __init__(self, *, fail_deals: int = 0) -> None:
        self.fail_deals = fail_deals
        self.notifications = []
        self.tests_sent = 0
        self.health_failures: list[tuple[str, int, str]] = []
        self.health_recoveries: list[str] = []

    def send(self, notification) -> None:
        if self.fail_deals:
            self.fail_deals -= 1
            raise TelegramError("temporary Telegram failure")
        self.notifications.append(notification)

    def send_test(self) -> None:
        self.tests_sent += 1

    def send_health_failure(self, marketplace: str, failures: int, error: str) -> None:
        self.health_failures.append((marketplace, failures, error))

    def send_health_recovery(self, marketplace: str) -> None:
        self.health_recoveries.append(marketplace)


def listing(
    listing_id: str,
    *,
    title: str = "iPhone 15 Pro 256GB",
    amount: str = "900",
    currency: str = "BGN",
    marketplace: str = "bazar",
    description: str | None = None,
) -> Listing:
    return Listing(
        marketplace=marketplace,
        listing_id=listing_id,
        title=title,
        price_text=f"{amount} {currency}",
        price_amount=Decimal(amount),
        currency=currency,
        url=f"https://example.test/listing/{listing_id}",
        location="София",
        image_url="https://example.test/image.jpg",
        description=description,
    )


def config_data() -> dict:
    return {
        "enabled_marketplaces": ["bazar"],
        "exclude_keywords": ["case", "калъф", "parts", "части", "broken", "счупен"],
        "good_deal_percent": 10,
        "great_deal_percent": 20,
        "request_timeout_seconds": 10,
        "request_retries": 0,
        "request_delay_seconds": 0,
        "pages_per_search": 1,
        "health_alert_after_failures": 3,
        "searches": [
            {
                "name": "iPhone 15 Pro",
                "keywords": ["iphone 15 pro"],
                "max_price_eur": 550,
            }
        ],
    }


class MonitorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config_path = root / "config.json"
        self.state_path = root / "seen.json"
        self.config = config_data()
        self.write_config()
        self.scraper = FakeScraper()
        self.notifier = FakeNotifier()

    def write_config(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False), encoding="utf-8"
        )

    def execute(self, *, notifier=None, scraper=None, dry_run: bool = False, send_test=False) -> int:
        return run(
            self.config_path,
            self.state_path,
            dry_run=dry_run,
            send_test=send_test,
            notifier=notifier if notifier is not None else self.notifier,
            scrapers={"bazar": scraper if scraper is not None else self.scraper},
        )

    def test_first_run_is_quiet_then_only_one_new_deal_is_sent(self) -> None:
        self.scraper.listings = [listing("old")]
        self.assertEqual(0, self.execute())
        self.assertEqual([], self.notifier.notifications)

        self.assertEqual(0, self.execute())
        self.assertEqual([], self.notifier.notifications)

        self.scraper.listings.append(listing("new"))
        self.assertEqual(0, self.execute())
        self.assertEqual(1, len(self.notifier.notifications))
        self.assertEqual("deal:bazar:new", self.notifier.notifications[0].key)

        self.assertEqual(0, self.execute())
        self.assertEqual(1, len(self.notifier.notifications))

    def test_all_encountered_ads_are_seen_even_if_over_limit_or_excluded(self) -> None:
        self.scraper.listings = [listing("seed")]
        self.execute()
        self.scraper.listings.extend(
            [
                listing("expensive", amount="2000"),
                listing("excluded", title="iPhone 15 Pro broken for parts"),
            ]
        )
        self.execute()
        self.assertEqual([], self.notifier.notifications)

        state = StateStore(self.state_path)
        self.assertTrue(state.is_seen("bazar", "expensive"))
        self.assertTrue(state.is_seen("bazar", "excluded"))

        # Making the price ceiling larger or removing exclusions must not turn
        # previously encountered advertisements into "new" deals.
        self.config["searches"][0]["max_price_eur"] = 2000
        self.config["exclude_keywords"] = []
        self.write_config()
        self.execute()
        self.assertEqual([], self.notifier.notifications)

    def test_new_or_changed_keyword_set_gets_its_own_quiet_baseline(self) -> None:
        self.scraper.listings = [listing("old")]
        self.execute()
        self.config["searches"].append(
            {
                "name": "Galaxy S24 Ultra",
                "keywords": ["galaxy s24 ultra"],
                "max_price_eur": 600,
            }
        )
        self.write_config()
        self.scraper.listings.append(
            listing("galaxy-old", title="Samsung Galaxy S24 Ultra", amount="1000")
        )
        self.execute()
        self.assertEqual([], self.notifier.notifications)

    def test_failed_telegram_deal_is_queued_and_retried_next_run(self) -> None:
        self.scraper.listings = [listing("old")]
        self.execute()
        self.scraper.listings.append(listing("new"))

        failing = FakeNotifier(fail_deals=1)
        self.assertEqual(1, self.execute(notifier=failing))
        state = StateStore(self.state_path)
        self.assertEqual(["deal:bazar:new"], [item["key"] for item in state.pending()])
        self.assertTrue(state.is_seen("bazar", "new"))

        recovered = FakeNotifier()
        self.assertEqual(0, self.execute(notifier=recovered))
        self.assertEqual(["deal:bazar:new"], [item.key for item in recovered.notifications])
        self.assertEqual([], StateStore(self.state_path).pending())

    def test_dry_run_does_not_write_state_or_send_telegram(self) -> None:
        self.scraper.listings = [listing("existing")]
        self.assertEqual(0, self.execute(dry_run=True))
        self.assertFalse(self.state_path.exists())
        self.assertEqual([], self.notifier.notifications)

    def test_manual_test_message_is_sent_before_monitoring(self) -> None:
        self.scraper.listings = []
        self.assertEqual(0, self.execute(send_test=True))
        self.assertEqual(1, self.notifier.tests_sent)

    def test_health_warning_after_three_failures_and_one_recovery(self) -> None:
        self.scraper.error = RuntimeError("marketplace unavailable")
        self.assertEqual(1, self.execute())
        self.assertEqual(1, self.execute())
        self.assertEqual(1, self.execute())
        self.assertEqual(1, len(self.notifier.health_failures))
        self.assertEqual(("bazar", 3), self.notifier.health_failures[0][:2])

        self.assertEqual(1, self.execute())
        self.assertEqual(1, len(self.notifier.health_failures))

        self.scraper.error = None
        self.scraper.listings = []
        self.assertEqual(0, self.execute())
        self.assertEqual(["bazar"], self.notifier.health_recoveries)

        self.assertEqual(0, self.execute())
        self.assertEqual(["bazar"], self.notifier.health_recoveries)

    def test_overlapping_searches_choose_most_specific_name(self) -> None:
        self.config["searches"] = [
            {
                "name": "iPhone 15 Pro",
                "keywords": ["iphone 15 pro"],
                "max_price_eur": 600,
                "market_reference": {
                    "median_price_eur": 490,
                    "sample_size": 45,
                    "scope": "mixed storage",
                    "as_of": "2026-08-25",
                    "source": "OLX Pro sample",
                    "resale_demand": "very_high",
                },
            },
            {
                "name": "iPhone 15 Pro Max",
                "keywords": ["iphone 15 pro max"],
                "max_price_eur": 650,
                "market_reference": {
                    "median_price_eur": 599,
                    "sample_size": 34,
                    "scope": "Pro Max mixed storage",
                    "as_of": "2026-08-25",
                    "source": "OLX Pro Max sample",
                    "resale_demand": "high",
                },
            },
        ]
        self.write_config()
        self.scraper.listings = [listing("old-max", title="iPhone 15 Pro Max 256GB")]
        self.execute()

        self.scraper.listings.append(listing("new-max", title="iPhone 15 Pro Max 512GB"))
        self.execute()
        self.assertEqual(1, len(self.notifier.notifications))
        text = self.notifier.notifications[0].text
        self.assertIn("iPhone 15 Pro Max", text)
        self.assertIn("Typical market asking price: <b>€599", text)
        self.assertIn("OLX Pro Max sample; 34 ads; Pro Max mixed storage", text)
        self.assertIn("Estimated resale demand: <b>HIGH</b>", text)
        self.assertNotIn("OLX Pro sample", text)

    def test_global_title_exclusion_does_not_scan_description(self) -> None:
        self.config["exclude_keywords"] = []
        self.config["exclude_title_keywords"] = ["case"]
        self.write_config()
        self.scraper.listings = [listing("old")]
        self.execute()

        self.scraper.listings.extend(
            [
                listing("accessory", title="Case for iPhone 15 Pro"),
                listing(
                    "phone-with-case",
                    title="iPhone 15 Pro 256GB",
                    description="Good phone supplied with a case",
                ),
            ]
        )
        self.execute()

        self.assertEqual(
            ["deal:bazar:phone-with-case"],
            [notification.key for notification in self.notifier.notifications],
        )
        state = StateStore(self.state_path)
        self.assertTrue(state.is_seen("bazar", "accessory"))

    def test_search_title_exclusion_prevents_wrong_variant_notification(self) -> None:
        self.config["searches"] = [
            {
                "name": "iPhone 15",
                "keywords": ["iphone 15"],
                "title_exclude_keywords": ["pro", "plus"],
                "max_price_eur": 500,
                "market_reference": {
                    "median_price_eur": 350,
                    "sample_size": 30,
                    "scope": "mostly 128GB",
                    "as_of": "2026-08-25",
                    "source": "OLX base-model sample",
                    "resale_demand": "very_high",
                },
            }
        ]
        self.write_config()
        self.scraper.listings = [listing("old", title="iPhone 15 128GB")]
        self.execute()

        self.scraper.listings.append(listing("new-pro", title="iPhone 15 Pro 128GB"))
        self.execute()
        self.assertEqual([], self.notifier.notifications)
        self.assertTrue(StateStore(self.state_path).is_seen("bazar", "new-pro"))

    def test_description_condition_exclusion_still_blocks_phone(self) -> None:
        self.config["exclude_keywords"] = ["broken"]
        self.config["exclude_title_keywords"] = []
        self.write_config()
        self.scraper.listings = [listing("old")]
        self.execute()

        self.scraper.listings.append(
            listing(
                "broken-detail",
                title="iPhone 15 Pro 256GB",
                description="The back glass is broken",
            )
        )
        self.execute()
        self.assertEqual([], self.notifier.notifications)
        self.assertEqual(["broken-detail"], self.scraper.enriched)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
