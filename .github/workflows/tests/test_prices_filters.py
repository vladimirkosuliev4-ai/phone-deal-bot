"""Unit tests for localized money parsing and configurable deal filters."""

from __future__ import annotations

from decimal import Decimal
import unittest

from utils.filters import contains_excluded, deal_level, matches_search, normalize_text
from utils.prices import bgn_to_eur, eur_to_bgn, format_price, parse_price


class PriceTests(unittest.TestCase):
    def test_parses_bulgarian_lev_formats(self) -> None:
        for text, expected in (
            ("780 лв", Decimal("780")),
            ("1 050 лв.", Decimal("1050")),
            ("1 234,56 BGN", Decimal("1234.56")),
        ):
            with self.subTest(text=text):
                self.assertEqual((expected, "BGN"), parse_price(text))

    def test_parses_euro_formats(self) -> None:
        for text, expected in (
            ("499,99 €", Decimal("499.99")),
            ("€ 525.50", Decimal("525.50")),
            ("1.234,56 EUR", Decimal("1234.56")),
            ("1,234.56 EUR", Decimal("1234.56")),
        ):
            with self.subTest(text=text):
                self.assertEqual((expected, "EUR"), parse_price(text))

    def test_rejects_missing_or_non_numeric_prices(self) -> None:
        for text in ("", "по договаряне", "без цена", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_price(text))

    def test_conversion_uses_fixed_peg_and_round_trips(self) -> None:
        converted = eur_to_bgn(Decimal("100"))
        self.assertLess(abs(converted - Decimal("195.583")), Decimal("0.005"))
        self.assertLess(abs(bgn_to_eur(converted) - Decimal("100")), Decimal("0.01"))

    def test_conversion_accepts_values_requiring_rounding(self) -> None:
        self.assertLess(
            abs(bgn_to_eur(Decimal("780")) - Decimal("398.806")),
            Decimal("0.01"),
        )

    def test_format_price_is_readable_and_currency_specific(self) -> None:
        bgn = format_price(Decimal("780"), "BGN")
        eur = format_price(Decimal("399.5"), "EUR")
        self.assertIn("780", bgn)
        self.assertTrue("лв" in bgn.lower() or "bgn" in bgn.lower())
        self.assertIn("399", eur)
        self.assertTrue("€" in eur or "eur" in eur.lower())


class FilterTests(unittest.TestCase):
    def test_normalization_handles_case_spacing_and_punctuation(self) -> None:
        self.assertEqual("iphone 15 pro max", normalize_text("  iPhone-15  PRO Max! "))
        self.assertEqual("калъф за телефон", normalize_text("КАЛЪФ за телефон"))

    def test_search_phrase_matches_normalized_contiguous_words(self) -> None:
        title = "Apple iPhone 15 Pro, 256 GB - Natural Titanium"
        self.assertTrue(matches_search(title, ["iphone 15 pro"]))
        self.assertTrue(matches_search(title, ["iphone 16", "15 pro 256 gb"]))
        self.assertFalse(matches_search(title, ["iphone 15 pro max"]))

    def test_compact_seller_model_names_match_normal_keywords(self) -> None:
        self.assertTrue(matches_search("IPhone15PRO 256GB", ["iphone 15 pro"]))
        self.assertTrue(matches_search("Samsung S24Ultra", ["s24 ultra"]))

    def test_search_keywords_are_alternatives(self) -> None:
        self.assertTrue(
            matches_search(
                "Samsung Galaxy S24 Ultra 512GB",
                ["iphone 15 pro", "galaxy s24 ultra"],
            )
        )

    def test_exclusions_are_case_insensitive_in_both_alphabets(self) -> None:
        excluded = ["case", "калъф", "parts", "части", "icloud", "счупен"]
        self.assertTrue(contains_excluded("IPHONE CASE - нов", excluded))
        self.assertTrue(contains_excluded("Продавам СЧУПЕН телефон", excluded))
        self.assertTrue(contains_excluded("iCloud locked", excluded))
        self.assertFalse(contains_excluded("iPhone 15 Pro в отлично състояние", excluded))

    def test_deal_threshold_boundaries_and_savings(self) -> None:
        cases = (
            (Decimal("500"), "MATCH", Decimal("0")),
            (Decimal("451"), "MATCH", Decimal("49")),
            (Decimal("450"), "GOOD DEAL", Decimal("50")),
            (Decimal("401"), "GOOD DEAL", Decimal("99")),
            (Decimal("400"), "GREAT DEAL", Decimal("100")),
        )
        for price, expected_name, expected_saving in cases:
            with self.subTest(price=price):
                label, saving = deal_level(price, Decimal("500"))
                self.assertIn(expected_name, label)
                self.assertEqual(expected_saving, saving)

    def test_over_limit_is_not_a_deal(self) -> None:
        self.assertIsNone(deal_level(Decimal("501"), Decimal("500")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
