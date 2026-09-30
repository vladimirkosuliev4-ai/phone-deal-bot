"""Public-page scraper for phone listings on ALO.bg."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from .base import (
    BaseScraper,
    Listing,
    RequestFailedError,
    ScraperError,
    canonical_url,
    clean_text,
    iter_json_documents,
    parse_price_text,
    walk_json,
)


class AloScraper(BaseScraper):
    """Read ALO's public mobile-phone result and detail pages."""

    name = "alo"
    base_url = "https://www.alo.bg/"
    search_url = (
        "https://www.alo.bg/obiavi/gsm-komunikacii/mobilni-telefoni-gsm/"
    )
    _id_pattern = re.compile(r"-(\d{6,})(?:/)?(?:[?#].*)?$", re.IGNORECASE)
    _price_pattern = re.compile(
        r"(?:Цена\s*:?\s*)?"
        r"(\d[\d\s\xa0.,']{0,24}\s*(?:€|EUR|BGN|лв\.?)"
        r"(?:\s*\([^)]{1,48}\))?)",
        re.IGNORECASE,
    )

    def search(self, query: str, pages: int = 1) -> list[Listing]:
        query = clean_text(query)
        if not query:
            return []
        page_count = max(1, int(pages))
        found: dict[str, Listing] = {}
        for page in range(1, page_count + 1):
            params: dict[str, str | int] = {"q": query}
            if page > 1:
                params["page"] = page
            try:
                response = self._request(self.search_url, params=params)
            except RequestFailedError as exc:
                if page > 1 and exc.status_code == 404:
                    break
                raise
            for listing in self.parse_search_results(
                response.text,
                source_url=response.url,
            ):
                existing = found.get(listing.listing_id)
                found[listing.listing_id] = (
                    listing if existing is None else _fill_missing(existing, listing)
                )
        return list(found.values())

    def parse_search_results(
        self,
        html: str,
        source_url: str | None = None,
    ) -> list[Listing]:
        soup = BeautifulSoup(html or "", "html.parser")
        found: dict[str, Listing] = {}

        for anchor in soup.select("a[href]"):
            listing = self._listing_from_anchor(anchor, source_url)
            if listing is None:
                continue
            existing = found.get(listing.listing_id)
            found[listing.listing_id] = (
                listing if existing is None else _fill_missing(existing, listing)
            )

        for listing in self._listings_from_embedded_json(soup, source_url):
            existing = found.get(listing.listing_id)
            found[listing.listing_id] = (
                listing if existing is None else _fill_missing(existing, listing)
            )
        return list(found.values())

    parse_html = parse_search_results

    def enrich(self, listing: Listing) -> Listing:
        if listing.marketplace.casefold() != self.name:
            return listing
        try:
            response = self._request(listing.url)
            return self._parse_detail(response.text, listing, response.url)
        except (ScraperError, ValueError, TypeError, AttributeError):
            return listing

    def _listing_from_anchor(
        self,
        anchor: Tag,
        source_url: str | None,
    ) -> Listing | None:
        url = canonical_url(
            anchor.get("href"), source_url or self.base_url, strip_query=True
        )
        id_match = self._listing_id(url)
        if id_match is None:
            return None
        listing_id = id_match
        scope = _card_scope(anchor, self._id_pattern)

        title = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
        if not _useful_title(title):
            title_node = scope.select_one(
                "[itemprop='name'], .title, [class*='title'], h1, h2, h3, h4, h5, h6"
            )
            title = clean_text(
                title_node.get("title")
                if title_node and title_node.get("title")
                else title_node.get_text(" ", strip=True)
                if title_node
                else ""
            )
        if not _useful_title(title):
            image = scope.select_one("img[alt]")
            title = clean_text(image.get("alt")) if image else ""
        if not _useful_title(title):
            return None

        price_text, price_amount, currency = self._price_from_scope(scope)
        location = _location_from_scope(scope, title)
        image_url = _image_from(scope, source_url or self.base_url)

        return Listing(
            marketplace=self.name,
            listing_id=listing_id,
            title=title,
            price_text=price_text,
            price_amount=price_amount,
            currency=currency,
            url=url,
            location=location,
            image_url=image_url,
        )

    def _listing_id(self, url: str) -> str | None:
        if not url:
            return None
        parts = urlsplit(url)
        if parts.netloc.casefold() not in {"alo.bg", "www.alo.bg"}:
            return None
        match = self._id_pattern.search(url)
        return match.group(1) if match else None

    def _price_from_scope(
        self, scope: Tag
    ) -> tuple[str, Decimal | None, str | None]:
        price_node = scope.select_one(
            "[itemprop='price'], .price, [class*='price'], [class*='cena']"
        )
        candidates = []
        if price_node is not None:
            candidates.append(clean_text(price_node.get_text(" ", strip=True)))
        candidates.append(clean_text(scope.get_text(" ", strip=True)))
        for candidate in candidates:
            match = self._price_pattern.search(candidate)
            price_text = clean_text(match.group(1) if match else candidate)
            amount, currency = parse_price_text(price_text)
            if amount is not None:
                return price_text, amount, currency
        return "", None, None

    def _listings_from_embedded_json(
        self,
        soup: BeautifulSoup,
        source_url: str | None,
    ) -> list[Listing]:
        found: dict[str, Listing] = {}
        for document in iter_json_documents(soup):
            for value in walk_json(document):
                if not isinstance(value, dict):
                    continue
                candidate: Any = value.get("item", value)
                if not isinstance(candidate, dict):
                    continue
                raw_url = candidate.get("url") or candidate.get("@id")
                if not isinstance(raw_url, str):
                    continue
                url = canonical_url(
                    raw_url, source_url or self.base_url, strip_query=True
                )
                listing_id = self._listing_id(url)
                if listing_id is None:
                    continue
                title = clean_text(candidate.get("name") or candidate.get("title"))
                if not _useful_title(title):
                    continue
                price_text, amount, currency = _offer_price(candidate.get("offers"))
                image_url = _json_image(
                    candidate.get("image"), source_url or self.base_url
                )
                location = _json_location(candidate)
                found.setdefault(
                    listing_id,
                    Listing(
                        marketplace=self.name,
                        listing_id=listing_id,
                        title=title,
                        price_text=price_text,
                        price_amount=amount,
                        currency=currency,
                        url=url,
                        location=location,
                        image_url=image_url,
                        description=clean_text(candidate.get("description")) or None,
                    ),
                )
        return list(found.values())

    def _parse_detail(self, html: str, original: Listing, source_url: str) -> Listing:
        soup = BeautifulSoup(html or "", "html.parser")
        description_node = soup.select_one(
            "[itemprop='description'], .description, [class*='description'], "
            "[class*='additional-info']"
        )
        description = clean_text(
            description_node.get_text(" ", strip=True) if description_node else ""
        ) or None
        if not description:
            description_meta = soup.select_one(
                "meta[name='description'], meta[property='og:description']"
            )
            description = clean_text(
                description_meta.get("content") if description_meta else ""
            ) or None

        location_node = soup.select_one(
            "[itemprop='address'], .location, [class*='location'], "
            "[class*='address']"
        )
        location = clean_text(
            location_node.get("content")
            if location_node and location_node.get("content")
            else location_node.get_text(" ", strip=True)
            if location_node
            else ""
        ) or None
        image_meta = soup.select_one(
            "meta[property='og:image'], meta[name='twitter:image']"
        )
        image_url = (
            canonical_url(image_meta.get("content"), source_url or self.base_url)
            if image_meta and image_meta.get("content")
            else None
        )

        fallback = next(
            (
                candidate
                for candidate in self._listings_from_embedded_json(soup, source_url)
                if candidate.listing_id == original.listing_id
            ),
            None,
        )
        if fallback:
            description = description or fallback.description
            location = location or fallback.location
            image_url = image_url or fallback.image_url

        return replace(
            original,
            location=location or original.location,
            image_url=image_url or original.image_url,
            description=description or original.description,
        )


def _card_scope(anchor: Tag, id_pattern: re.Pattern[str]) -> Tag:
    """Choose the smallest ancestor that contains this card's displayed price."""

    current: Tag = anchor
    fallback: Tag = anchor
    for _ in range(8):
        parent = current.parent
        if not isinstance(parent, Tag):
            break
        current = parent
        text = clean_text(current.get_text(" ", strip=True))
        if len(text) > 6_000:
            break
        listing_ids = {
            match.group(1)
            for child in current.select("a[href]")
            if (match := id_pattern.search(str(child.get("href") or "")))
        }
        if len(listing_ids) <= 2:
            fallback = current
        if len(listing_ids) <= 2 and (
            "Цена" in text or re.search(r"\d\s*(?:€|EUR|BGN|лв\.?)", text, re.I)
        ):
            return current
    return fallback


def _useful_title(value: str) -> bool:
    text = clean_text(value)
    if len(text) < 3 or text.isdigit():
        return False
    return text.casefold() not in {
        "виж",
        "отвори",
        "снимка",
        "следваща",
        "предишна",
    }


def _location_from_scope(scope: Tag, title: str) -> str | None:
    node = scope.select_one(
        "[itemprop='address'], .location, [class*='location'], "
        "[class*='region'], [class*='town'], [class*='city']"
    )
    if node is not None:
        value = clean_text(node.get("content") or node.get_text(" ", strip=True))
        if value:
            return value

    raw_lines = [clean_text(line) for line in scope.get_text("\n").splitlines()]
    lines = [line for line in raw_lines if line]
    price_index = next(
        (index for index, line in enumerate(lines) if "Цена" in line), len(lines)
    )
    for line in reversed(lines[:price_index][-5:]):
        if line == title or len(line) > 160:
            continue
        if line.casefold() in {"днешна обява", "vip обява", "оцвети"}:
            continue
        if re.search(r"\d\s*(?:€|EUR|BGN|лв\.?)", line, re.I):
            continue
        category_match = re.search(
            r"Мобилни телефони,\s*GSM\s+(.+)$", line, re.IGNORECASE
        )
        if category_match:
            return clean_text(category_match.group(1)) or None
        if "»" in line:
            line = clean_text(line.rsplit("»", 1)[-1])
        if line and line != title:
            return line
    return None


def _image_from(scope: Tag, base_url: str) -> str | None:
    image = scope.select_one("img")
    if image is None:
        return None
    for attribute in (
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-url",
    ):
        value = image.get(attribute)
        if isinstance(value, str) and value and not value.startswith("data:"):
            return canonical_url(value, base_url)
    srcset = image.get("srcset") or image.get("data-srcset")
    if isinstance(srcset, str) and srcset.strip():
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        return canonical_url(first, base_url)
    return None


def _offer_price(value: Any) -> tuple[str, Decimal | None, str | None]:
    offers = value[0] if isinstance(value, list) and value else value
    if not isinstance(offers, dict):
        return "", None, None
    raw_price = offers.get("price") or offers.get("lowPrice")
    raw_currency = clean_text(offers.get("priceCurrency")).upper()
    if raw_price is None or raw_currency not in {"EUR", "BGN"}:
        return "", None, None
    try:
        amount = Decimal(str(raw_price).replace(",", "."))
    except InvalidOperation:
        return "", None, None
    symbol = "€" if raw_currency == "EUR" else "лв."
    return f"{amount} {symbol}", amount, raw_currency


def _json_image(value: Any, base_url: str) -> str | None:
    image = value
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    return canonical_url(image, base_url) if isinstance(image, str) else None


def _json_location(candidate: dict[str, Any]) -> str | None:
    location: Any = candidate.get("location") or candidate.get("address")
    if isinstance(location, str):
        return clean_text(location) or None
    if isinstance(location, dict):
        address = location.get("address") if "address" in location else location
        if isinstance(address, str):
            return clean_text(address) or None
        if isinstance(address, dict):
            pieces = [
                clean_text(address.get(key))
                for key in ("addressLocality", "addressRegion", "streetAddress")
            ]
            return ", ".join(dict.fromkeys(piece for piece in pieces if piece)) or None
    return None


def _fill_missing(primary: Listing, fallback: Listing) -> Listing:
    return replace(
        primary,
        title=primary.title or fallback.title,
        price_text=primary.price_text or fallback.price_text,
        price_amount=(
            primary.price_amount
            if primary.price_amount is not None
            else fallback.price_amount
        ),
        currency=primary.currency or fallback.currency,
        location=primary.location or fallback.location,
        image_url=primary.image_url or fallback.image_url,
        description=primary.description or fallback.description,
    )
