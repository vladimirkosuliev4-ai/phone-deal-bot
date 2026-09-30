"""Public-page scraper for Bazar.bg."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re
from typing import Any

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
    stable_fallback_id,
    walk_json,
)


class BazarScraper(BaseScraper):
    """Read Bazar search and advertisement pages without an account."""

    name = "bazar"
    base_url = "https://bazar.bg/"
    search_url = "https://bazar.bg/obiavi"
    _id_pattern = re.compile(r"/obiava-(\d+)(?:/|$)", re.IGNORECASE)

    def search(self, query: str, pages: int = 1) -> list[Listing]:
        query = clean_text(query)
        if not query:
            return []
        page_count = max(1, int(pages))
        found: dict[str, Listing] = {}
        for page in range(1, page_count + 1):
            params: dict[str, str | int] = {"q": query, "sort": "date"}
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
                found.setdefault(listing.listing_id, listing)
        return list(found.values())

    def parse_search_results(
        self,
        html: str,
        source_url: str | None = None,
    ) -> list[Listing]:
        soup = BeautifulSoup(html or "", "html.parser")
        found: dict[str, Listing] = {}

        anchors = soup.select(
            'a.listItemLink[href*="/obiava-"], '
            'a[href*="bazar.bg/obiava-"], '
            'a[href^="/obiava-"]'
        )
        for anchor in anchors:
            listing = self._listing_from_anchor(anchor, source_url)
            if listing is not None:
                found.setdefault(listing.listing_id, listing)

        for listing in self._listings_from_embedded_json(soup, source_url):
            existing = found.get(listing.listing_id)
            if existing is None:
                found[listing.listing_id] = listing
            else:
                found[listing.listing_id] = _fill_missing(existing, listing)
        return list(found.values())

    # A short alias is convenient for external fixture/smoke tests.
    parse_html = parse_search_results

    def enrich(self, listing: Listing) -> Listing:
        if listing.marketplace.casefold() != self.name:
            return listing
        try:
            response = self._request(listing.url)
            detail = self._parse_detail(response.text, listing, response.url)
        except (ScraperError, ValueError, TypeError, AttributeError):
            return listing
        return detail

    def _listing_from_anchor(self, anchor: Tag, source_url: str | None) -> Listing | None:
        raw_href = anchor.get("href")
        url = canonical_url(raw_href, source_url or self.base_url, strip_query=True)
        if not url:
            return None
        id_match = self._id_pattern.search(url)
        data_id = clean_text(anchor.get("data-id"))
        listing_id = id_match.group(1) if id_match else data_id
        if not listing_id:
            listing_id = stable_fallback_id(url)

        container = anchor.find_parent(class_=re.compile(r"\blistItemContainer\b"))
        scope = container if isinstance(container, Tag) else anchor
        title = clean_text(anchor.get("title"))
        if not title:
            title_node = anchor.select_one(
                "span.title, h1.title, h2.title, h3.title, h4.title, h5.title, h6.title, "
                "h1, h2, h3, h4, h5, h6"
            )
            title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
        if not title:
            image = anchor.select_one("img[alt]")
            title = clean_text(image.get("alt")) if image else ""
        if not title:
            return None

        price_node = scope.select_one(".price, [itemprop='price'], [data-testid='price']")
        price_text = clean_text(
            price_node.get_text(" ", strip=True) if price_node else ""
        )
        price_amount, currency = parse_price_text(price_text)
        location_node = scope.select_one(".location, [itemprop='address']")
        location = clean_text(
            location_node.get_text(" ", strip=True) if location_node else ""
        ) or None
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
                if not isinstance(raw_url, str) or "/obiava-" not in raw_url:
                    continue
                url = canonical_url(raw_url, source_url or self.base_url, strip_query=True)
                id_match = self._id_pattern.search(url)
                listing_id = id_match.group(1) if id_match else stable_fallback_id(url)
                title = clean_text(candidate.get("name") or candidate.get("title"))
                if not title:
                    continue
                price_text, amount, currency = _offer_price(candidate.get("offers"))
                image = candidate.get("image")
                if isinstance(image, list):
                    image = next((item for item in image if isinstance(item, str)), None)
                image_url = (
                    canonical_url(image, source_url or self.base_url)
                    if isinstance(image, str)
                    else None
                )
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
                        image_url=image_url,
                        description=clean_text(candidate.get("description")) or None,
                    ),
                )
        return list(found.values())

    def _parse_detail(self, html: str, original: Listing, source_url: str) -> Listing:
        soup = BeautifulSoup(html or "", "html.parser")
        description_node = soup.select_one(
            '[itemprop="description"], .classifiedDescription, .adDescription'
        )
        description = clean_text(
            description_node.get_text(" ", strip=True) if description_node else ""
        ) or None
        location_node = soup.select_one("#see_on_map, [itemprop='address']")
        location = clean_text(
            location_node.get("title")
            if location_node and location_node.get("title")
            else location_node.get_text(" ", strip=True)
            if location_node
            else ""
        ) or None
        image_meta = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
        image_url = (
            canonical_url(image_meta.get("content"), source_url or self.base_url)
            if image_meta and image_meta.get("content")
            else None
        )

        fallback: Listing | None = None
        embedded = self._listings_from_embedded_json(soup, source_url)
        for candidate in embedded:
            if candidate.listing_id == original.listing_id:
                fallback = candidate
                break
        if fallback:
            description = description or fallback.description
            image_url = image_url or fallback.image_url

        return replace(
            original,
            location=location or original.location,
            image_url=image_url or original.image_url,
            description=description or original.description,
        )


def _image_from(scope: Tag, base_url: str) -> str | None:
    image = scope.select_one("img")
    if image is None:
        return None
    for attribute in ("src", "data-src", "data-original", "data-lazy-src"):
        value = image.get(attribute)
        if isinstance(value, str) and value and not value.startswith("data:"):
            return canonical_url(value, base_url)
    srcset = image.get("srcset") or image.get("data-srcset")
    if isinstance(srcset, str) and srcset.strip():
        return canonical_url(srcset.split(",", 1)[0].strip().split(" ", 1)[0], base_url)
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


def _fill_missing(primary: Listing, fallback: Listing) -> Listing:
    return replace(
        primary,
        price_text=primary.price_text or fallback.price_text,
        price_amount=(
            primary.price_amount if primary.price_amount is not None else fallback.price_amount
        ),
        currency=primary.currency or fallback.currency,
        location=primary.location or fallback.location,
        image_url=primary.image_url or fallback.image_url,
        description=primary.description or fallback.description,
    )
