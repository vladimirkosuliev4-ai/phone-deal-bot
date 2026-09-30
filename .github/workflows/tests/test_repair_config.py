from __future__ import annotations

from copy import deepcopy
import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from repair_flip.config import RepairConfigError, load_repair_config
from tests.repair_helpers import repair_config_data


class RepairConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "repair.json"

    def write(self, data: object) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_loads_typed_configuration(self) -> None:
        self.write(repair_config_data())
        config = load_repair_config(self.path)
        self.assertEqual(("bazar",), config.enabled_marketplaces)
        self.assertEqual(Decimal("80"), config.repair_costs["screen"].expected_eur)
        self.assertEqual("iphone_14_pro_max", config.phones[0].phone_id)
        self.assertEqual((128, 256, 512), config.phones[0].storage_gb)
        self.assertEqual("repairable", config.rules[-1].category)

    def test_shipped_repair_config_is_valid_and_separate(self) -> None:
        root = Path(__file__).parents[1]
        config = load_repair_config(root / "repair_config.json")
        self.assertGreaterEqual(len(config.phones), 10)
        self.assertNotEqual(root / "repair_config.json", root / "config.json")
        self.assertTrue((root / "repair_seen_listings.json").exists())

    def test_rejects_bad_cost_ranges_and_missing_rule_costs(self) -> None:
        data = repair_config_data()
        data["repair_costs_eur"]["screen"] = {"low": 100, "expected": 80, "high": 90}
        self.write(data)
        with self.assertRaisesRegex(RepairConfigError, "low <= expected <= high"):
            load_repair_config(self.path)

        data = repair_config_data()
        del data["repair_costs_eur"]["battery"]
        self.write(data)
        with self.assertRaisesRegex(RepairConfigError, "missing estimates"):
            load_repair_config(self.path)

    def test_rejects_duplicate_rule_and_phone_ids(self) -> None:
        data = repair_config_data()
        duplicate = deepcopy(data["defect_rules"]["repairable"][0])
        data["defect_rules"]["repairable"].append(duplicate)
        self.write(data)
        with self.assertRaisesRegex(RepairConfigError, "Duplicate defect rule"):
            load_repair_config(self.path)

        data = repair_config_data()
        data["phones"].append(deepcopy(data["phones"][0]))
        self.write(data)
        with self.assertRaisesRegex(RepairConfigError, "Duplicate phone id"):
            load_repair_config(self.path)


if __name__ == "__main__":
    unittest.main()
