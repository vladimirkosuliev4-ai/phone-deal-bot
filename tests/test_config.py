"""Configuration tests focused on errors a beginner can correct."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from utils.config import ConfigError, load_config


def valid_config() -> dict:
    return {
        "enabled_marketplaces": ["bazar", "alo"],
        "exclude_keywords": ["case", "калъф"],
        "searches": [
            {
                "name": "iPhone 15 Pro",
                "keywords": ["iphone 15 pro"],
                "max_price_eur": 550,
            }
        ],
    }


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def write(self, value: object) -> None:
        self.path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_loads_searches_as_typed_values_and_defaults(self) -> None:
        self.write(valid_config())
        config = load_config(self.path)
        self.assertEqual(("bazar", "alo"), config.enabled_marketplaces)
        self.assertEqual(("case", "калъф"), config.exclude_keywords)
        self.assertEqual("iPhone 15 Pro", config.searches[0].name)
        self.assertEqual(("iphone 15 pro",), config.searches[0].keywords)
        self.assertEqual(Decimal("550"), config.searches[0].max_price_eur)
        self.assertEqual(Decimal("10"), config.good_deal_percent)
        self.assertEqual(Decimal("20"), config.great_deal_percent)

    def test_loads_title_exclusions_and_typed_market_reference(self) -> None:
        data = valid_config()
        data["exclude_title_keywords"] = ["case", "калъф"]
        data["searches"][0]["title_exclude_keywords"] = ["pro max"]
        data["searches"][0]["market_reference"] = {
            "median_price_eur": "490.50",
            "sample_size": 45,
            "scope": "mixed storage",
            "as_of": "2026-08-25",
            "source": "OLX.bg cleaned asking-price sample",
            "resale_demand": "very_high",
        }
        self.write(data)

        config = load_config(self.path)
        search = config.searches[0]
        self.assertEqual(("case", "калъф"), config.exclude_title_keywords)
        self.assertEqual(("pro max",), search.title_exclude_keywords)
        self.assertIsNotNone(search.market_reference)
        reference = search.market_reference
        assert reference is not None
        self.assertEqual(Decimal("490.50"), reference.median_price_eur)
        self.assertEqual(45, reference.sample_size)
        self.assertEqual("mixed storage", reference.scope)
        self.assertEqual("2026-08-25", reference.as_of)
        self.assertEqual("OLX.bg cleaned asking-price sample", reference.source)
        self.assertEqual("very high", reference.resale_demand)

    def test_market_reference_is_optional(self) -> None:
        self.write(valid_config())
        self.assertIsNone(load_config(self.path).searches[0].market_reference)

    def test_shipped_config_is_valid(self) -> None:
        repository_config = Path(__file__).parents[1] / "config.json"
        config = load_config(repository_config)
        self.assertTrue(config.searches)
        self.assertTrue(config.exclude_title_keywords)
        self.assertTrue(all(search.market_reference for search in config.searches))

    def test_defaults_to_bazar_and_alo_when_marketplaces_are_omitted(self) -> None:
        data = valid_config()
        del data["enabled_marketplaces"]
        self.write(data)
        self.assertEqual(("bazar", "alo"), load_config(self.path).enabled_marketplaces)

    def test_invalid_json_reports_line_and_column(self) -> None:
        self.path.write_text('{"searches": [}', encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, r"line \d+, column \d+"):
            load_config(self.path)

    def test_missing_file_is_explained(self) -> None:
        with self.assertRaisesRegex(ConfigError, "Configuration file not found"):
            load_config(self.path)

    def test_rejects_unknown_or_duplicate_marketplaces(self) -> None:
        data = valid_config()
        data["enabled_marketplaces"] = ["alo", "example"]
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "Unknown marketplace"):
            load_config(self.path)

        data["enabled_marketplaces"] = ["alo", "alo"]
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "duplicates"):
            load_config(self.path)

    def test_rejects_missing_searches_and_invalid_search_fields(self) -> None:
        data = valid_config()
        data["searches"] = []
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "at least one phone search"):
            load_config(self.path)

        data["searches"] = [{"name": "Phone", "keywords": [], "max_price_eur": 500}]
        self.write(data)
        with self.assertRaisesRegex(ConfigError, r"searches\[0\]\.keywords"):
            load_config(self.path)

        data["searches"] = [{"name": "Phone", "keywords": ["phone"], "max_price_eur": 0}]
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "greater than zero"):
            load_config(self.path)

    def test_rejects_invalid_deal_threshold_order(self) -> None:
        data = valid_config()
        data.update({"good_deal_percent": 20, "great_deal_percent": 10})
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "percentages"):
            load_config(self.path)

    def test_rejects_out_of_range_request_settings(self) -> None:
        data = valid_config()
        data["request_retries"] = 99
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "request_retries"):
            load_config(self.path)

    def test_rejects_invalid_title_exclusion_lists(self) -> None:
        data = valid_config()
        data["exclude_title_keywords"] = ["case", ""]
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "exclude_title_keywords"):
            load_config(self.path)

        data = valid_config()
        data["searches"][0]["title_exclude_keywords"] = "pro max"
        self.write(data)
        with self.assertRaisesRegex(ConfigError, "title_exclude_keywords"):
            load_config(self.path)

    def test_rejects_invalid_market_reference_schema(self) -> None:
        valid_reference = {
            "median_price_eur": 490,
            "sample_size": 45,
            "scope": "mixed storage",
            "as_of": "2026-08-25",
            "source": "OLX.bg sample",
            "resale_demand": "very_high",
        }
        cases = (
            ([], "JSON object"),
            ({**valid_reference, "median_price_eur": 0}, "greater than zero"),
            ({**valid_reference, "median_price_eur": "NaN"}, "finite number"),
            ({**valid_reference, "sample_size": True}, "whole number"),
            ({**valid_reference, "sample_size": 0}, "whole number"),
            ({**valid_reference, "sample_size": 100_001}, "whole number"),
            ({**valid_reference, "scope": "  "}, "scope"),
            ({**valid_reference, "source": None}, "source"),
            ({**valid_reference, "as_of": "25/08/2026"}, "YYYY-MM-DD"),
            ({**valid_reference, "resale_demand": "popular"}, "very_high"),
        )

        for value, message in cases:
            with self.subTest(reference=value):
                data = valid_config()
                data["searches"][0]["market_reference"] = value
                self.write(data)
                with self.assertRaisesRegex(ConfigError, message):
                    load_config(self.path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
