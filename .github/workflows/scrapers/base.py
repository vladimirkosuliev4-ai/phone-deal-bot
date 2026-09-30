"""Shared marketplace scraper primitives.

The scrapers use normal public HTML pages only.  They deliberately do not try
to evade access controls, solve CAPTCHAs, or impersonate a logged-in browser.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import time
from typing import Any, Iterator, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


DEFAULT_USER_AGENT = (
    "PhoneDealMonitor/1.0 "
    "(personal, low-frequency marketplace monitor; +https://github.com/)"
)
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
BLOCKED_STATUS_CODES = frozenset({401, 403, 451})


@dataclass(frozen=True, slots=True)
class Listing:
    """Marketplace-neutral representation of one advertisement."""

    marketplace: str
    listing_id: str
    title: str
    price_text: str
    price_amount: Decimal | None
    currency: str | None
    url: str
    location: str | None = None
    image_url: str | None = None
    description: str | None = None


class ScraperError(RuntimeError):
    """A marketplace request or response could not be processed."""

    def __init__(
        self,
        message: str,
        *,
        marketplace: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.marketplace = marketplace
        self.url = url
        self.status_code = status_code


class AccessBlockedError(ScraperError):
    """The website refused ordinary public-page access."""


class RequestFailedError(ScraperError):
    """A request failed after the configured bounded retries."""


class BaseScraper(ABC):
    """Common request handling and the interface used by the monitor."""

    name = "base"
    base_url = ""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
        retries: int = 2,
        backoff_seconds: float = 1.0,
        delay_seconds: float = 0.75,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = max(1.0, float(timeout))
        self.retries = max(0, min(int(retries), 5))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.delay_seconds = max(0.0, float(delay_seconds))
        self._last_request_at: float | None = None

        current_user_agent = self.session.headers.get("User-Agent", "")
        if not current_user_agent or current_user_agent.startswith("python-requests/"):
            self.session.headers["User-Agent"] = user_agent
        self.session.headers.setdefault(
            "Accept",
            "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.5",
        )
        self.session.headers.setdefault("Accept-Language", "bg-BG,bg;q=0.9,en;q=0.7")

    @abstractmethod
    def search(self, query: str, pages: int = 1) -> list[Listing]:
        """Return newest-first public listings for *query*."""

    def enrich(self, listing: Listing) -> Listing:
        """Best-effort detail fetch; concrete scrapers may add missing fields."""

        return listing

    def _request(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> requests.Response:
        """GET one public page with a small, bounded transient-error retry loop."""

        last_error: BaseException | None = None
        final_url = url

        for attempt in range(self.retries + 1):
            self._wait_until_polite()
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                self._last_request_at = time.monotonic()
                final_url = response.url or url
            except (requests.Timeout, requests.ConnectionError) as exc:
                self._last_request_at = time.monotonic()
                last_error = exc
                if attempt < self.retries:
                    self._sleep_before_retry(attempt, None)
                    continue
                raise RequestFailedError(
                    f"{self.name}: request failed after {attempt + 1} attempt(s): {exc}",
                    marketplace=self.name,
                    url=final_url,
                ) from exc
            except requests.RequestException as exc:
                raise RequestFailedError(
                    f"{self.name}: request failed: {exc}",
                    marketplace=self.name,
                    url=final_url,
                ) from exc

            if response.status_code in BLOCKED_STATUS_CODES:
                raise AccessBlockedError(
                    f"{self.name}: public page access was refused (HTTP {response.status_code})",
                    marketplace=self.name,
                    url=final_url,
                    status_code=response.status_code,
                )

            if response.status_code in TRANSIENT_STATUS_CODES:
                if attempt < self.retries:
                    self._sleep_before_retry(attempt, response)
                    continue
                raise RequestFailedError(
                    f"{self.name}: HTTP {response.status_code} after {attempt + 1} attempt(s)",
                    marketplace=self.name,
                    url=final_url,
                    status_code=response.status_code,
                )

            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise RequestFailedError(
                    f"{self.name}: HTTP {response.status_code}",
                    marketplace=self.name,
                    url=final_url,
                    status_code=response.status_code,
                ) from exc
            if _looks_like_access_challenge(response.text):
                raise AccessBlockedError(
                    f"{self.name}: public page returned an access-verification challenge",
                    marketplace=self.name,
                    url=final_url,
                    status_code=response.status_code,
                )
            return response

        # The loop always returns or raises.  This keeps type checkers honest.
        raise RequestFailedError(
            f"{self.name}: request failed: {last_error or 'unknown error'}",
            marketplace=self.name,
            url=final_url,
        )

    def _wait_until_polite(self) -> None:
        if self._last_request_at is None or self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def _sleep_before_retry(
        self,
        attempt: int,
        response: requests.Response | None,
    ) -> None:
        retry_after = 0.0
        if response is not None:
            value = response.headers.get("Retry-After", "").strip()
            if value.isdigit():
                retry_after = min(float(value), 30.0)
        delay = max(retry_after, self.backoff_seconds * (2**attempt))
        if delay > 0:
            time.sleep(min(delay, 30.0))


def clean_text(value: Any) -> str:
    """Collapse whitespace in human-readable text."""

    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def _looks_like_access_challenge(html: str) -> bool:
    """Recognize conservative 200-OK anti-bot pages without false positives."""

    beginning = (html or "")[:60_000].casefold()
    markers = (
        "<title>just a moment",
        "<title>access denied",
        "request could not be satisfied",
        'id="challenge-form"',
        "cf-chl-",
        "verify you are human",
        "confirm you are human",
    )
    return any(marker in beginning for marker in markers)


def canonical_url(
    value: str | None,
    base_url: str,
    *,
    strip_query: bool = False,
) -> str:
    """Resolve relative and scheme-relative URLs and remove accidental // paths."""

    if not value:
        return ""
    resolved = urljoin(base_url, value.strip())
    parts = urlsplit(resolved)
    path = re.sub(r"/{2,}", "/", parts.path)
    query = "" if strip_query else parts.query
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def stable_fallback_id(url: str) -> str:
    """Create a deterministic ID only when a marketplace ID is unavailable."""

    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def parse_price_text(value: str | None) -> tuple[Decimal | None, str | None]:
    """Parse the first displayed EUR/BGN price without doing conversion.

    Both suffix (``780 лв.``) and prefix (``€ 399.99``) styles are accepted.
    The mojibake replacements make saved fixtures and mislabelled legacy pages
    parseable without changing the listing text shown to the user.
    """

    text = clean_text(value)
    if not text:
        return None, None
    detectable = (
        text.replace("â‚¬", "€")
        .replace("Ð»Ð².", "лв.")
        .replace("Ð»Ð²", "лв")
    )
    number = r"(?P<number>\d[\d\s\xa0.,']*)"
    currency = r"(?P<currency>€|EUR|BGN|лв\.?)"
    matches: list[tuple[int, str, str]] = []

    suffix = re.compile(number + r"\s*" + currency, re.IGNORECASE)
    prefix = re.compile(currency + r"\s*" + number, re.IGNORECASE)
    for match in suffix.finditer(detectable):
        matches.append((match.start(), match.group("number"), match.group("currency")))
    for match in prefix.finditer(detectable):
        matches.append((match.start(), match.group("number"), match.group("currency")))

    for _, raw_number, raw_currency in sorted(matches, key=lambda item: item[0]):
        amount = _parse_localized_decimal(raw_number)
        if amount is None:
            continue
        normalized_currency = (
            "EUR" if raw_currency.casefold() in {"€", "eur"} else "BGN"
        )
        return amount, normalized_currency
    return None, None


def _parse_localized_decimal(value: str) -> Decimal | None:
    compact = re.sub(r"[\s\xa0']", "", value)
    if not compact or not any(character.isdigit() for character in compact):
        return None

    comma_positions = [index for index, char in enumerate(compact) if char == ","]
    dot_positions = [index for index, char in enumerate(compact) if char == "."]
    separators = comma_positions + dot_positions

    if comma_positions and dot_positions:
        decimal_position = max(separators)
        fraction_length = len(compact) - decimal_position - 1
        if fraction_length in (1, 2):
            integer = re.sub(r"[.,]", "", compact[:decimal_position])
            fraction = compact[decimal_position + 1 :]
            normalized = f"{integer}.{fraction}"
        else:
            normalized = re.sub(r"[.,]", "", compact)
    elif separators:
        separator = "," if comma_positions else "."
        positions = comma_positions or dot_positions
        decimal_position = positions[-1]
        fraction_length = len(compact) - decimal_position - 1
        if fraction_length in (1, 2):
            integer = compact[:decimal_position].replace(separator, "")
            fraction = compact[decimal_position + 1 :]
            normalized = f"{integer}.{fraction}"
        else:
            normalized = compact.replace(separator, "")
    else:
        normalized = compact

    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


def iter_json_documents(soup: BeautifulSoup) -> Iterator[Any]:
    """Yield valid JSON-LD and common embedded JSON documents."""

    selectors = (
        'script[type="application/ld+json"]',
        "script#__NEXT_DATA__",
        'script[type="application/json"][data-testid="initial-state"]',
    )
    seen_nodes: set[int] = set()
    for selector in selectors:
        for node in soup.select(selector):
            identity = id(node)
            if identity in seen_nodes:
                continue
            seen_nodes.add(identity)
            raw = node.string or node.get_text()
            if not raw or not raw.strip():
                continue
            try:
                yield json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue


def walk_json(value: Any) -> Iterator[Any]:
    """Depth-first traversal used for tolerant embedded-data fallbacks."""

    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)
