"""Minimal Telegram Bot API client with image-to-text fallback."""

from __future__ import annotations

import html
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Any

import requests

from utils.config import MarketReference
from utils.filters import DealInfo
from utils.prices import format_money, format_price, to_eur

if TYPE_CHECKING:
    from scrapers.base import Listing


class TelegramError(RuntimeError):
    pass


@dataclass(frozen=True)
class Notification:
    key: str
    text: str
    image_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Notification":
        return cls(
            key=str(value["key"]),
            text=str(value["text"]),
            image_url=str(value["image_url"]) if value.get("image_url") else None,
        )


def _shorten(value: str, maximum: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= maximum else value[: maximum - 1].rstrip() + "…"


def build_deal_notification(
    listing: "Listing",
    search_name: str,
    max_price_eur: Decimal,
    deal: DealInfo,
    *,
    market_reference: MarketReference | None = None,
) -> Notification:
    title = html.escape(_shorten(listing.title, 240))
    phone = html.escape(search_name)
    marketplace = html.escape(listing.marketplace.upper())
    location = html.escape(_shorten(listing.location, 120)) if listing.location else None
    url = html.escape(listing.url, quote=True)
    price_line = format_price(listing.price_amount, listing.currency)

    lines = [
        f"<b>{deal.heading}</b>",
        f"📱 <b>{phone}</b>",
        f"📝 {title}",
        f"💰 <b>{html.escape(price_line)}</b>",
        f"🏪 {marketplace}",
    ]
    if location:
        lines.append(f"📍 {location}")
    lines.extend(_market_reference_lines(listing, market_reference))
    lines.extend(
        [
            f"My maximum: <b>{html.escape(format_money(max_price_eur, 'EUR'))}</b>",
            f"💸 <b>{html.escape(format_money(deal.savings_eur, 'EUR'))}</b> below my limit "
            f"({deal.discount_percent}%)",
            "",
            f'👉 <a href="{url}"><b>OPEN LISTING</b></a>',
        ]
    )
    return Notification(
        key=f"deal:{listing.marketplace}:{listing.listing_id}",
        text="\n".join(lines),
        image_url=listing.image_url,
    )


def _market_reference_lines(
    listing: "Listing", reference: MarketReference | None
) -> list[str]:
    if reference is None or listing.price_amount is None or not listing.currency:
        return []
    try:
        listing_eur = to_eur(listing.price_amount, listing.currency)
    except ValueError:
        return []

    median = reference.median_price_eur
    difference = (median - listing_eur).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    percent = ((abs(difference) / median) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )

    lines = [
        f"📊 Typical market asking price: "
        f"<b>{html.escape(format_price(median, 'EUR'))}</b>"
    ]
    if difference > 0:
        lines.append(
            f"💸 This listing is <b>{html.escape(format_money(difference, 'EUR'))}</b> "
            f"below market ({percent}%)"
        )
    elif difference < 0:
        lines.append(
            f"📈 This listing is <b>{html.escape(format_money(abs(difference), 'EUR'))}</b> "
            f"above market ({percent}%)"
        )
    else:
        lines.append("📊 This listing is at the typical market asking price")

    lines.append(
        f"📈 Estimated resale demand: <b>{html.escape(reference.resale_demand.upper())}</b>"
    )
    ad_word = "ad" if reference.sample_size == 1 else "ads"
    snapshot = (
        f"{reference.source}; {reference.sample_size} {ad_word}; "
        f"{reference.scope}; as of {reference.as_of}"
    )
    lines.append(f"🧾 Market snapshot: {html.escape(_shorten(snapshot, 220))}")
    return lines


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        session: requests.Session | None = None,
        timeout: int = 20,
    ) -> None:
        if not token.strip() or not chat_id.strip():
            raise TelegramError("Telegram token and chat ID must not be empty.")
        self._base_url = f"https://api.telegram.org/bot{token.strip()}"
        self.chat_id = chat_id.strip()
        self.session = session or requests.Session()
        self.timeout = timeout

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self._base_url}/{method}", data=payload, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise TelegramError(f"Telegram network error: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or not body.get("ok"):
            description = str(body.get("description", f"HTTP {response.status_code}"))
            raise TelegramError(f"Telegram rejected {method}: {description}")
        return body

    def send_message(self, text: str, *, disable_preview: bool = False) -> None:
        self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true" if disable_preview else "false",
            },
        )

    def send(self, notification: Notification) -> None:
        if notification.image_url and len(notification.text) <= 1024:
            try:
                self._post(
                    "sendPhoto",
                    {
                        "chat_id": self.chat_id,
                        "photo": notification.image_url,
                        "caption": notification.text,
                        "parse_mode": "HTML",
                    },
                )
                return
            except TelegramError:
                # Telegram sometimes cannot download marketplace CDN images. The
                # deal itself is more important, so retry it as a normal message.
                pass
        self.send_message(notification.text)

    def send_test(self) -> None:
        self.send_message(
            "✅ <b>Phone Deal Bot is connected!</b>\n"
            "Telegram secrets work. Existing listings will be remembered first; "
            "only later new deals will alert you."
        )

    def send_health_failure(self, marketplace: str, failures: int, error: str) -> None:
        self.send_message(
            f"⚠️ <b>{html.escape(marketplace.upper())} monitoring problem</b>\n"
            f"The marketplace failed {failures} times in a row. "
            "The bot will keep retrying automatically.\n"
            f"Reason: <code>{html.escape(_shorten(error, 300))}</code>",
            disable_preview=True,
        )

    def send_health_recovery(self, marketplace: str) -> None:
        self.send_message(
            f"✅ <b>{html.escape(marketplace.upper())} monitoring recovered</b>\n"
            "New listings are being checked again."
        )
