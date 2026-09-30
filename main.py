"""Phone Deal Bot command-line entry point."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from bot.telegram import Notification, TelegramError, TelegramNotifier, build_deal_notification
from scrapers import build_scrapers
from scrapers.base import Listing
from utils.config import AppConfig, ConfigError, SearchConfig, load_config
from utils.filters import calculate_deal, contains_excluded, matching_specificity, normalize_text
from utils.prices import to_eur
from utils.state import StateError, StateStore


@dataclass
class SearchResult:
    search: SearchConfig
    fingerprint: str
    was_initialized: bool
    listings: dict[str, Listing]
    complete: bool


def search_fingerprint(search: SearchConfig) -> str:
    """Stable identity unaffected by display, filter, or price-context edits."""

    normalized = sorted({normalize_text(keyword) for keyword in search.keywords})
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _notifier_from_environment(timeout: int) -> TelegramNotifier:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("TELEGRAM_BOT_TOKEN", token),
            ("TELEGRAM_CHAT_ID", chat_id),
        )
        if not value
    ]
    if missing:
        raise TelegramError(
            "Missing GitHub Secret/environment variable(s): " + ", ".join(missing)
        )
    return TelegramNotifier(token, chat_id, timeout=timeout)


def _deliver_pending(state: StateStore, notifier: TelegramNotifier) -> bool:
    failed = False
    for raw in state.pending():
        try:
            notification = Notification.from_dict(raw)
            notifier.send(notification)
        except (KeyError, TypeError, TelegramError) as exc:
            failed = True
            print(f"Pending Telegram notification could not be delivered: {exc}", file=sys.stderr)
        else:
            state.remove_notification(notification.key)
            print(f"Delivered pending notification {notification.key}.")
    return failed


def _select_search(listing: Listing, search_results: list[SearchResult], config: AppConfig) -> SearchConfig | None:
    choices: list[tuple[int, Decimal, int, SearchConfig]] = []
    for index, result in enumerate(search_results):
        if not result.complete or not result.was_initialized:
            continue
        specificity = matching_specificity(listing.title, result.search.keywords)
        if specificity < 0:
            continue
        if contains_excluded(listing.title, result.search.title_exclude_keywords):
            continue
        if listing.price_amount is None or not listing.currency:
            discount = Decimal("-Infinity")
        else:
            try:
                discount = result.search.max_price_eur - to_eur(
                    listing.price_amount, listing.currency
                )
            except ValueError:
                discount = Decimal("-Infinity")
        choices.append((specificity, discount, -index, result.search))
    if not choices:
        return None
    choices.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return choices[0][3]


def _process_marketplace(
    marketplace: str,
    scraper: Any,
    config: AppConfig,
    state: StateStore,
    notifier: TelegramNotifier | None,
    dry_run: bool,
) -> tuple[bool, bool, int]:
    """Return ``(had_any_success, delivery_failed, notifications_sent)``."""

    previous_seen = state.seen_ids(marketplace)
    runs: list[SearchResult] = []
    errors: list[str] = []

    for search in config.searches:
        fingerprint = search_fingerprint(search)
        merged: dict[str, Listing] = {}
        complete = True
        for query in dict.fromkeys(search.keywords):
            try:
                found = scraper.search(query, pages=config.pages_per_search)
            except Exception as exc:  # A broken marketplace must not stop the other one.
                complete = False
                message = f'{search.name} / "{query}": {type(exc).__name__}: {exc}'
                errors.append(message)
                print(f"[{marketplace}] {message}", file=sys.stderr)
                continue
            for listing in found:
                if listing.marketplace != marketplace:
                    print(
                        f"[{marketplace}] Ignoring result labeled as {listing.marketplace!r}.",
                        file=sys.stderr,
                    )
                    continue
                merged[str(listing.listing_id)] = listing
        runs.append(
            SearchResult(
                search=search,
                fingerprint=fingerprint,
                was_initialized=state.is_search_initialized(marketplace, fingerprint),
                listings=merged,
                complete=complete,
            )
        )

    successful_runs = [run for run in runs if run.complete]
    had_any_success = bool(successful_runs)
    delivery_failed = False
    sent = 0

    # Build candidates from the state as it existed at the beginning of this
    # marketplace run. Baselining a newly-added overlapping search therefore
    # cannot hide a genuinely new result from an already initialized search.
    candidates: dict[str, Listing] = {}
    for run in successful_runs:
        if run.was_initialized:
            for listing_id, listing in run.listings.items():
                if listing_id not in previous_seen:
                    candidates[listing_id] = listing

    for listing_id, card_listing in candidates.items():
        selected = _select_search(card_listing, runs, config)
        if selected is None:
            continue
        if card_listing.price_amount is None or not card_listing.currency:
            print(f"[{marketplace}] Skipping {listing_id}: no usable price.")
            continue
        if contains_excluded(
            card_listing.title,
            config.exclude_keywords + config.exclude_title_keywords,
        ):
            print(f"[{marketplace}] Excluded by title: {card_listing.title}")
            continue
        try:
            price_eur = to_eur(card_listing.price_amount, card_listing.currency)
        except ValueError as exc:
            print(f"[{marketplace}] Skipping {listing_id}: {exc}", file=sys.stderr)
            continue
        deal = calculate_deal(
            price_eur,
            selected.max_price_eur,
            config.good_deal_percent,
            config.great_deal_percent,
        )
        if deal is None:
            continue

        try:
            listing = scraper.enrich(card_listing)
        except Exception as exc:
            listing = card_listing
            print(
                f"[{marketplace}] Detail enrichment failed for {listing_id}; using card data: {exc}",
                file=sys.stderr,
            )
        searchable_detail = " ".join(part for part in (listing.title, listing.description) if part)
        if (
            contains_excluded(listing.title, config.exclude_title_keywords)
            or contains_excluded(listing.title, selected.title_exclude_keywords)
            or contains_excluded(searchable_detail, config.exclude_keywords)
        ):
            print(f"[{marketplace}] Excluded after detail check: {listing.title}")
            continue

        # Enrichment is allowed to correct the displayed price. Re-check it.
        if listing.price_amount is None or not listing.currency:
            continue
        try:
            enriched_eur = to_eur(listing.price_amount, listing.currency)
        except ValueError:
            continue
        deal = calculate_deal(
            enriched_eur,
            selected.max_price_eur,
            config.good_deal_percent,
            config.great_deal_percent,
        )
        if deal is None:
            continue

        notification = build_deal_notification(
            listing,
            selected.name,
            selected.max_price_eur,
            deal,
            market_reference=selected.market_reference,
        )
        if dry_run:
            print(
                f"[DRY RUN] {notification.key}: {deal.level} | {listing.title} | "
                f"€{enriched_eur} | {listing.url}"
            )
            continue
        assert notifier is not None
        if any(str(item.get("key")) == notification.key for item in state.pending()):
            continue
        try:
            notifier.send(notification)
        except TelegramError as exc:
            state.queue_notification(notification.to_dict())
            delivery_failed = True
            print(f"Telegram delivery failed; queued {notification.key}: {exc}", file=sys.stderr)
        else:
            sent += 1
            print(f"Sent {notification.key}: {listing.title}")

    # Only a fully successful search is safe to baseline. All cards encountered
    # successfully are remembered, even when too expensive or excluded.
    for run in successful_runs:
        for listing_id in run.listings:
            state.mark_seen(marketplace, listing_id)
        if not run.was_initialized:
            state.mark_search_initialized(marketplace, run.fingerprint)
            print(
                f"[{marketplace}] Baseline created for {run.search.name}: "
                f"{len(run.listings)} existing listing(s), no deal alerts."
            )

    if errors:
        error_text = " | ".join(errors)
        should_alert = state.record_marketplace_failure(
            marketplace, error_text, config.health_alert_after_failures
        )
        if should_alert and not dry_run and notifier is not None:
            health = state.data["marketplace_health"][marketplace]
            try:
                notifier.send_health_failure(
                    marketplace, int(health["consecutive_failures"]), error_text
                )
            except TelegramError as exc:
                # Permit another alert attempt next run.
                health["alerted"] = False
                delivery_failed = True
                print(f"Health warning delivery failed: {exc}", file=sys.stderr)
    else:
        recovered = state.record_marketplace_success(marketplace)
        if recovered and not dry_run and notifier is not None:
            try:
                notifier.send_health_recovery(marketplace)
            except TelegramError as exc:
                delivery_failed = True
                print(f"Recovery message delivery failed: {exc}", file=sys.stderr)

    return had_any_success, delivery_failed, sent


def run(
    config_path: str | Path = "config.json",
    state_path: str | Path = "seen_listings.json",
    *,
    dry_run: bool = False,
    send_test: bool = False,
    notifier: TelegramNotifier | None = None,
    scrapers: dict[str, Any] | None = None,
) -> int:
    config = load_config(config_path)
    state = StateStore(state_path)

    if not dry_run:
        notifier = notifier or _notifier_from_environment(config.request_timeout_seconds)
        if send_test:
            notifier.send_test()
            print("Telegram setup-test message sent successfully.")
        pending_failed = _deliver_pending(state, notifier)
    else:
        pending_failed = False
        print("Dry-run mode: Telegram and state-file writes are disabled.")

    scraper_map = scrapers or build_scrapers(asdict(config))
    any_success = False
    delivery_failed = pending_failed
    total_sent = 0
    try:
        for marketplace in config.enabled_marketplaces:
            scraper = scraper_map.get(marketplace)
            if scraper is None:
                print(f"No scraper is registered for {marketplace}.", file=sys.stderr)
                state.record_marketplace_failure(
                    marketplace, "No scraper registered", config.health_alert_after_failures
                )
                continue
            success, failed, sent = _process_marketplace(
                marketplace, scraper, config, state, notifier, dry_run
            )
            any_success = any_success or success
            delivery_failed = delivery_failed or failed
            total_sent += sent
        if not dry_run:
            state.touch_heartbeat(every_days=30)
    finally:
        if not dry_run:
            state.save()

    print(f"Monitoring finished: {total_sent} new deal notification(s) sent.")
    if delivery_failed:
        return 1
    if config.enabled_marketplaces and not any_success:
        print("Every enabled marketplace failed; see errors above.", file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor Bulgarian marketplaces for phone deals.")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--state", default="seen_listings.json", help="Path to persistent state JSON")
    parser.add_argument(
        "--send-test", action="store_true", help="Send a Telegram setup-test before monitoring"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and report decisions without Telegram messages or state writes",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return run(args.config, args.state, dry_run=args.dry_run, send_test=args.send_test)
    except (ConfigError, StateError, TelegramError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
