"""Independent broken-phone repair-and-resale monitor."""

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

from bot.telegram import Notification, TelegramError, TelegramNotifier
from repair_flip.analysis import analyze_flip
from repair_flip.config import RepairConfigError, load_repair_config
from repair_flip.detection import (
    detect_defects,
    is_accessory_title,
    parse_storage_gb,
    phone_matches_title,
    phone_specificity,
)
from repair_flip.domain import FlipAnalysis, PhoneProfile, RepairConfig
from repair_flip.market import build_market_baseline, comparable_record
from repair_flip.telegram import build_repair_notification
from scrapers import build_scrapers
from scrapers.base import Listing
from utils.filters import normalize_text
from utils.prices import to_eur
from utils.state import StateError, StateStore


@dataclass
class SearchBatch:
    marketplace: str
    phone: PhoneProfile
    fingerprint: str
    was_initialized: bool
    all_listing_ids: tuple[str, ...]
    relevant: tuple[Listing, ...]
    complete: bool


@dataclass
class AnalyzedListing:
    listing: Listing
    analysis: FlipAnalysis
    notification: Notification


def repair_search_fingerprint(phone: PhoneProfile) -> str:
    identity = {
        "id": phone.phone_id,
        "query": normalize_text(phone.query),
        "aliases": sorted(normalize_text(alias) for alias in phone.aliases),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _notifier_from_environment(timeout: int) -> TelegramNotifier:
    token = os.environ.get("REPAIR_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("REPAIR_TELEGRAM_CHAT_ID", "").strip()
    missing = [
        name
        for name, value in (
            ("REPAIR_TELEGRAM_BOT_TOKEN", token),
            ("REPAIR_TELEGRAM_CHAT_ID", chat_id),
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
            print(f"Pending repair alert could not be delivered: {exc}", file=sys.stderr)
        else:
            state.remove_notification(notification.key)
            print(f"Delivered pending repair alert {notification.key}.")
    return failed


def _collect_batches(
    config: RepairConfig,
    state: StateStore,
    scrapers: dict[str, Any],
) -> tuple[list[SearchBatch], dict[str, list[str]], bool]:
    batches: list[SearchBatch] = []
    errors: dict[str, list[str]] = {name: [] for name in config.enabled_marketplaces}
    any_success = False
    for marketplace in config.enabled_marketplaces:
        scraper = scrapers.get(marketplace)
        if scraper is None:
            errors[marketplace].append("No scraper registered")
            continue
        for phone in config.phones:
            fingerprint = repair_search_fingerprint(phone)
            try:
                found = scraper.search(phone.query, pages=config.pages_per_search)
            except Exception as exc:
                message = f"{phone.name}: {type(exc).__name__}: {exc}"
                errors[marketplace].append(message)
                print(f"[{marketplace}] {message}", file=sys.stderr)
                batches.append(
                    SearchBatch(
                        marketplace,
                        phone,
                        fingerprint,
                        state.is_search_initialized(marketplace, fingerprint),
                        (),
                        (),
                        False,
                    )
                )
                continue
            any_success = True
            all_ids: list[str] = []
            relevant: list[Listing] = []
            for listing in found:
                if listing.marketplace != marketplace:
                    continue
                if not phone_matches_title(listing.title, phone):
                    continue
                all_ids.append(str(listing.listing_id))
                if is_accessory_title(listing.title, config.accessory_title_keywords):
                    continue
                relevant.append(listing)
            batches.append(
                SearchBatch(
                    marketplace=marketplace,
                    phone=phone,
                    fingerprint=fingerprint,
                    was_initialized=state.is_search_initialized(marketplace, fingerprint),
                    all_listing_ids=tuple(dict.fromkeys(all_ids)),
                    relevant=tuple(relevant),
                    complete=True,
                )
            )
    return batches, errors, any_success


def _candidate_listings(
    batches: list[SearchBatch], state: StateStore
) -> dict[tuple[str, str], tuple[PhoneProfile, Listing]]:
    previous_seen = {
        batch.marketplace: state.seen_ids(batch.marketplace) for batch in batches
    }
    candidates: dict[tuple[str, str], tuple[PhoneProfile, Listing]] = {}
    for batch in batches:
        if not batch.complete or not batch.was_initialized:
            continue
        for listing in batch.relevant:
            key = (batch.marketplace, str(listing.listing_id))
            if key[1] in previous_seen[batch.marketplace]:
                continue
            existing = candidates.get(key)
            if existing is None or phone_specificity(listing.title, batch.phone) > phone_specificity(
                listing.title, existing[0]
            ):
                candidates[key] = (batch.phone, listing)
    return candidates


def _records_by_phone(
    batches: list[SearchBatch], config: RepairConfig
) -> dict[str, list[Any]]:
    records: dict[str, list[Any]] = {phone.phone_id: [] for phone in config.phones}
    seen: set[tuple[str, str, str]] = set()
    for batch in batches:
        if not batch.complete:
            continue
        for listing in batch.relevant:
            key = (batch.phone.phone_id, listing.marketplace, str(listing.listing_id))
            if key in seen:
                continue
            seen.add(key)
            record = comparable_record(listing, batch.phone.storage_gb, config)
            if record is not None:
                records[batch.phone.phone_id].append(record)
    return records


def _analyze_candidates(
    candidates: dict[tuple[str, str], tuple[PhoneProfile, Listing]],
    records_by_phone: dict[str, list[Any]],
    config: RepairConfig,
    scrapers: dict[str, Any],
) -> list[AnalyzedListing]:
    analyzed: list[AnalyzedListing] = []
    for (marketplace, listing_id), (phone, card) in candidates.items():
        scraper = scrapers[marketplace]
        try:
            listing = scraper.enrich(card)
        except Exception as exc:
            listing = card
            print(
                f"[{marketplace}] Detail enrichment failed for {listing_id}: {exc}",
                file=sys.stderr,
            )
        if not phone_matches_title(listing.title, phone):
            print(f"[{marketplace}] Wrong model after detail check: {listing.title}")
            continue
        text = " ".join(part for part in (listing.title, listing.description) if part)
        detection = detect_defects(text, config.rules)
        if listing.price_amount is None or not listing.currency:
            print(f"[{marketplace}] Skipping {listing_id}: no usable price.")
            continue
        try:
            purchase_eur = to_eur(listing.price_amount, listing.currency)
        except ValueError as exc:
            print(f"[{marketplace}] Skipping {listing_id}: {exc}", file=sys.stderr)
            continue
        storage = parse_storage_gb(text, phone.storage_gb)
        records = [
            record
            for record in records_by_phone.get(phone.phone_id, [])
            if not (
                record.marketplace == marketplace and str(record.listing_id) == listing_id
            )
        ]
        baseline = build_market_baseline(
            records,
            candidate_storage_gb=storage,
            candidate_detection=detection,
            config=config,
        )
        analysis = analyze_flip(
            phone=phone,
            storage_gb=storage,
            detection=detection,
            baseline=baseline,
            purchase_price_eur=purchase_eur,
            config=config,
        )
        print(
            f"[{marketplace}] {analysis.classification} {analysis.score}/100 | "
            f"{listing.title} | {analysis.reason}"
        )
        analyzed.append(
            AnalyzedListing(
                listing=listing,
                analysis=analysis,
                notification=build_repair_notification(listing, analysis),
            )
        )
    return analyzed


def _send_ranked(
    analyzed: list[AnalyzedListing],
    config: RepairConfig,
    state: StateStore,
    notifier: TelegramNotifier | None,
    dry_run: bool,
) -> tuple[int, bool]:
    eligible = [
        item
        for item in analyzed
        if item.analysis.classification in config.notify_classifications
        and item.analysis.score >= config.minimum_notification_score
    ]
    eligible.sort(key=lambda item: item.analysis.score, reverse=True)
    eligible = eligible[: config.max_notifications_per_run]
    if dry_run:
        for item in eligible:
            print(
                f"[DRY RUN ALERT] {item.analysis.classification} {item.analysis.score}/100 | "
                f"{item.listing.url}"
            )
        return 0, False

    assert notifier is not None
    sent = 0
    failed = False
    pending_keys = {str(item.get("key")) for item in state.pending()}
    for item in eligible:
        if item.notification.key in pending_keys:
            continue
        try:
            notifier.send(item.notification)
        except TelegramError as exc:
            state.queue_notification(item.notification.to_dict())
            failed = True
            print(f"Telegram delivery failed; queued {item.notification.key}: {exc}", file=sys.stderr)
        else:
            sent += 1
            print(f"Sent repair alert {item.notification.key}.")
    return sent, failed


def _update_health(
    config: RepairConfig,
    errors: dict[str, list[str]],
    state: StateStore,
    notifier: TelegramNotifier | None,
    dry_run: bool,
) -> bool:
    failed = False
    for marketplace, marketplace_errors in errors.items():
        if marketplace_errors:
            error_text = " | ".join(marketplace_errors)
            should_alert = state.record_marketplace_failure(
                marketplace, error_text, config.health_alert_after_failures
            )
            if should_alert and not dry_run and notifier is not None:
                health = state.data["marketplace_health"][marketplace]
                try:
                    notifier.send_health_failure(
                        f"repair {marketplace}",
                        int(health["consecutive_failures"]),
                        error_text,
                    )
                except TelegramError as exc:
                    health["alerted"] = False
                    failed = True
                    print(f"Repair health warning failed: {exc}", file=sys.stderr)
        else:
            recovered = state.record_marketplace_success(marketplace)
            if recovered and not dry_run and notifier is not None:
                try:
                    notifier.send_health_recovery(f"repair {marketplace}")
                except TelegramError as exc:
                    failed = True
                    print(f"Repair recovery message failed: {exc}", file=sys.stderr)
    return failed


def run(
    config_path: str | Path = "repair_config.json",
    state_path: str | Path = "repair_seen_listings.json",
    *,
    dry_run: bool = False,
    send_test: bool = False,
    notifier: TelegramNotifier | None = None,
    scrapers: dict[str, Any] | None = None,
) -> int:
    config = load_repair_config(config_path)
    state = StateStore(state_path)
    if not dry_run:
        notifier = notifier or _notifier_from_environment(config.request_timeout_seconds)
        if send_test:
            notifier.send_message(
                "🔧 <b>Repair Flip Bot is connected!</b>\n"
                "It will baseline existing broken-phone listings first, then rank only new opportunities."
            )
        delivery_failed = _deliver_pending(state, notifier)
    else:
        delivery_failed = False
        print("Repair dry-run: Telegram and state-file writes are disabled.")

    scraper_map = scrapers or build_scrapers(asdict(config))
    batches, errors, any_success = _collect_batches(config, state, scraper_map)
    candidates = _candidate_listings(batches, state)
    records = _records_by_phone(batches, config)
    analyzed = _analyze_candidates(candidates, records, config, scraper_map)
    sent, send_failed = _send_ranked(
        analyzed, config, state, notifier, dry_run
    )
    delivery_failed = delivery_failed or send_failed
    delivery_failed = delivery_failed or _update_health(
        config, errors, state, notifier, dry_run
    )

    if not dry_run:
        for batch in batches:
            if not batch.complete:
                continue
            for listing_id in batch.all_listing_ids:
                state.mark_seen(batch.marketplace, listing_id)
            if not batch.was_initialized:
                state.mark_search_initialized(batch.marketplace, batch.fingerprint)
                print(
                    f"[{batch.marketplace}] Repair baseline for {batch.phone.name}: "
                    f"{len(batch.all_listing_ids)} existing result(s), no old alerts."
                )
        state.touch_heartbeat(every_days=30)
        state.save()

    print(
        f"Repair monitoring finished: {len(analyzed)} new listing(s) analyzed; "
        f"{sent} alert(s) sent."
    )
    if delivery_failed:
        return 1
    if config.enabled_marketplaces and not any_success:
        print("Every repair marketplace search failed; see errors above.", file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor broken phones for repair-and-resale opportunities."
    )
    parser.add_argument("--config", default="repair_config.json")
    parser.add_argument("--state", default="repair_seen_listings.json")
    parser.add_argument("--send-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        return run(
            args.config,
            args.state,
            dry_run=args.dry_run,
            send_test=args.send_test,
        )
    except (RepairConfigError, StateError, TelegramError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
