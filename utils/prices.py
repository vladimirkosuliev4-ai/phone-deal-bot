"""Price parsing and exact EUR/BGN conversion helpers."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BGN_PER_EUR = Decimal("1.95583")
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def eur_to_bgn(value: Decimal | int | str) -> Decimal:
    """Convert EUR to BGN using the fixed official rate requested by the user."""

    return _money(Decimal(str(value)) * BGN_PER_EUR)


def bgn_to_eur(value: Decimal | int | str) -> Decimal:
    """Convert BGN to EUR using the fixed official rate requested by the user."""

    return _money(Decimal(str(value)) / BGN_PER_EUR)


def normalize_currency(value: str) -> str | None:
    folded = value.casefold()
    if "€" in folded or re.search(r"\beur\b", folded):
        return "EUR"
    if "лв" in folded or re.search(r"\bbgn\b", folded):
        return "BGN"
    return None


def _parse_number(raw: str) -> Decimal:
    cleaned = raw.replace("\u00a0", "").replace(" ", "").replace("'", "")
    cleaned = cleaned.rstrip(".,")
    if not cleaned:
        raise InvalidOperation

    comma_positions = [i for i, char in enumerate(cleaned) if char == ","]
    dot_positions = [i for i, char in enumerate(cleaned) if char == "."]

    if comma_positions and dot_positions:
        decimal_separator = "," if comma_positions[-1] > dot_positions[-1] else "."
        thousands_separator = "." if decimal_separator == "," else ","
        cleaned = cleaned.replace(thousands_separator, "")
        cleaned = cleaned.replace(decimal_separator, ".")
    elif comma_positions or dot_positions:
        separator = "," if comma_positions else "."
        pieces = cleaned.split(separator)
        last_len = len(pieces[-1])
        if len(pieces) == 2 and last_len in (1, 2):
            cleaned = pieces[0] + "." + pieces[1]
        elif len(pieces) > 2 and last_len in (1, 2):
            cleaned = "".join(pieces[:-1]) + "." + pieces[-1]
        else:
            cleaned = "".join(pieces)

    return Decimal(cleaned)


def parse_price(text: str | None) -> tuple[Decimal, str] | None:
    """Parse a localized price and return ``(amount, 'EUR'|'BGN')``.

    Values without an explicit supported currency are intentionally rejected.
    """

    if not text:
        return None
    currency = normalize_currency(text)
    if not currency:
        return None
    match = re.search(r"\d[\d\s\u00a0'.,]*", text)
    if not match:
        return None
    try:
        amount = _parse_number(match.group(0))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return _money(amount), currency


def to_eur(amount: Decimal, currency: str) -> Decimal:
    normalized = normalize_currency(currency) or currency.upper()
    if normalized == "EUR":
        return _money(amount)
    if normalized == "BGN":
        return bgn_to_eur(amount)
    raise ValueError(f"Unsupported currency: {currency}")


def format_money(value: Decimal, currency: str) -> str:
    value = _money(value)
    number = f"{value:,.2f}".replace(",", " ")
    if number.endswith(".00"):
        number = number[:-3]
    if currency.upper() == "EUR":
        return f"€{number}"
    if currency.upper() == "BGN":
        return f"{number} лв"
    return f"{number} {currency}"


def format_price(amount: Decimal, currency: str) -> str:
    """Format the source price and include its value in the other currency."""

    normalized = normalize_currency(currency) or currency.upper()
    if normalized == "EUR":
        return f"{format_money(amount, 'EUR')} / {format_money(eur_to_bgn(amount), 'BGN')}"
    if normalized == "BGN":
        return f"{format_money(amount, 'BGN')} / {format_money(bgn_to_eur(amount), 'EUR')}"
    raise ValueError(f"Unsupported currency: {currency}")

