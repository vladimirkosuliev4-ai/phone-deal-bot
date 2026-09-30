"""Comparable-listing cleanup and robust current-market baselines."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from scrapers.base import Listing
from utils.filters import normalize_text
from utils.prices import to_eur

from .detection import detect_defects, parse_storage_gb
from .domain import ComparableRecord, DefectDetection, MarketBaseline, RepairConfig


CENT = Decimal("0.01")


def comparable_record(
    listing: Listing,
    allowed_storage: tuple[int, ...],
    config: RepairConfig,
) -> ComparableRecord | None:
    if listing.price_amount is None or not listing.currency:
        return None
    try:
        price_eur = to_eur(listing.price_amount, listing.currency)
    except ValueError:
        return None
    if price_eur <= 0:
        return None
    text = " ".join(part for part in (listing.title, listing.description) if part)
    return ComparableRecord(
        marketplace=listing.marketplace,
        listing_id=listing.listing_id,
        title=listing.title,
        price_eur=price_eur,
        storage_gb=parse_storage_gb(text, allowed_storage),
        detection=detect_defects(text, config.rules),
    )


def deduplicate_records(records: list[ComparableRecord]) -> list[ComparableRecord]:
    """Remove likely cross-posts with the same normalized title and price."""

    result: list[ComparableRecord] = []
    seen: set[tuple[str, Decimal]] = set()
    for record in records:
        key = (normalize_text(record.title), record.price_eur)
        if key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def build_market_baseline(
    records: list[ComparableRecord],
    *,
    candidate_storage_gb: int | None,
    candidate_detection: DefectDetection,
    config: RepairConfig,
) -> MarketBaseline:
    records = [record for record in deduplicate_records(records) if not record.detection.unacceptable]
    if candidate_storage_gb is not None:
        exact = [record for record in records if record.storage_gb == candidate_storage_gb]
        exact_working = sum(not record.detection.has_damage for record in exact)
        if (
            not config.allow_mixed_storage_fallback
            or exact_working >= config.min_working_comparables
        ):
            scoped = exact
            storage_scope = f"exact {candidate_storage_gb}GB"
            exact_storage = True
        else:
            scoped = records
            storage_scope = f"mixed storage fallback for {candidate_storage_gb}GB"
            exact_storage = False
    else:
        scoped = records
        storage_scope = "mixed storage; listing capacity unknown"
        exact_storage = False

    working_records = [record for record in scoped if not record.detection.has_damage]
    broken_records = [
        record
        for record in scoped
        if record.detection.has_damage and not record.detection.unacceptable
    ]
    candidate_codes = candidate_detection.codes
    similar_broken = [
        record for record in broken_records if candidate_codes.intersection(record.detection.codes)
    ]
    if len(similar_broken) >= config.min_broken_comparables:
        selected_broken = similar_broken
        broken_scope = "same detected defect"
    else:
        selected_broken = broken_records
        broken_scope = "all non-locked broken examples"

    working_prices, working_removed = _remove_outliers(
        [record.price_eur for record in working_records]
    )
    broken_prices, broken_removed = _remove_outliers(
        [record.price_eur for record in selected_broken]
    )

    working_stats = (
        _price_stats(working_prices)
        if len(working_prices) >= config.min_working_comparables
        else (None, None, None)
    )
    broken_stats = (
        _price_stats(broken_prices)
        if len(broken_prices) >= config.min_broken_comparables
        else (None, None, None)
    )

    if working_stats[1] is None:
        confidence = "LOW"
    elif (
        exact_storage
        and len(working_prices) >= config.min_working_comparables * 2
        and broken_stats[1] is not None
    ):
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"
    if not exact_storage and confidence == "HIGH":
        confidence = "MEDIUM"

    return MarketBaseline(
        working_low_eur=working_stats[0],
        working_median_eur=working_stats[1],
        working_high_eur=working_stats[2],
        broken_low_eur=broken_stats[0],
        broken_median_eur=broken_stats[1],
        broken_high_eur=broken_stats[2],
        working_count=len(working_prices),
        broken_count=len(broken_prices),
        comparable_count=len(working_prices) + len(broken_prices),
        outliers_removed=working_removed + broken_removed,
        storage_scope=storage_scope,
        broken_scope=broken_scope,
        confidence=confidence,
    )


def _remove_outliers(values: list[Decimal]) -> tuple[list[Decimal], int]:
    ordered = sorted(values)
    if len(ordered) < 4:
        return ordered, 0
    q1 = _quantile(ordered, Decimal("0.25"))
    q3 = _quantile(ordered, Decimal("0.75"))
    spread = q3 - q1
    if spread == 0:
        # When most ads share a price, retain the central cluster while still
        # allowing a small amount of natural seller variation.
        tolerance = max(Decimal("20"), q1 * Decimal("0.15"))
        lower, upper = q1 - tolerance, q3 + tolerance
    else:
        lower, upper = q1 - spread * Decimal("1.5"), q3 + spread * Decimal("1.5")
    cleaned = [value for value in ordered if max(Decimal("0"), lower) <= value <= upper]
    return cleaned, len(ordered) - len(cleaned)


def _price_stats(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    ordered = sorted(values)
    return (
        _quantile(ordered, Decimal("0.25")).quantize(CENT, rounding=ROUND_HALF_UP),
        _quantile(ordered, Decimal("0.5")).quantize(CENT, rounding=ROUND_HALF_UP),
        _quantile(ordered, Decimal("0.75")).quantize(CENT, rounding=ROUND_HALF_UP),
    )


def _quantile(values: list[Decimal], fraction: Decimal) -> Decimal:
    if not values:
        raise ValueError("Cannot calculate a quantile from no values.")
    if len(values) == 1:
        return values[0]
    position = Decimal(len(values) - 1) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(values) - 1)
    weight = position - Decimal(lower_index)
    return values[lower_index] + (values[upper_index] - values[lower_index]) * weight
