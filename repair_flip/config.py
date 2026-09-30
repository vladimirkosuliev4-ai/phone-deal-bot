"""Strict loader for the independent repair-flip bot configuration."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .domain import DefectRule, PhoneProfile, RepairConfig, RepairCost


class RepairConfigError(ValueError):
    pass


def _decimal(value: Any, field: str, *, minimum: Decimal = Decimal("0")) -> Decimal:
    if isinstance(value, bool):
        raise RepairConfigError(f'"{field}" must be a number.')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise RepairConfigError(f'"{field}" must be a number.') from None
    if not result.is_finite() or result < minimum:
        raise RepairConfigError(f'"{field}" must be a finite number of at least {minimum}.')
    return result


def _integer(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RepairConfigError(f'"{field}" must be a whole number from {low} to {high}.')
    return value


def _bounded_decimal(value: Any, field: str, low: int, high: int) -> Decimal:
    result = _decimal(value, field, minimum=Decimal(low))
    if result > Decimal(high):
        raise RepairConfigError(f'"{field}" must be from {low} to {high}.')
    return result


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a JSON list" if allow_empty else "a non-empty JSON list"
        raise RepairConfigError(f'"{field}" must be {qualifier} of text values.')
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise RepairConfigError(f'"{field}" must contain only non-empty text values.')
    return tuple(item.strip() for item in value)


def _cost(value: Any, field: str) -> RepairCost:
    if not isinstance(value, dict):
        raise RepairConfigError(f'"{field}" must be a JSON object.')
    low = _decimal(value.get("low"), f"{field}.low")
    expected = _decimal(value.get("expected"), f"{field}.expected")
    high = _decimal(value.get("high"), f"{field}.high")
    if not low <= expected <= high:
        raise RepairConfigError(f'"{field}" must satisfy low <= expected <= high.')
    return RepairCost(low, expected, high)


def _cost_map(value: Any, field: str) -> dict[str, RepairCost]:
    if not isinstance(value, dict):
        raise RepairConfigError(f'"{field}" must be a JSON object.')
    result: dict[str, RepairCost] = {}
    for raw_code, raw_cost in value.items():
        code = str(raw_code).strip().casefold()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", code):
            raise RepairConfigError(f'"{field}" contains an invalid defect code: {raw_code!r}.')
        result[code] = _cost(raw_cost, f"{field}.{code}")
    return result


def _rules(value: Any) -> tuple[DefectRule, ...]:
    if not isinstance(value, dict):
        raise RepairConfigError('"defect_rules" must be a JSON object.')
    result: list[DefectRule] = []
    seen: set[str] = set()
    for category in ("unacceptable", "high_risk", "repairable"):
        entries = value.get(category)
        if not isinstance(entries, list) or not entries:
            raise RepairConfigError(f'"defect_rules.{category}" must be a non-empty list.')
        for index, item in enumerate(entries):
            field = f"defect_rules.{category}[{index}]"
            if not isinstance(item, dict):
                raise RepairConfigError(f'"{field}" must be a JSON object.')
            code = item.get("code")
            label = item.get("label")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", code):
                raise RepairConfigError(f'"{field}.code" must be a lowercase identifier.')
            if code in seen:
                raise RepairConfigError(f'Duplicate defect rule code: "{code}".')
            if not isinstance(label, str) or not label.strip():
                raise RepairConfigError(f'"{field}.label" must be non-empty text.')
            keywords = _strings(item.get("keywords"), f"{field}.keywords")
            safe = _strings(item.get("safe_keywords", []), f"{field}.safe_keywords", allow_empty=True)
            result.append(
                DefectRule(
                    code=code,
                    label=label.strip(),
                    category=category,
                    keywords=keywords,
                    safe_keywords=safe,
                )
            )
            seen.add(code)
    return tuple(result)


def _phones(value: Any, repair_codes: set[str]) -> tuple[PhoneProfile, ...]:
    if not isinstance(value, list) or not value:
        raise RepairConfigError('"phones" must contain at least one phone profile.')
    result: list[PhoneProfile] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        field = f"phones[{index}]"
        if not isinstance(item, dict):
            raise RepairConfigError(f'"{field}" must be a JSON object.')
        phone_id = item.get("id")
        name = item.get("name")
        query = item.get("query")
        if not isinstance(phone_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", phone_id):
            raise RepairConfigError(f'"{field}.id" must be a lowercase identifier.')
        if phone_id in seen:
            raise RepairConfigError(f'Duplicate phone id: "{phone_id}".')
        for key, raw in (("name", name), ("query", query)):
            if not isinstance(raw, str) or not raw.strip():
                raise RepairConfigError(f'"{field}.{key}" must be non-empty text.')
        aliases = _strings(item.get("aliases"), f"{field}.aliases")
        excludes = _strings(
            item.get("title_exclude_keywords", []),
            f"{field}.title_exclude_keywords",
            allow_empty=True,
        )
        raw_storage = item.get("storage_gb")
        if not isinstance(raw_storage, list) or not raw_storage:
            raise RepairConfigError(f'"{field}.storage_gb" must be a non-empty list.')
        storage = tuple(
            sorted({_integer(number, f"{field}.storage_gb", 8, 4096) for number in raw_storage})
        )
        liquidity = item.get("liquidity")
        if not isinstance(liquidity, str) or liquidity.casefold().strip() not in {
            "very_high",
            "high",
            "medium",
            "low",
        }:
            raise RepairConfigError(
                f'"{field}.liquidity" must be very_high, high, medium, or low.'
            )
        overrides = _cost_map(
            item.get("repair_cost_overrides", {}), f"{field}.repair_cost_overrides"
        )
        unknown_codes = sorted(set(overrides) - repair_codes)
        if unknown_codes:
            raise RepairConfigError(
                f'"{field}.repair_cost_overrides" has unknown codes: {", ".join(unknown_codes)}.'
            )
        result.append(
            PhoneProfile(
                phone_id=phone_id,
                name=name.strip(),
                query=query.strip(),
                aliases=aliases,
                title_exclude_keywords=excludes,
                storage_gb=storage,
                liquidity=liquidity.casefold().strip(),
                repair_cost_overrides=overrides,
            )
        )
        seen.add(phone_id)
    return tuple(result)


def load_repair_config(path: str | Path = "repair_config.json") -> RepairConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RepairConfigError(f"Repair configuration file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise RepairConfigError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}."
        ) from None
    if not isinstance(raw, dict):
        raise RepairConfigError("The top level of repair_config.json must be an object.")

    marketplaces = _strings(raw.get("enabled_marketplaces"), "enabled_marketplaces")
    allowed_marketplaces = {"olx", "bazar", "alo"}
    unknown_marketplaces = sorted(set(marketplaces) - allowed_marketplaces)
    if unknown_marketplaces:
        raise RepairConfigError(
            f"Unknown marketplace(s): {', '.join(unknown_marketplaces)}."
        )
    if len(marketplaces) != len(set(marketplaces)):
        raise RepairConfigError('"enabled_marketplaces" contains duplicates.')

    rules = _rules(raw.get("defect_rules"))
    repair_costs = _cost_map(raw.get("repair_costs_eur"), "repair_costs_eur")
    cost_required = {rule.code for rule in rules if rule.category != "unacceptable"}
    missing_costs = sorted(cost_required - set(repair_costs))
    if missing_costs:
        raise RepairConfigError(
            '"repair_costs_eur" is missing estimates for: ' + ", ".join(missing_costs) + "."
        )
    phones = _phones(raw.get("phones"), set(repair_costs))

    notifications = tuple(
        item.upper()
        for item in _strings(raw.get("notify_classifications"), "notify_classifications")
    )
    valid_classes = {"GOOD FLIP", "MAYBE", "HIGH RISK", "SKIP"}
    if set(notifications) - valid_classes:
        raise RepairConfigError(
            '"notify_classifications" may contain GOOD FLIP, MAYBE, HIGH RISK, or SKIP.'
        )

    allow_fallback = raw.get("allow_mixed_storage_fallback", False)
    if not isinstance(allow_fallback, bool):
        raise RepairConfigError('"allow_mixed_storage_fallback" must be true or false.')
    delay = raw.get("request_delay_seconds", 1.0)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 30:
        raise RepairConfigError('"request_delay_seconds" must be a number from 0 to 30.')

    return RepairConfig(
        enabled_marketplaces=marketplaces,
        phones=phones,
        accessory_title_keywords=_strings(
            raw.get("accessory_title_keywords", []),
            "accessory_title_keywords",
            allow_empty=True,
        ),
        rules=rules,
        repair_costs=repair_costs,
        notify_classifications=notifications,
        pages_per_search=_integer(raw.get("pages_per_search", 1), "pages_per_search", 1, 5),
        request_timeout_seconds=_integer(
            raw.get("request_timeout_seconds", 20), "request_timeout_seconds", 5, 120
        ),
        request_retries=_integer(raw.get("request_retries", 2), "request_retries", 0, 5),
        request_delay_seconds=float(delay),
        health_alert_after_failures=_integer(
            raw.get("health_alert_after_failures", 3),
            "health_alert_after_failures",
            1,
            20,
        ),
        min_working_comparables=_integer(
            raw.get("min_working_comparables", 5), "min_working_comparables", 3, 50
        ),
        min_broken_comparables=_integer(
            raw.get("min_broken_comparables", 3), "min_broken_comparables", 2, 50
        ),
        allow_mixed_storage_fallback=allow_fallback,
        post_repair_resale_discount_percent=_bounded_decimal(
            raw.get("post_repair_resale_discount_percent", 5),
            "post_repair_resale_discount_percent",
            0,
            50,
        ),
        repair_contingency_percent=_bounded_decimal(
            raw.get("repair_contingency_percent", 15),
            "repair_contingency_percent",
            0,
            200,
        ),
        other_expected_costs_eur=_decimal(
            raw.get("other_expected_costs_eur", 15), "other_expected_costs_eur"
        ),
        minimum_viable_profit_eur=_decimal(
            raw.get("minimum_viable_profit_eur", 40), "minimum_viable_profit_eur"
        ),
        minimum_viable_roi_percent=_bounded_decimal(
            raw.get("minimum_viable_roi_percent", 15),
            "minimum_viable_roi_percent",
            0,
            1000,
        ),
        good_flip_profit_eur=_decimal(
            raw.get("good_flip_profit_eur", 90), "good_flip_profit_eur"
        ),
        good_flip_roi_percent=_bounded_decimal(
            raw.get("good_flip_roi_percent", 30),
            "good_flip_roi_percent",
            0,
            1000,
        ),
        good_flip_working_discount_percent=_bounded_decimal(
            raw.get("good_flip_working_discount_percent", 30),
            "good_flip_working_discount_percent",
            0,
            100,
        ),
        max_repair_cost_percent=_bounded_decimal(
            raw.get("max_repair_cost_percent", 45),
            "max_repair_cost_percent",
            0,
            100,
        ),
        minimum_notification_score=_bounded_decimal(
            raw.get("minimum_notification_score", 25),
            "minimum_notification_score",
            0,
            100,
        ),
        max_notifications_per_run=_integer(
            raw.get("max_notifications_per_run", 8), "max_notifications_per_run", 1, 30
        ),
    )
