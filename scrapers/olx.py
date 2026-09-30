"""Public-page scraper for OLX.bg.

OLX may return HTTP 403 to hosted runners.  That response is surfaced as
``AccessBlockedError`` so the monitor can report marketplace health; this
module intentionally contains no access-control bypass.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag

from .base import (
    BaseScraper,
    Listing,
    ScraperError,
    canonical_url,
    clean_text,
    iter_json_documents,
    parse_price_text,
    stable_fallback_id,
    walk_json,
)


class OlxScraper(BaseScraper):
    """Read OLX result and detail pages using their ordinary public HTML."""

    name = "olx"
    base_url = "https://www.olx.bg/"
    _id_pattern = re.compile(r"-ID([A-Za-z0-9_-]+)\.html(?:$|[?#])", re.IGNORECASE)

    def search(self, query: str, pages: int = 1) -> list[Listing]:
        query = clean_text(query)
        if not query:
            return []
        page_count = max(1, int(pages))
        slug = re.sub(r"\s+", "-", query.casefold()).strip("-")
        # Keep the search inside the phone category.  The site-wide ``/ads``
        # route mixes unrelated categories and can hide new phone results.
        search_url = f"{self.base_url}elektronika/telefoni/q-{quote(slug, safe='-')}/"
        found: dict[str, Listing] = {}
        for page in range(1, page_count + 1):
            params: dict[str, str | int] = {"search[order]": "created_at:desc"}
            if page > 1:
                params["page"] = page
            response = self._request(search_url, params=params)
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

        cards = soup.select('[data-cy="l-card"], [data-testid="l-card"]')
        if cards:
            scopes: list[Tag] = cards
        else:
            scopes = []
            for anchor in soup.select(
                'a[href*="/d/obyava/"][href*="-ID"], '
                'a[href*="/d/ad/"][href*="-ID"]'
            ):
                scope = anchor.find_parent("article") or anchor.find_parent("li") or anchor
                if isinstance(scope, Tag) and scope not in scopes:
                    scopes.append(scope)

        for scope in scopes:
            listing = self._listing_from_scope(scope, source_url)
            if listing is not None:
                found.setdefault(listing.listing_id, listing)

        for listing in self._listings_from_embedded_json(soup, source_url):
            existing = found.get(listing.listing_id)
            if existing is None:
                found[listing.listing_id] = listing
            else:
                found[listing.listing_id] = _fill_missing(existing, listing)
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

    def _listing_from_scope(self, scope: Tag, source_url: str | None) -> Listing | None:
        anchor = scope.select_one(
            'a[href*="/d/obyava/"][href*="-ID"], '
            'a[href*="/d/ad/"][href*="-ID"]'
        )
        if anchor is None:
            anchor = scope.select_one('a[href*="-ID"][href$=".html"]')
        if anchor is None:
            return None
        url = canonical_url(anchor.get("href"), source_url or self.base_url, strip_query=True)
        if not url:
            return None
        id_match = self._id_pattern.search(url)
        data_id = clean_text(scope.get("data-id") or anchor.get("data-id"))
        listing_id = id_match.group(1) if id_match else data_id
        if not listing_id:
            listing_id = stable_fallback_id(url)

        title_node = scope.select_one(
            '[data-cy="ad-card-title"], [data-testid="ad-title"], h4, h6, h5, h3, h2'
        )
        title = clean_text(
            title_node.get_text(" ", strip=True) if title_node else anchor.get("title")
        )
        if not title:
            image = anchor.select_one("img[alt]")
            title = clean_text(image.get("alt")) if image else ""
        if not title:
            return None

        price_node = scope.select_one(
            '[data-testid="ad-price"], [data-testid="ad-price-container"], [data-cy="ad-price"]'
        )
        price_text = clean_text(
            price_node.get_text(" ", strip=True) if price_node else ""
        )
        price_amount, currency = parse_price_text(price_text)
        location_node = scope.select_one(
            '[data-testid="location-date"], [data-testid="location"], [data-cy="ad-location"]'
        )
        location = _location_without_date(
            location_node.get_text(" ", strip=True) if location_node else ""
        )
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
                raw_url = _first_string(candidate, "url", "urlPath", "href", "@id")
                if not raw_url or "-ID" not in raw_url or "/d/" not in raw_url:
                    continue
                url = canonical_url(raw_url, source_url or self.base_url, strip_query=True)
                id_match = self._id_pattern.search(url)
                raw_id = _first_string(candidate, "id", "adId", "listingId")
                listing_id = id_match.group(1) if id_match else raw_id
                if not listing_id:
                    listing_id = stable_fallback_id(url)
                title = clean_text(candidate.get("name") or candidate.get("title"))
                if not title:
                    continue
                price_text, price_amount, currency = _json_price(candidate)
                location = _json_location(candidate)
                image_url = _json_image(candidate, source_url or self.base_url)
                description = clean_text(candidate.get("description")) or None
                found.setdefault(
                    listing_id,
                    Listing(
                        marketplace=self.name,
                        listing_id=listing_id,
                        title=title,
                        price_text=price_text,
                        price_amount=price_amount,
                        currency=currency,
                        url=url,
                        location=location,
                        image_url=image_url,
                        description=description,
                    ),
                )
        return list(found.values())

    def _parse_detail(self, html: str, original: Listing, source_url: str) -> Listing:
        soup = BeautifulSoup(html or "", "html.parser")
        description_node = soup.select_one(
            '[data-cy="ad_description"], [data-testid="ad-description"], '
            '[data-testid="ad-description-container"], [itemprop="description"]'
        )
        description = clean_text(
            description_node.get_text(" ", strip=True) if description_node else ""
        ) or None
        location_node = soup.select_one(
            '[data-testid="mapAddress"], [data-testid="location-date"], '
            '[data-cy="ad_location"]'
        )
        location = _location_without_date(
            location_node.get_text(" ", strip=True) if location_node else ""
        )
        image_meta = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
        image_url = (
            canonical_url(image_meta.get("content"), source_url or self.base_url)
            if image_meta and image_meta.get("content")
            else None
        )

        fallback: Listing | None = None
        for candidate in self._listings_from_embedded_json(soup, source_url):
            if candidate.listing_id == original.listing_id:
                fallback = candidate
                break
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


def _location_without_date(value: str) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    # OLX normally renders "place - date" in a single card element.
    return clean_text(re.split(r"\s+-\s+", text, maxsplit=1)[0]) or None


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


def _first_string(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def _json_price(candidate: dict[str, Any]) -> tuple[str, Decimal | None, str | None]:
    price = candidate.get("offers") or candidate.get("price")
    if isinstance(price, list):
        price = price[0] if price else None
    if isinstance(price, str):
        amount, currency = parse_price_text(price)
        return clean_text(price), amount, currency
    if not isinstance(price, dict):
        return "", None, None

    for key in ("displayValue", "label", "formatted", "priceText"):
        display = price.get(key)
        if isinstance(display, str):
            amount, currency = parse_price_text(display)
            if amount is not None:
                return clean_text(display), amount, currency

    regular = price.get("regularPrice")
    if isinstance(regular, dict):
        for key in ("displayValue", "label", "formatted"):
            display = regular.get(key)
            if isinstance(display, str):
                amount, currency = parse_price_text(display)
                if amount is not None:
                    return clean_text(display), amount, currency

    raw_amount = price.get("price") or price.get("value")
    raw_currency = clean_text(price.get("priceCurrency") or price.get("currency")).upper()
    if raw_amount is None or raw_currency not in {"BGN", "EUR"}:
        return "", None, None
    try:
        amount = Decimal(str(raw_amount).replace(",", "."))
    except InvalidOperation:
        return "", None, None
    symbol = "€" if raw_currency == "EUR" else "лв."
    return f"{amount} {symbol}", amount, raw_currency


def _json_location(candidate: dict[str, Any]) -> str | None:
    location = candidate.get("location")
    if isinstance(location, str):
        return _location_without_date(location)
    if isinstance(location, dict):
        label = _first_string(location, "label", "name", "displayName")
        city = location.get("city")
        if isinstance(city, dict):
            city = _first_string(city, "name", "label")
        district = location.get("district")
        if isinstance(district, dict):
            district = _first_string(district, "name", "label")
        pieces = [clean_text(piece) for piece in (label or city, district) if clean_text(piece)]
        return ", ".join(dict.fromkeys(pieces)) or None
    return None


def _json_image(candidate: dict[str, Any], base_url: str) -> str | None:
    image: Any = candidate.get("image") or candidate.get("imageUrl")
    if isinstance(image, dict):
        image = _first_string(image, "url", "link", "src")
    if isinstance(image, list):
        image = image[0] if image else None
        if isinstance(image, dict):
            image = _first_string(image, "url", "link", "src")
    if not image:
        photos = candidate.get("photos") or candidate.get("images")
        if isinstance(photos, list) and photos:
            image = photos[0]
            if isinstance(image, dict):
                image = _first_string(image, "url", "link", "src")
    return canonical_url(image, base_url) if isinstance(image, str) else None


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
