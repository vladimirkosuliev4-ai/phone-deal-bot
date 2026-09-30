"""Small JSON state store used between short-lived GitHub Actions runs."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable


def _fresh_state() -> dict[str, Any]:
    return {
        "version": 1,
        "initialized_searches": {},
        "seen": {},
        "pending_notifications": [],
        "marketplace_health": {},
        "last_heartbeat": None,
    }


class StateError(RuntimeError):
    pass


class StateStore:
    def __init__(self, path: str | Path = "seen_listings.json") -> None:
        self.path = Path(path)
        self.data = _fresh_state()
        self._original = deepcopy(self.data)
        self.load()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.data = _fresh_state()
            self._original = deepcopy(self.data)
            return self.data
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Cannot read state file {self.path}: {exc}") from exc
        if not isinstance(loaded, dict) or loaded.get("version") != 1:
            raise StateError(f"Unsupported or invalid state file: {self.path}")
        fresh = _fresh_state()
        for key in fresh:
            if key in loaded:
                fresh[key] = loaded[key]
        if not isinstance(fresh["seen"], dict) or not isinstance(fresh["initialized_searches"], dict):
            raise StateError(f"Invalid state collections in {self.path}")
        if not isinstance(fresh["pending_notifications"], list) or not isinstance(
            fresh["marketplace_health"], dict
        ):
            raise StateError(f"Invalid state metadata in {self.path}")
        self.data = fresh
        self._normalize()
        self._original = deepcopy(self.data)
        return self.data

    def _normalize(self) -> None:
        self.data["seen"] = {
            str(marketplace): sorted({str(item) for item in items})
            for marketplace, items in self.data["seen"].items()
            if isinstance(items, list)
        }
        self.data["initialized_searches"] = {
            str(marketplace): sorted({str(item) for item in items})
            for marketplace, items in self.data["initialized_searches"].items()
            if isinstance(items, list)
        }

    @property
    def changed(self) -> bool:
        self._normalize()
        return self.data != self._original

    def seen_ids(self, marketplace: str) -> set[str]:
        return set(self.data["seen"].get(marketplace, []))

    def is_seen(self, marketplace: str, listing_id: str) -> bool:
        return str(listing_id) in self.seen_ids(marketplace)

    def mark_seen(self, marketplace: str, listing_id: str) -> None:
        items = self.seen_ids(marketplace)
        items.add(str(listing_id))
        self.data["seen"][marketplace] = sorted(items)

    def is_search_initialized(self, marketplace: str, fingerprint: str) -> bool:
        return fingerprint in set(self.data["initialized_searches"].get(marketplace, []))

    def mark_search_initialized(self, marketplace: str, fingerprint: str) -> None:
        values = set(self.data["initialized_searches"].get(marketplace, []))
        values.add(fingerprint)
        self.data["initialized_searches"][marketplace] = sorted(values)

    def baseline(self, marketplace: str, fingerprint: str, listing_ids: Iterable[str]) -> None:
        for listing_id in listing_ids:
            self.mark_seen(marketplace, listing_id)
        self.mark_search_initialized(marketplace, fingerprint)

    def pending(self) -> list[dict[str, Any]]:
        return list(self.data["pending_notifications"])

    def queue_notification(self, notification: dict[str, Any]) -> None:
        key = str(notification["key"])
        existing = {str(item.get("key")) for item in self.data["pending_notifications"]}
        if key not in existing:
            self.data["pending_notifications"].append(notification)

    def remove_notification(self, key: str) -> None:
        self.data["pending_notifications"] = [
            item for item in self.data["pending_notifications"] if str(item.get("key")) != key
        ]

    def record_marketplace_failure(self, marketplace: str, error: str, alert_after: int) -> bool:
        health = self.data["marketplace_health"].setdefault(
            marketplace, {"consecutive_failures": 0, "alerted": False, "last_error": None}
        )
        health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
        health["last_error"] = str(error)[:500]
        should_alert = health["consecutive_failures"] >= alert_after and not bool(health.get("alerted"))
        if should_alert:
            health["alerted"] = True
        return should_alert

    def record_marketplace_success(self, marketplace: str) -> bool:
        health = self.data["marketplace_health"].setdefault(
            marketplace, {"consecutive_failures": 0, "alerted": False, "last_error": None}
        )
        recovered = bool(health.get("alerted"))
        health.update({"consecutive_failures": 0, "alerted": False, "last_error": None})
        return recovered

    def touch_heartbeat(self, now: datetime | None = None, every_days: int = 30) -> bool:
        now = now or datetime.now(timezone.utc)
        raw = self.data.get("last_heartbeat")
        try:
            previous = datetime.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            previous = None
        if previous is None or now - previous >= timedelta(days=every_days):
            self.data["last_heartbeat"] = now.astimezone(timezone.utc).isoformat()
            return True
        return False

    def save(self) -> bool:
        self._normalize()
        if not self.changed:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temp_name: str | None = None
        try:
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, delete=False, newline="\n"
            ) as handle:
                handle.write(payload)
                temp_name = handle.name
            os.replace(temp_name, self.path)
        except OSError as exc:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise StateError(f"Cannot save state file {self.path}: {exc}") from exc
        self._original = deepcopy(self.data)
        return True

