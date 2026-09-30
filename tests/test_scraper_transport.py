"""Request-policy and detail-enrichment tests with an in-memory HTTP fake."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

import requests

from scrapers.base import AccessBlockedError, Listing, RequestFailedError
from scrapers import build_scrapers
from scrapers.alo import AloScraper
from scrapers.bazar import BazarScraper
from scrapers.olx import OlxScraper


FIXTURES = Path(__file__).with_name("fixtures")


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        text: str = "",
        *,
        url: str = "https://example.test/results",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, dict | None, float, bool]] = []

    def get(self, url: str, *, params, timeout: float, allow_redirects: bool):
        self.calls.append((url, params, timeout, allow_redirects))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RequestPolicyTests(unittest.TestCase):
    def test_alo_search_uses_query_and_pagination_params_then_deduplicates(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    fixture("alo_search.html"),
                    url="https://www.alo.bg/obiavi/gsm-komunikacii/mobilni-telefoni-gsm/?q=iphone",
                ),
                FakeResponse(
                    200,
                    fixture("alo_search.html"),
                    url=(
                        "https://www.alo.bg/obiavi/gsm-komunikacii/"
                        "mobilni-telefoni-gsm/?q=iphone&page=2"
                    ),
                ),
            ]
        )
        scraper = AloScraper(session=session, retries=0, delay_seconds=0)

        results = scraper.search("  iphone 15 pro  ", pages=2)

        self.assertEqual(3, len(results))
        self.assertEqual(2, len(session.calls))
        self.assertEqual(scraper.search_url, session.calls[0][0])
        self.assertEqual({"q": "iphone 15 pro"}, session.calls[0][1])
        self.assertEqual({"q": "iphone 15 pro", "page": 2}, session.calls[1][1])

    def test_alo_empty_query_does_not_make_a_request(self) -> None:
        session = FakeSession([])
        scraper = AloScraper(session=session, retries=0, delay_seconds=0)
        self.assertEqual([], scraper.search("   "))
        self.assertEqual([], session.calls)

    def test_missing_later_search_page_keeps_first_page_results(self) -> None:
        for scraper_type, fixture_name in (
            (AloScraper, "alo_search.html"),
            (BazarScraper, "bazar_search.html"),
        ):
            with self.subTest(scraper=scraper_type.__name__):
                session = FakeSession(
                    [
                        FakeResponse(200, fixture(fixture_name)),
                        FakeResponse(404),
                    ]
                )
                scraper = scraper_type(session=session, retries=0, delay_seconds=0)
                self.assertTrue(scraper.search("iphone", pages=2))
                self.assertEqual(2, len(session.calls))

    def test_transient_http_response_is_retried_a_bounded_number(self) -> None:
        session = FakeSession(
            [
                FakeResponse(429, headers={"Retry-After": "0"}),
                FakeResponse(200, fixture("bazar_search.html"), url="https://bazar.bg/obiavi?q=x"),
            ]
        )
        scraper = BazarScraper(
            session=session, retries=2, timeout=6, delay_seconds=0, backoff_seconds=0
        )
        results = scraper.search("iphone")
        self.assertTrue(results)
        self.assertEqual(2, len(session.calls))
        self.assertEqual(6, session.calls[0][2])

    def test_timeout_is_retried_then_can_recover(self) -> None:
        session = FakeSession(
            [
                requests.Timeout("slow"),
                FakeResponse(200, fixture("olx_search.html"), url="https://www.olx.bg/ads/q-x/"),
            ]
        )
        scraper = OlxScraper(
            session=session, retries=1, delay_seconds=0, backoff_seconds=0
        )
        self.assertTrue(scraper.search("iphone"))
        self.assertEqual(2, len(session.calls))

    def test_access_block_is_reported_and_not_bypassed_or_retried(self) -> None:
        session = FakeSession([FakeResponse(403)])
        scraper = OlxScraper(
            session=session, retries=5, delay_seconds=0, backoff_seconds=0
        )
        with self.assertRaisesRegex(AccessBlockedError, "HTTP 403"):
            scraper.search("iphone")
        self.assertEqual(1, len(session.calls))

    def test_200_access_challenge_is_reported_as_blocked(self) -> None:
        session = FakeSession(
            [FakeResponse(200, "<html><title>Just a moment...</title><div id=challenge-form></div>")]
        )
        scraper = OlxScraper(session=session, retries=3, delay_seconds=0)
        with self.assertRaisesRegex(AccessBlockedError, "access-verification challenge"):
            scraper.search("iphone")
        self.assertEqual(1, len(session.calls))

    def test_permanent_http_error_is_clear(self) -> None:
        session = FakeSession([FakeResponse(404)])
        scraper = BazarScraper(session=session, retries=0, delay_seconds=0)
        with self.assertRaisesRegex(RequestFailedError, "HTTP 404"):
            scraper.search("iphone")

    def test_scraper_sets_an_honest_user_agent(self) -> None:
        session = FakeSession([])
        BazarScraper(session=session)
        self.assertIn("PhoneDealMonitor", session.headers["User-Agent"])

    def test_registry_can_enable_bazar_and_alo_while_olx_is_paused(self) -> None:
        scrapers = build_scrapers(
            {"enabled_marketplaces": ["bazar", "alo"]}, session=FakeSession([])
        )
        self.assertEqual({"bazar", "alo"}, set(scrapers))


class DetailEnrichmentTests(unittest.TestCase):
    def test_alo_enrichment_adds_description_location_and_image(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    fixture("alo_detail.html"),
                    url="https://www.alo.bg/iphone-15-pro-11374513",
                )
            ]
        )
        scraper = AloScraper(session=session, retries=0, delay_seconds=0)
        original = Listing(
            "alo", "11374513", "iPhone", "530 €", Decimal("530"), "EUR",
            "https://www.alo.bg/iphone-15-pro-11374513",
        )
        enriched = scraper.enrich(original)
        self.assertIn("кутия", enriched.description or "")
        self.assertIn("Бургас", enriched.location or "")
        self.assertEqual("https://img.alo.bg/detail-iphone.jpg", enriched.image_url)

    def test_bazar_enrichment_adds_description_location_and_image(self) -> None:
        session = FakeSession(
            [FakeResponse(200, fixture("bazar_detail.html"), url="https://bazar.bg/obiava-1/x")]
        )
        scraper = BazarScraper(session=session, retries=0, delay_seconds=0)
        original = Listing(
            "bazar", "1", "iPhone", "780 лв.", Decimal("780"), "BGN",
            "https://bazar.bg/obiava-1/x",
        )
        enriched = scraper.enrich(original)
        self.assertIn("кутия", enriched.description or "")
        self.assertIn("Бургас", enriched.location or "")
        self.assertEqual("https://img.bazar.bg/detail.jpg", enriched.image_url)

    def test_olx_enrichment_adds_description_location_and_image(self) -> None:
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    fixture("olx_detail.html"),
                    url="https://www.olx.bg/d/obyava/x-IDabc.html",
                )
            ]
        )
        scraper = OlxScraper(session=session, retries=0, delay_seconds=0)
        original = Listing(
            "olx", "abc", "iPhone", "500 EUR", Decimal("500"), "EUR",
            "https://www.olx.bg/d/obyava/x-IDabc.html",
        )
        enriched = scraper.enrich(original)
        self.assertIn("гаранция", enriched.description or "")
        self.assertIn("Търново", enriched.location or "")
        self.assertEqual("https://apollo.olxcdn.com/detail.jpg", enriched.image_url)

    def test_enrichment_network_failure_keeps_original_listing(self) -> None:
        session = FakeSession([requests.Timeout("offline")])
        scraper = OlxScraper(session=session, retries=0, delay_seconds=0)
        original = Listing(
            "olx", "abc", "iPhone", "500 EUR", Decimal("500"), "EUR",
            "https://www.olx.bg/d/obyava/x-IDabc.html",
        )
        self.assertIs(original, scraper.enrich(original))

    def test_alo_enrichment_network_failure_keeps_original_listing(self) -> None:
        session = FakeSession([requests.Timeout("offline")])
        scraper = AloScraper(session=session, retries=0, delay_seconds=0)
        original = Listing(
            "alo", "11374513", "iPhone", "530 €", Decimal("530"), "EUR",
            "https://www.alo.bg/iphone-15-pro-11374513",
        )
        self.assertIs(original, scraper.enrich(original))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
