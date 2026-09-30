"""Profit, ROI, risk, confidence, and priority classification."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .domain import (
    DefectDetection,
    FlipAnalysis,
    MarketBaseline,
    PhoneProfile,
    RepairConfig,
    RepairEstimate,
)


CENT = Decimal("0.01")
TENTH = Decimal("0.1")


def analyze_flip(
    *,
    phone: PhoneProfile,
    storage_gb: int | None,
    detection: DefectDetection,
    baseline: MarketBaseline,
    purchase_price_eur: Decimal,
    config: RepairConfig,
) -> FlipAnalysis:
    purchase_price_eur = _money(purchase_price_eur)
    repair = _repair_estimate(phone, detection, config)

    if detection.unacceptable:
        return _result(
            "SKIP",
            Decimal("0"),
            "Unacceptable account, ownership, IMEI, or board-level risk detected.",
            phone,
            storage_gb,
            detection,
            baseline,
            purchase_price_eur,
            config.other_expected_costs_eur,
            repair,
        )
    if not detection.has_damage:
        return _result(
            "SKIP",
            Decimal("0"),
            "No repair fault was detected; this is not a broken-phone opportunity.",
            phone,
            storage_gb,
            detection,
            baseline,
            purchase_price_eur,
            config.other_expected_costs_eur,
            repair,
        )

    working_median = baseline.working_median_eur
    if working_median is None:
        classification = "HIGH RISK" if detection.risk == "HIGH" else "MAYBE"
        score = _score_without_market(phone, detection, baseline)
        return _result(
            classification,
            score,
            "LOW CONFIDENCE – insufficient comparable working listings; no resale price or profit was invented.",
            phone,
            storage_gb,
            detection,
            baseline,
            purchase_price_eur,
            config.other_expected_costs_eur,
            repair,
        )

    expected_resale = _money(
        working_median
        * (Decimal("1") - config.post_repair_resale_discount_percent / Decimal("100"))
    )
    repair_expected = repair.expected_eur if repair else Decimal("0")
    total_investment = _money(
        purchase_price_eur + repair_expected + config.other_expected_costs_eur
    )
    profit = _money(expected_resale - total_investment)
    roi = _percent(profit, total_investment)
    working_discount = _percent(working_median - purchase_price_eur, working_median)
    broken_discount = (
        _percent(baseline.broken_median_eur - purchase_price_eur, baseline.broken_median_eur)
        if baseline.broken_median_eur is not None
        else None
    )
    repair_percent = _percent(repair_expected, expected_resale)

    if detection.risk == "HIGH" or repair_percent > config.max_repair_cost_percent:
        classification = "HIGH RISK"
        reason = (
            "The listing may be profitable, but the detected fault or repair-cost exposure can erase the margin."
        )
    elif (
        profit >= config.good_flip_profit_eur
        and roi >= config.good_flip_roi_percent
        and working_discount >= config.good_flip_working_discount_percent
        and baseline.confidence in {"HIGH", "MEDIUM"}
    ):
        classification = "GOOD FLIP"
        reason = (
            "The purchase is well below the working market median and the configured repair estimate leaves strong profit and ROI."
        )
    elif (
        profit < config.minimum_viable_profit_eur
        or roi < config.minimum_viable_roi_percent
    ):
        classification = "SKIP"
        reason = "Expected repair and selling costs leave too little profit or ROI."
    elif (
        baseline.broken_median_eur is not None
        and purchase_price_eur >= baseline.broken_median_eur * Decimal("0.95")
    ):
        classification = "SKIP"
        reason = "The seller is asking about the normal broken-phone market price, leaving little buying advantage."
    else:
        classification = "MAYBE"
        reason = "The numbers may work, but the margin, evidence, or repair certainty is not strong enough for GOOD FLIP."

    score = _priority_score(
        phone=phone,
        detection=detection,
        baseline=baseline,
        repair=repair,
        profit=profit,
        roi=roi,
        working_discount=working_discount,
        broken_discount=broken_discount,
        config=config,
    )
    return FlipAnalysis(
        classification=classification,
        score=score,
        risk=detection.risk,
        confidence=baseline.confidence,
        reason=reason,
        phone=phone,
        storage_gb=storage_gb,
        detection=detection,
        baseline=baseline,
        purchase_price_eur=purchase_price_eur,
        other_costs_eur=config.other_expected_costs_eur,
        repair=repair,
        expected_resale_eur=expected_resale,
        total_investment_eur=total_investment,
        estimated_profit_eur=profit,
        roi_percent=roi,
        working_discount_percent=working_discount,
        broken_discount_percent=broken_discount,
        repair_cost_percent=repair_percent,
    )


def _repair_estimate(
    phone: PhoneProfile,
    detection: DefectDetection,
    config: RepairConfig,
) -> RepairEstimate | None:
    costs = []
    for issue in detection.issues:
        if issue.category == "unacceptable":
            continue
        cost = phone.repair_cost_overrides.get(issue.code) or config.repair_costs.get(issue.code)
        if cost is not None:
            costs.append(cost)
    if not costs:
        return None
    low = _money(sum((cost.low_eur for cost in costs), Decimal("0")))
    raw_expected = sum((cost.expected_eur for cost in costs), Decimal("0"))
    expected = _money(
        raw_expected * (Decimal("1") + config.repair_contingency_percent / Decimal("100"))
    )
    high = _money(max(sum((cost.high_eur for cost in costs), Decimal("0")), expected))
    uncertainty = _percent(high - low, expected) if expected else Decimal("0")
    return RepairEstimate(low, expected, high, uncertainty)


def _priority_score(
    *,
    phone: PhoneProfile,
    detection: DefectDetection,
    baseline: MarketBaseline,
    repair: RepairEstimate | None,
    profit: Decimal,
    roi: Decimal,
    working_discount: Decimal,
    broken_discount: Decimal | None,
    config: RepairConfig,
) -> Decimal:
    score = Decimal("0")
    score += _scaled(profit, config.good_flip_profit_eur, Decimal("30"))
    score += _scaled(roi, config.good_flip_roi_percent, Decimal("25"))
    score += _scaled(
        working_discount, config.good_flip_working_discount_percent, Decimal("15")
    )
    if broken_discount is not None:
        score += _scaled(broken_discount, Decimal("30"), Decimal("10"))
    score += {"HIGH": Decimal("10"), "MEDIUM": Decimal("6"), "LOW": Decimal("2")}.get(
        baseline.confidence, Decimal("0")
    )
    score += {
        "very_high": Decimal("10"),
        "high": Decimal("8"),
        "medium": Decimal("5"),
        "low": Decimal("2"),
    }.get(phone.liquidity, Decimal("0"))
    score -= {"HIGH": Decimal("24"), "MODERATE": Decimal("8")}.get(
        detection.risk, Decimal("0")
    )
    if repair is not None:
        score -= min(Decimal("10"), repair.uncertainty_percent / Decimal("10"))
    return max(Decimal("0"), min(Decimal("100"), score)).quantize(
        TENTH, rounding=ROUND_HALF_UP
    )


def _score_without_market(
    phone: PhoneProfile, detection: DefectDetection, baseline: MarketBaseline
) -> Decimal:
    score = Decimal("20")
    score += {"very_high": 10, "high": 8, "medium": 5, "low": 2}.get(
        phone.liquidity, 0
    )
    score += min(Decimal("5"), Decimal(baseline.comparable_count))
    score -= {"HIGH": 18, "MODERATE": 6}.get(detection.risk, 0)
    return max(Decimal("0"), score).quantize(TENTH)


def _result(
    classification: str,
    score: Decimal,
    reason: str,
    phone: PhoneProfile,
    storage_gb: int | None,
    detection: DefectDetection,
    baseline: MarketBaseline,
    purchase_price_eur: Decimal,
    other_costs_eur: Decimal,
    repair: RepairEstimate | None,
) -> FlipAnalysis:
    return FlipAnalysis(
        classification=classification,
        score=score,
        risk=detection.risk,
        confidence=baseline.confidence,
        reason=reason,
        phone=phone,
        storage_gb=storage_gb,
        detection=detection,
        baseline=baseline,
        purchase_price_eur=purchase_price_eur,
        other_costs_eur=other_costs_eur,
        repair=repair,
        expected_resale_eur=None,
        total_investment_eur=None,
        estimated_profit_eur=None,
        roi_percent=None,
        working_discount_percent=None,
        broken_discount_percent=None,
        repair_cost_percent=None,
    )


def _scaled(value: Decimal, target: Decimal, maximum: Decimal) -> Decimal:
    if target <= 0 or value <= 0:
        return Decimal("0")
    return min(maximum, value / target * maximum)


def _percent(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return (numerator / denominator * Decimal("100")).quantize(
        TENTH, rounding=ROUND_HALF_UP
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)
