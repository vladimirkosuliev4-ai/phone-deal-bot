"""Phone/storage matching and conservative multilingual defect detection."""

from __future__ import annotations

import re

from utils.filters import contains_excluded, matching_specificity, normalize_text

from .domain import DefectDetection, DefectRule, DetectedIssue, PhoneProfile


def phone_matches_title(title: str, phone: PhoneProfile) -> bool:
    if _model_specificity(title, phone.aliases) < 0:
        return False
    return not _matches_compact_or_spaced(title, phone.title_exclude_keywords)


def phone_specificity(title: str, phone: PhoneProfile) -> int:
    if _matches_compact_or_spaced(title, phone.title_exclude_keywords):
        return -1
    return _model_specificity(title, phone.aliases)


def is_accessory_title(title: str, accessory_keywords: tuple[str, ...]) -> bool:
    return contains_excluded(title, accessory_keywords)


def parse_storage_gb(text: str, allowed: tuple[int, ...] = ()) -> int | None:
    """Extract phone storage while avoiding RAM and battery-health numbers."""

    folded = text.casefold().replace("терабайта", "tb").replace("терабайт", "tb")
    candidates: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d{2,4})\s*(?:gb|гб)\b", folded):
        candidates.append(int(match.group(1)))
    for match in re.finditer(r"(?<![\d.])(1|2)\s*(?:tb|тб)\b", folded):
        candidates.append(int(match.group(1)) * 1024)
    if allowed:
        allowed_set = set(allowed)
        valid = [value for value in candidates if value in allowed_set]
        return max(valid, default=None)
    plausible = [value for value in candidates if value in {32, 64, 128, 256, 512, 1024, 2048}]
    return max(plausible, default=None)


def detect_defects(text: str, rules: tuple[DefectRule, ...]) -> DefectDetection:
    normalized = normalize_text(text)
    found: list[DetectedIssue] = []
    for rule in rules:
        searchable = _remove_safe_phrases(normalized, rule.safe_keywords)
        if contains_excluded(searchable, rule.keywords):
            found.append(DetectedIssue(rule.code, rule.label, rule.category))

    battery_rule = next((rule for rule in rules if rule.code == "battery"), None)
    if battery_rule is not None and not any(issue.code == "battery" for issue in found):
        folded = text.casefold()
        health_values = [
            int(match.group(1))
            for pattern in (
                r"(?:battery\s*health|\bbh\b|здраве\s*(?:на\s*)?батерия(?:та)?)\D{0,12}(\d{2})\s*%",
                r"(\d{2})\s*%\s*(?:battery|батерия)",
            )
            for match in re.finditer(pattern, folded)
        ]
        if health_values and min(health_values) < 80:
            found.append(
                DetectedIssue(battery_rule.code, battery_rule.label, battery_rule.category)
            )

    # A vague "broken/for repair" marker adds useful caution only when no
    # specific defect was detected.
    if any(issue.code != "unknown_fault" for issue in found):
        found = [issue for issue in found if issue.code != "unknown_fault"]

    found = list({issue.code: issue for issue in found}.values())
    unacceptable = any(issue.category == "unacceptable" for issue in found)
    high_risk = any(issue.category == "high_risk" for issue in found)
    repairable_count = sum(issue.category == "repairable" for issue in found)
    if unacceptable:
        risk = "UNACCEPTABLE"
    elif high_risk:
        risk = "HIGH"
    elif repairable_count > 1:
        risk = "MODERATE"
    else:
        risk = "LOW"
    return DefectDetection(
        issues=tuple(found),
        risk=risk,
        unacceptable=unacceptable,
        has_damage=bool(found),
    )


def _remove_safe_phrases(normalized: str, safe_keywords: tuple[str, ...]) -> str:
    padded = f" {normalized} "
    for phrase in safe_keywords:
        needle = normalize_text(phrase)
        if needle:
            padded = padded.replace(f" {needle} ", " ")
    return " ".join(padded.split())


def _model_specificity(title: str, aliases: tuple[str, ...]) -> int:
    ordinary = matching_specificity(title, aliases)
    normalized_title = normalize_text(title)
    compact_title = normalized_title.replace(" ", "")
    compact_matches = [
        len(normalize_text(alias).replace(" ", ""))
        for alias in aliases
        if normalize_text(alias).replace(" ", "") in compact_title
    ]
    compact = max(compact_matches, default=-1)
    return max(ordinary, compact)


def _matches_compact_or_spaced(text: str, phrases: tuple[str, ...]) -> bool:
    if contains_excluded(text, phrases):
        return True
    compact_text = normalize_text(text).replace(" ", "")
    return any(
        compact_phrase in compact_text
        for phrase in phrases
        if (compact_phrase := normalize_text(phrase).replace(" ", ""))
    )
