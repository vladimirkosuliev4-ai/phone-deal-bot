from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repair_flip.config import load_repair_config
from repair_flip.detection import detect_defects, parse_storage_gb, phone_matches_title
from tests.repair_helpers import repair_config_data


class RepairDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "config.json"
        path.write_text(json.dumps(repair_config_data(), ensure_ascii=False), encoding="utf-8")
        self.config = load_repair_config(path)
        self.phone = self.config.phones[0]

    def test_matches_exact_phone_and_storage(self) -> None:
        self.assertTrue(phone_matches_title("Apple iPhone14ProMax 256GB", self.phone))
        self.assertEqual(256, parse_storage_gb("iPhone 14 Pro Max 256GB, 6GB RAM", self.phone.storage_gb))
        self.assertEqual(1024, parse_storage_gb("Phone 1 TB", (256, 512, 1024)))

    def test_detects_repairable_bulgarian_fault_and_drops_vague_marker(self) -> None:
        found = detect_defects("iPhone със счупен дисплей, продава се за ремонт", self.config.rules)
        self.assertEqual(frozenset({"screen"}), found.codes)
        self.assertEqual("LOW", found.risk)
        self.assertFalse(found.unacceptable)

    def test_detects_lock_but_respects_clean_safe_phrase(self) -> None:
        locked = detect_defects("iPhone broken screen and iCloud locked", self.config.rules)
        self.assertTrue(locked.unacceptable)
        self.assertIn("activation_lock", locked.codes)

        clean = detect_defects("iCloud clean, broken screen", self.config.rules)
        self.assertFalse(clean.unacceptable)
        self.assertEqual(frozenset({"screen"}), clean.codes)

    def test_detects_low_battery_health_from_number(self) -> None:
        low = detect_defects("iPhone 14 Pro Max, battery health 76%", self.config.rules)
        self.assertIn("battery", low.codes)
        healthy = detect_defects("iPhone 14 Pro Max, battery health 91%", self.config.rules)
        self.assertNotIn("battery", healthy.codes)

    def test_vague_fault_and_no_power_are_high_risk(self) -> None:
        vague = detect_defects("Телефонът е за ремонт", self.config.rules)
        self.assertEqual("HIGH", vague.risk)
        power = detect_defects("iPhone не се включва", self.config.rules)
        self.assertIn("no_power", power.codes)
        self.assertNotIn("unknown_fault", power.codes)


if __name__ == "__main__":
    unittest.main()
