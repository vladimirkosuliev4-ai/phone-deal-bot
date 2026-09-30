"""Small valid repair-bot configuration shared by offline tests."""


def repair_config_data() -> dict:
    return {
        "enabled_marketplaces": ["bazar"],
        "pages_per_search": 1,
        "request_timeout_seconds": 10,
        "request_retries": 0,
        "request_delay_seconds": 0,
        "health_alert_after_failures": 3,
        "min_working_comparables": 5,
        "min_broken_comparables": 3,
        "allow_mixed_storage_fallback": False,
        "post_repair_resale_discount_percent": 5,
        "repair_contingency_percent": 20,
        "other_expected_costs_eur": 15,
        "minimum_viable_profit_eur": 40,
        "minimum_viable_roi_percent": 15,
        "good_flip_profit_eur": 80,
        "good_flip_roi_percent": 25,
        "good_flip_working_discount_percent": 30,
        "max_repair_cost_percent": 45,
        "minimum_notification_score": 20,
        "max_notifications_per_run": 8,
        "notify_classifications": ["GOOD FLIP", "MAYBE", "HIGH RISK"],
        "accessory_title_keywords": ["case", "калъф", "дисплей за"],
        "repair_costs_eur": {
            "no_power": {"low": 80, "expected": 160, "high": 300},
            "unknown_fault": {"low": 50, "expected": 120, "high": 250},
            "screen": {"low": 50, "expected": 80, "high": 110},
            "battery": {"low": 30, "expected": 50, "high": 70},
        },
        "defect_rules": {
            "unacceptable": [
                {
                    "code": "activation_lock",
                    "label": "iCloud / Activation Lock",
                    "keywords": ["icloud", "activation lock"],
                    "safe_keywords": ["icloud clean", "без icloud"],
                }
            ],
            "high_risk": [
                {
                    "code": "no_power",
                    "label": "Does not power on",
                    "keywords": ["no power", "не се включва"],
                    "safe_keywords": ["включва се"],
                },
                {
                    "code": "unknown_fault",
                    "label": "Unclear fault",
                    "keywords": ["за ремонт", "счупен", "проблем"],
                    "safe_keywords": ["без проблем"],
                },
            ],
            "repairable": [
                {
                    "code": "screen",
                    "label": "Broken screen",
                    "keywords": ["broken screen", "счупен дисплей"],
                    "safe_keywords": ["screen works"],
                },
                {
                    "code": "battery",
                    "label": "Weak battery",
                    "keywords": ["weak battery", "слаба батерия"],
                    "safe_keywords": ["нова батерия"],
                },
            ],
        },
        "phones": [
            {
                "id": "iphone_14_pro_max",
                "name": "iPhone 14 Pro Max",
                "query": "iphone 14 pro max",
                "aliases": ["iphone 14 pro max"],
                "title_exclude_keywords": [],
                "storage_gb": [128, 256, 512],
                "liquidity": "very_high",
                "repair_cost_overrides": {
                    "screen": {"low": 50, "expected": 80, "high": 110}
                },
            }
        ],
    }
