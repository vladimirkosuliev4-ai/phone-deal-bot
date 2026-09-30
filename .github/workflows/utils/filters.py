"""Text matching, exclusion filtering, and deal classification."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


def normalize_text(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    # Sellers commonly omit spaces in model names (``iPhone15Pro``,
    # ``S24Ultra``).  Splitting letter/digit boundaries makes those forms
    # match the beginner-friendly phrases used in config.json.
    folded = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", folded)
    return " ".join(re.findall(r"[^\W_]+", folded, flags=re.UNICODE))


def _contains_phrase(normalized_haystack: str, normalized_needle: str) -> bool:
    if not normalized_needle:
        return False
    return f" {normalized_needle} " in f" {normalized_haystack} "


def matches_search(title: str, keywords: list[str] | tuple[str, ...]) -> bool:
    normalized_title = normalize_text(title)
    return any(_contains_phrase(normalized_title, normalize_text(keyword)) for keyword in keywords)


def matching_specificity(title: str, keywords: list[str] | tuple[str, ...]) -> int:
    normalized_title = normalize_text(title)
    matches = [
        len(normalize_text(keyword).split()) * 1_000 + len(normalize_text(keyword))
        for keyword in keywords
        if _contains_phrase(normalized_title, normalize_text(keyword))
    ]
    return max(matches, default=-1)


def contains_excluded(text: str, excluded_words: list[str] | tuple[str, ...]) -> bool:
    normalized_text = normalize_text(text)
    return any(
        _contains_phrase(normalized_text, normalize_text(word))
        for word in excluded_words
        if normalize_text(word)
    )


@dataclass(frozen=True)
class DealInfo:
    level: str
    emoji: str
    savings_eur: Decimal
    discount_percent: Decimal

    @property
    def heading(self) -> str:
        return f"{self.emoji} {self.level}"


def calculate_deal(
    price_eur: Decimal,
    max_price_eur: Decimal,
    good_percent: Decimal | int | str = 10,
    great_percent: Decimal | int | str = 20,
) -> DealInfo | None:
    price_eur = Decimal(price_eur)
    max_price_eur = Decimal(max_price_eur)
    if max_price_eur <= 0 or price_eur > max_price_eur:
        return None
    savings = (max_price_eur - price_eur).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    percent = ((savings / max_price_eur) * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    if percent >= Decimal(str(great_percent)):
        level, emoji = "GREAT DEAL", "🚨"
    elif percent >= Decimal(str(good_percent)):
        level, emoji = "GOOD DEAL", "🔥"
    else:
        level, emoji = "MATCH", "🟢"
    return DealInfo(level, emoji, savings, percent)


def deal_level(
    price_eur: Decimal,
    max_price_eur: Decimal,
    good_percent: Decimal | int | str = 10,
    great_percent: Decimal | int | str = 20,
) -> tuple[str, Decimal] | None:
    """Compatibility helper returning ``(level, savings_eur)``."""

    deal = calculate_deal(price_eur, max_price_eur, good_percent, great_percent)
    return (deal.level, deal.savings_eur) if deal else None
