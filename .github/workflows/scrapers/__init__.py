"""Marketplace scraper registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests

from .alo import AloScraper
from .base import (
    AccessBlockedError,
    BaseScraper,
    Listing,
    RequestFailedError,
    ScraperError,
)
from .bazar import BazarScraper
from .olx import OlxScraper


def build_scrapers(
    config: Mapping[str, Any] | None = None,
    session: requests.Session | None = None,
) -> dict[str, BaseScraper]:
    """Build enabled scrapers from optional monitor/request configuration.

    Request settings may be placed at the config top level or in ``http``.
    This tolerance keeps the scraper layer independent from config validation.
    """

    config = config or {}
    nested = config.get("http")
    http = nested if isinstance(nested, Mapping) else config

    timeout = _number(
        http,
        ("timeout_seconds", "request_timeout_seconds", "timeout"),
        20.0,
    )
    retries = int(_number(http, ("retries", "request_retries"), 2))
    backoff = _number(http, ("backoff_seconds", "retry_backoff_seconds"), 1.0)
    delay = _number(
        http,
        ("delay_seconds", "request_delay_seconds", "polite_delay_seconds"),
        0.75,
    )
    common = {
        "timeout": timeout,
        "retries": retries,
        "backoff_seconds": backoff,
        "delay_seconds": delay,
    }

    # A supplied session is intentionally shared.  With no explicit session,
    # each marketplace gets its own cookie jar.
    available: dict[str, BaseScraper] = {
        "olx": OlxScraper(session=session, **common),
        "bazar": BazarScraper(session=session, **common),
        "alo": AloScraper(session=session, **common),
    }
    enabled = config.get("enabled_marketplaces")
    if not isinstance(enabled, (list, tuple, set)):
        return available
    names = {clean_name for value in enabled if (clean_name := str(value).casefold().strip())}
    return {name: scraper for name, scraper in available.items() if name in names}


def _number(config: Mapping[str, Any], keys: tuple[str, ...], default: float) -> float:
    for key in keys:
        value = config.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default
    return default


__all__ = [
    "AccessBlockedError",
    "AloScraper",
    "BaseScraper",
    "BazarScraper",
    "Listing",
    "OlxScraper",
    "RequestFailedError",
    "ScraperError",
    "build_scrapers",
]
