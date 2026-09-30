"""Tests for durable seen-listing, retry, health, and heartbeat state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from utils.state import StateError, StateStore


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "seen.json"

    def test_missing_file_starts_with_empty_versioned_state(self) -> None:
        state = StateStore(self.path)
        self.assertEqual(1, state.data["version"])
        self.assertEqual(set(), state.seen_ids("olx"))
        self.assertFalse(state.is_search_initialized("olx", "search-a"))
        self.assertFalse(self.path.exists())

    def test_baseline_marks_existing_ids_without_notifications(self) -> None:
        state = StateStore(self.path)
        self.assertEqual([], state.pending())
        state.baseline("olx", "iphone-15", ["ad-2", "ad-1", "ad-1"])

        self.assertTrue(state.is_search_initialized("olx", "iphone-15"))
        self.assertTrue(state.is_seen("olx", "ad-1"))
        self.assertTrue(state.is_seen("olx", "ad-2"))
        self.assertEqual([], state.pending())

    def test_seen_ids_are_separate_per_marketplace_and_persist(self) -> None:
        state = StateStore(self.path)
        state.mark_seen("olx", "same-id")
        self.assertTrue(state.is_seen("olx", "same-id"))
        self.assertFalse(state.is_seen("bazar", "same-id"))
        self.assertTrue(state.save())
        self.assertFalse(state.save(), "unchanged state should not rewrite the file")

        reloaded = StateStore(self.path)
        self.assertTrue(reloaded.is_seen("olx", "same-id"))
        self.assertFalse(reloaded.is_seen("bazar", "same-id"))

    def test_pending_notification_queue_is_deduplicated_and_persistent(self) -> None:
        state = StateStore(self.path)
        item = {"key": "deal:olx:123", "text": "deal", "image_url": None}
        state.queue_notification(item)
        state.queue_notification(dict(item))
        self.assertEqual([item], state.pending())
        state.save()

        reloaded = StateStore(self.path)
        self.assertEqual([item], reloaded.pending())
        reloaded.remove_notification(item["key"])
        self.assertEqual([], reloaded.pending())

    def test_health_alerts_once_then_reports_one_recovery(self) -> None:
        state = StateStore(self.path)
        self.assertFalse(state.record_marketplace_failure("olx", "403", alert_after=3))
        self.assertFalse(state.record_marketplace_failure("olx", "403", alert_after=3))
        self.assertTrue(state.record_marketplace_failure("olx", "challenge", alert_after=3))
        self.assertFalse(state.record_marketplace_failure("olx", "still blocked", alert_after=3))
        self.assertTrue(state.record_marketplace_success("olx"))
        self.assertFalse(state.record_marketplace_success("olx"))

    def test_heartbeat_updates_only_after_interval(self) -> None:
        state = StateStore(self.path)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(state.touch_heartbeat(start, every_days=30))
        self.assertFalse(state.touch_heartbeat(start + timedelta(days=29), every_days=30))
        self.assertTrue(state.touch_heartbeat(start + timedelta(days=30), every_days=30))

    def test_saved_json_is_utf8_and_deterministically_sorted(self) -> None:
        state = StateStore(self.path)
        state.mark_seen("bazar", "обява-2")
        state.mark_seen("bazar", "обява-1")
        state.save()
        raw = self.path.read_text(encoding="utf-8")
        decoded = json.loads(raw)
        self.assertEqual(["обява-1", "обява-2"], decoded["seen"]["bazar"])
        self.assertIn("обява", raw)
        self.assertTrue(raw.endswith("\n"))

    def test_invalid_json_has_beginner_readable_error(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(StateError, "Cannot read state file"):
            StateStore(self.path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
