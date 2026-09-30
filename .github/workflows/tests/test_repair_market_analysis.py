from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repair_flip.analysis import analyze_flip
from repair_flip.config import load_repair_config
from repair_flip.detection import detect_defects
from repair_flip.domain import ComparableRecord
from repair_flip.market import build_market_baseline
from tests.repair_helpers import repair_config_data


class RepairMarketAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(repair_config_data(), ensure_ascii=False), encoding="utf-8")
        self.config = load_repair_config(path)
        self.phone = self.config.phones[0]
        self.working_detection = detect_defects("Fully working phone", self.config.rules)
        self.screen_detection = detect_defects("Broken screen", self.config.rules)

    def record(
        self,
        listing_id: str,
        price: int,
        *,
        storage: int = 256,
        broken: bool = False,
        title: str | None = None,
    ) -> ComparableRecord:
        return ComparableRecord(
            marketplace="bazar",
            listing_id=listing_id,
            title=title or f"iPhone 14 Pro Max {storage}GB {listing_id}",
            price_eur=Decimal(price),
            storage_gb=storage,
            detection=self.screen_detection if broken else self.working_detection,
        )

    def useful_records(self) -> list[ComparableRecord]:
        records = [
            self.record(f"w{index}", price)
            for index, price in enumerate((390, 410, 430, 450, 470), 1)
        ]
        records.extend(
            self.record(f"b{index}", price, broken=True)
            for index, price in enumerate((250, 270, 290), 1)
        )
        return records

    def test_builds_exact_storage_ranges_and_removes_outlier(self) -> None:
        records = self.useful_records()
        records.append(self.record("outlier", 2000))
        records.append(self.record("wrong-storage", 300, storage=128))
        baseline = build_market_baseline(
            records,
            candidate_storage_gb=256,
            candidate_detection=self.screen_detection,
            config=self.config,
        )
        self.assertEqual(Decimal("430.00"), baseline.working_median_eur)
        self.assertEqual(Decimal("270.00"), baseline.broken_median_eur)
        self.assertEqual(5, baseline.working_count)
        self.assertEqual(3, baseline.broken_count)
        self.assertEqual(1, baseline.outliers_removed)
        self.assertEqual("exact 256GB", baseline.storage_scope)

    def test_refuses_to_invent_price_with_too_few_exact_comparables(self) -> None:
        records = [self.record("w1", 420), self.record("w2", 440)]
        records.extend(self.record(f"x{i}", 300 + i, storage=128) for i in range(8))
        baseline = build_market_baseline(
            records,
            candidate_storage_gb=256,
            candidate_detection=self.screen_detection,
            config=self.config,
        )
        self.assertIsNone(baseline.working_median_eur)
        self.assertEqual("LOW", baseline.confidence)

    def test_optional_mixed_storage_fallback_is_explicit(self) -> None:
        records = [self.record("w1", 420), self.record("w2", 440)]
        records.extend(self.record(f"x{i}", 300 + i * 10, storage=128) for i in range(6))
        baseline = build_market_baseline(
            records,
            candidate_storage_gb=256,
            candidate_detection=self.screen_detection,
            config=replace(self.config, allow_mixed_storage_fallback=True),
        )
        self.assertIsNotNone(baseline.working_median_eur)
        self.assertIn("mixed storage fallback", baseline.storage_scope)

    def test_classifies_good_flip_from_market_not_fixed_purchase_limit(self) -> None:
        baseline = build_market_baseline(
            self.useful_records(),
            candidate_storage_gb=256,
            candidate_detection=self.screen_detection,
            config=self.config,
        )
        analysis = analyze_flip(
            phone=self.phone,
            storage_gb=256,
            detection=self.screen_detection,
            baseline=baseline,
            purchase_price_eur=Decimal("200"),
            config=self.config,
        )
        self.assertEqual("GOOD FLIP", analysis.classification)
        self.assertEqual(Decimal("408.50"), analysis.expected_resale_eur)
        self.assertEqual(Decimal("96.00"), analysis.repair.expected_eur)
        self.assertEqual(Decimal("311.00"), analysis.total_investment_eur)
        self.assertEqual(Decimal("97.50"), analysis.estimated_profit_eur)
        self.assertEqual(Decimal("31.4"), analysis.roi_percent)

    def test_high_purchase_price_is_skip_and_no_power_is_high_risk(self) -> None:
        baseline = build_market_baseline(
            self.useful_records(),
            candidate_storage_gb=256,
            candidate_detection=self.screen_detection,
            config=self.config,
        )
        expensive = analyze_flip(
            phone=self.phone,
            storage_gb=256,
            detection=self.screen_detection,
            baseline=baseline,
            purchase_price_eur=Decimal("350"),
            config=self.config,
        )
        self.assertEqual("SKIP", expensive.classification)

        power = detect_defects("No power", self.config.rules)
        risky = analyze_flip(
            phone=self.phone,
            storage_gb=256,
            detection=power,
            baseline=baseline,
            purchase_price_eur=Decimal("100"),
            config=self.config,
        )
        self.assertEqual("HIGH RISK", risky.classification)

    def test_insufficient_market_is_explicit_low_confidence(self) -> None:
        baseline = build_market_baseline(
            [self.record("w1", 430)],
            candidate_storage_gb=256,
            candidate_detection=self.screen_detection,
            config=self.config,
        )
        analysis = analyze_flip(
            phone=self.phone,
            storage_gb=256,
            detection=self.screen_detection,
            baseline=baseline,
            purchase_price_eur=Decimal("150"),
            config=self.config,
        )
        self.assertEqual("MAYBE", analysis.classification)
        self.assertIsNone(analysis.expected_resale_eur)
        self.assertIn("LOW CONFIDENCE", analysis.reason)


if __name__ == "__main__":
    unittest.main()
