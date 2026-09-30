# Broken Phone Repair Flip Bot

This is a **second, independent bot** inside the same repository. The ordinary phone-deal bot still uses `main.py`, `config.json`, `seen_listings.json`, and the **Phone deal monitor** workflow. This repair bot uses:

```text
repair_main.py
repair_config.json
repair_seen_listings.json
repair_flip/
.github/workflows/repair-monitor.yml
```

It checks the same free public Bazar.bg and ALO.bg pages, but sends alerts through a **different Telegram bot**. It never reads the ordinary deal bot's Telegram secrets.

## Create the separate Telegram bot

1. In Telegram, open the verified **@BotFather** account.
2. Send `/newbot`.
3. Choose a name such as `Repair Flip Bot`.
4. Choose a unique username ending in `bot`, for example `panev_repair_flip_bot`.
5. Copy the new token BotFather gives you. Do not paste it into a public file or chat.
6. Open the new bot, tap **Start**, and send it `hello`.
7. In Safari, open `https://api.telegram.org/botYOUR_NEW_TOKEN/getUpdates`, replacing `YOUR_NEW_TOKEN` with the new token.
8. Copy the number after `"chat":{"id":`. This is normally the same personal chat ID used by your other bot, but save it under the separate secret name below.
9. Clear that Safari tab/history because the token was present in the address.

In the GitHub repository, open **Settings → Secrets and variables → Actions** and add these two repository secrets:

```text
REPAIR_TELEGRAM_BOT_TOKEN
REPAIR_TELEGRAM_CHAT_ID
```

Paste the new repair bot's token into the first and your numeric chat ID into the second. Leave the original `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` unchanged; those continue to belong only to the ordinary deal bot.

## What happens every run

For every configured phone model, the bot:

1. Downloads the newest public search results from Bazar and ALO.
2. Keeps only the correct model and removes accessories and wrong variants.
3. Reads storage such as 128GB, 256GB, 512GB, or 1TB from the title/description.
4. Separates apparently working phones from broken phones using configurable Bulgarian and English defect phrases.
5. Rejects iCloud/Activation Lock, MDM, stolen/blacklisted, finance-lock, baseband/IMEI, severe-water, missing-parts, and motherboard listings.
6. Removes likely duplicate cross-posts and statistical price outliers.
7. Requires at least five exact-storage working comparables before creating a working-price median. It does not invent a market value when evidence is insufficient.
8. Prefers broken comparables with the same detected defect and otherwise clearly labels that the broken baseline mixes faults.
9. Estimates repair cost, resale price, total investment, profit, ROI, discounts, risk, confidence, and a 0–100 priority score.
10. Sends only configured classifications, ranked highest score first.

Current asking-price baselines are rebuilt during every run. They are not the dated static price references used by the ordinary bot.

## Telegram classifications

- **GOOD FLIP**: sufficient market evidence, repairable fault, strong expected profit/ROI, and low or moderate technical risk.
- **MAYBE**: possible opportunity, but margin, repair certainty, or comparable evidence is weaker.
- **HIGH RISK**: a potentially expensive or uncertain fault such as no power, water exposure, Face ID/Touch ID, or an unspecified problem.
- **SKIP**: prohibited risk, no detected repair opportunity, or economics below the configured minimum. SKIP is logged and remembered but is not sent by default.

When fewer than the required working comparables exist, the alert says:

```text
LOW CONFIDENCE – insufficient comparable listings
```

In that situation, resale price, profit, and ROI are deliberately left uncalculated.

## Run it now from GitHub

1. Open the repository on GitHub.
2. Open **Actions**.
3. Select **Repair flip monitor**.
4. Tap **Run workflow**.
5. Leave the branch on `main`.
6. Enable **Send a Repair Flip Bot Telegram setup message** for the first test.
7. Tap the green **Run workflow** button.

The setup message and all later repair alerts come from the new repair bot. The first run records existing listings without sending old advertisements. Later runs analyze only new listings. The repair workflow runs about every five minutes, staggered two minutes after the ordinary bot.

No paid API, database, server, VPS, paid scraper, or AI service is required.

## Edit repair costs and decision thresholds

Everything intended for normal adjustment is in `repair_config.json`.

Global repair ranges are under `repair_costs_eur`:

```json
"screen": {"low": 80, "expected": 140, "high": 240}
```

Individual phone profiles can override a cost because an OLED screen for a Pro Max or Ultra is not priced like a base model:

```json
"repair_cost_overrides": {
  "screen": {"low": 170, "expected": 235, "high": 315}
}
```

The bot adds `repair_contingency_percent` to the expected repair amount. `other_expected_costs_eur` covers configurable travel, supplies, delivery, or selling costs.

Important economic settings include:

- `minimum_viable_profit_eur`
- `minimum_viable_roi_percent`
- `good_flip_profit_eur`
- `good_flip_roi_percent`
- `good_flip_working_discount_percent`
- `max_repair_cost_percent`
- `minimum_notification_score`
- `max_notifications_per_run`

This bot has **no fixed maximum phone purchase price**. A listing qualifies from its estimated economics relative to current comparable prices.

## Add or remove phone models

Edit only the `phones` list in `repair_config.json`. Each profile supplies:

- `id`: stable lowercase identifier.
- `name`: Telegram display name.
- `query`: one marketplace search phrase.
- `aliases`: accepted title forms.
- `title_exclude_keywords`: variants that must not match this profile.
- `storage_gb`: allowed storage capacities.
- `liquidity`: `very_high`, `high`, `medium`, or `low` priority context.
- `repair_cost_overrides`: optional model-specific cost ranges.

A newly added or materially changed phone query receives its own quiet baseline.

## Edit defect language

Rules are under `defect_rules` in three groups:

- `unacceptable`
- `high_risk`
- `repairable`

Each rule has Bulgarian/English `keywords` and optional `safe_keywords`. A safe phrase prevents false detection; for example, `iCloud clean` and `без iCloud` neutralize the broad iCloud-lock warning.

The detector also recognizes compact seller model names such as `iPhone14ProMax`, and battery-health statements below 80% such as `BH 76%`.

## Important limitations

- Marketplace prices are **active asking prices**, not verified completed sales.
- The bot cannot confirm how fast one specific advertisement will sell. The configurable liquidity value is only prioritization context.
- Cosmetic similarity is inferred from text. This free implementation does not use paid image recognition or an AI API.
- Sellers can omit or misrepresent defects. A listing classified GOOD FLIP can still have hidden damage.
- Repair-cost defaults are planning estimates, not binding repair-shop quotes. Update them from your actual parts and labor costs.
- Always verify ownership, remove Find My/Google account locks in front of you, check IMEI/blacklist status, test every function, and inspect for water or board damage before paying.
- OLX remains disabled in automatic GitHub runs because its public pages reject those requests. The bot does not bypass access controls, CAPTCHAs, or marketplace protections.

## Optional local dry run

From the project folder:

```bash
python -m pip install -r requirements.txt
python repair_main.py --dry-run
```

A dry run performs public searches but does not send Telegram messages or change `repair_seen_listings.json`.

Run all tests with:

```bash
python -m unittest discover -s tests -v
```

## Cost

For a public GitHub repository, this version uses only free GitHub-hosted Actions runners, Python, free libraries, public marketplace pages, and the Telegram Bot API. Running cost remains **€0/month**.
