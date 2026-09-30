"""Typed values shared by repair-flip configuration and analysis modules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RepairCost:
    low_eur: Decimal
    expected_eur: Decimal
    high_eur: Decimal


@dataclass(frozen=True, slots=True)
class DefectRule:
    code: str
    label: str
    category: str
    keywords: tuple[str, ...]
    safe_keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PhoneProfile:
    phone_id: str
    name: str
    query: str
    aliases: tuple[str, ...]
    title_exclude_keywords: tuple[str, ...]
    storage_gb: tuple[int, ...]
    liquidity: str
    repair_cost_overrides: dict[str, RepairCost]


@dataclass(frozen=True, slots=True)
class RepairConfig:
    enabled_marketplaces: tuple[str, ...]
    phones: tuple[PhoneProfile, ...]
    accessory_title_keywords: tuple[str, ...]
    rules: tuple[DefectRule, ...]
    repair_costs: dict[str, RepairCost]
    notify_classifications: tuple[str, ...]
    pages_per_search: int
    request_timeout_seconds: int
    request_retries: int
    request_delay_seconds: float
    health_alert_after_failures: int
    min_working_comparables: int
    min_broken_comparables: int
    allow_mixed_storage_fallback: bool
    post_repair_resale_discount_percent: Decimal
    repair_contingency_percent: Decimal
    other_expected_costs_eur: Decimal
    minimum_viable_profit_eur: Decimal
    minimum_viable_roi_percent: Decimal
    good_flip_profit_eur: Decimal
    good_flip_roi_percent: Decimal
    good_flip_working_discount_percent: Decimal
    max_repair_cost_percent: Decimal
    minimum_notification_score: Decimal
    max_notifications_per_run: int


@dataclass(frozen=True, slots=True)
class DetectedIssue:
    code: str
    label: str
    category: str


@dataclass(frozen=True, slots=True)
class DefectDetection:
    issues: tuple[DetectedIssue, ...]
    risk: str
    unacceptable: bool
    has_damage: bool

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(issue.label for issue in self.issues)

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(issue.code for issue in self.issues)


@dataclass(frozen=True, slots=True)
class ComparableRecord:
    marketplace: str
    listing_id: str
    title: str
    price_eur: Decimal
    storage_gb: int | None
    detection: DefectDetection


@dataclass(frozen=True, slots=True)
class MarketBaseline:
    working_low_eur: Decimal | None
    working_median_eur: Decimal | None
    working_high_eur: Decimal | None
    broken_low_eur: Decimal | None
    broken_median_eur: Decimal | None
    broken_high_eur: Decimal | None
    working_count: int
    broken_count: int
    comparable_count: int
    outliers_removed: int
    storage_scope: str
    broken_scope: str
    confidence: str


@dataclass(frozen=True, slots=True)
class RepairEstimate:
    low_eur: Decimal
    expected_eur: Decimal
    high_eur: Decimal
    uncertainty_percent: Decimal


@dataclass(frozen=True, slots=True)
class FlipAnalysis:
    classification: str
    score: Decimal
    risk: str
    confidence: str
    reason: str
    phone: PhoneProfile
    storage_gb: int | None
    detection: DefectDetection
    baseline: MarketBaseline
    purchase_price_eur: Decimal
    other_costs_eur: Decimal
    repair: RepairEstimate | None
    expected_resale_eur: Decimal | None
    total_investment_eur: Decimal | None
    estimated_profit_eur: Decimal | None
    roi_percent: Decimal | None
    working_discount_percent: Decimal | None
    broken_discount_percent: Decimal | None
    repair_cost_percent: Decimal | None
