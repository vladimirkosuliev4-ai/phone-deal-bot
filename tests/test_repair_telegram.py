from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repair_flip.analysis import analyze_flip
from repair_flip.config import load_repair_config
from repair_flip.detection import detect_defects
from repair_flip.domain import ComparableRecord
from repair_flip.market import build_market_baseline
from repair_flip.telegram import build_repair_notification
from scrapers.base import Listing
from tests.repair_helpers import repair_config_data


class RepairTelegramTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(repair_config_data(), ensure_ascii=False), encoding="utf-8")
        self.config = load_repair_config(path)
        self.phone = self.config.phones[0]

    def analysis(self):
        working = detect_defects("working", self.config.rules)
        screen = detect_defects("broken screen", self.config.rules)
        records = [
            ComparableRecord("bazar", f"w{i}", f"working {i}", Decimal(price), 256, working)
            for i, price in enumerate((390, 410, 430, 450, 470), 1)
        ]
        records.extend(
            ComparableRecord("bazar", f"b{i}", f"broken {i}", Decimal(price), 256, screen)
            for i, price in enumerate((250, 270, 290), 1)
        )
        baseline = build_market_baseline(
            records,
            candidate_storage_gb=256,
            candidate_detection=screen,
            config=self.config,
        )
        return analyze_flip(
            phone=self.phone,
            storage_gb=256,
            detection=screen,
            baseline=baseline,
            purchase_price_eur=Decimal("200"),
            config=self.config,
        )

    def test_complete_compact_safe_report(self) -> None:
        listing = Listing(
            marketplace="bazar",
            listing_id="new1",
            title="iPhone 14 Pro Max 256GB <broken> & cheap",
            price_text="€200",
            price_amount=Decimal("200"),
            currency="EUR",
            url='https://example.test/ad?x=1&from="test"',
            location="София",
            image_url="https://example.test/image.jpg",
        )
        notification = build_repair_notification(listing, self.analysis())
        self.assertEqual("repair:bazar:new1", notification.key)
        self.assertIn("GOOD FLIP", notification.text)
        self.assertIn("Working low / median / high", notification.text)
        self.assertIn("Broken market median", notification.text)
        self.assertIn("Profit", notification.text)
        self.assertIn("ROI", notification.text)
        self.assertIn("Comparables: 5 working / 3 broken", notification.text)
        self.assertIn("&amp;from=", notification.text)
        self.assertNotIn("<broken>", notification.text)
        self.assertLessEqual(len(notification.text), 1024)


if __name__ == "__main__":
    unittest.main()
