# Bulgarian Phone Deal Bot

This free Python bot checks public listings on **Bazar.bg** and **ALO.bg**, remembers what it has already seen, and sends a Telegram alert when a newly found phone is at or below your price limit. It runs in GitHub Actions, so your iPhone and computer can be turned off.

The repository also contains a completely separate bot for evaluating broken phones for repair and resale. See [Broken Phone Repair Flip Bot](REPAIR_FLIP_README.md). It has its own Telegram bot, secrets, configuration, memory file, entry point, and GitHub workflow, and does not replace this ordinary deal bot.

The OLX adapter remains in the project, but OLX is paused in the default configuration because its public pages currently reject automated GitHub Actions requests. Facebook Marketplace is not integrated: Facebook does not provide a supported public search API for ordinary Marketplace listings, and Meta actively restricts unauthorized automated collection. The bot does not use login-cookie scraping, CAPTCHA bypasses, or proxies.

The first run is deliberately quiet: it records the listings that already exist without alerting you. Later runs alert only for new matching listings.

## Before you start

- Keep the GitHub repository **public**. Standard GitHub-hosted runners are free and unlimited for public repositories. A private repository has a limited monthly allowance and therefore is not a reliable €0 choice for a job that runs every five minutes. See [GitHub's runner documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job) and [Actions billing documentation](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
- GitHub Secrets stay hidden, but `config.json` and `seen_listings.json` are public in a public repository. They must never contain your Telegram token, chat ID, phone number, address, or other private information.
- Five minutes is GitHub's shortest schedule. Runs are **approximately** every five minutes, not guaranteed to start at the exact minute; GitHub can delay or occasionally drop scheduled work during heavy load. See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
- Bazar and ALO can change their public pages without warning. Each has a separate adapter, so a temporary failure on one marketplace does not stop the other.
- The OLX adapter is retained for future use but disabled by default. OLX can return HTTP 403 or show a challenge, and this project does not bypass those controls. Review the [OLX terms](https://www.olxgroup.com/terms-of-use-2/) before enabling it anywhere you are permitted to do so.
- Facebook Marketplace is not enabled because there is no supported public API for searching ordinary user listings. Meta describes technical measures against unauthorized automated scraping in its [scraping-protection explanation](https://about.fb.com/news/2021/04/how-we-combat-scraping/).
- The code, Python libraries, Telegram Bot API, and standard public-repository GitHub Actions runner cost €0. No paid API, server, database, proxy, or AI service is used.

## Set up everything from an iPhone

Safari works for the GitHub steps. If GitHub hides a menu, tap Safari's **aA** button and choose **Request Desktop Website**.

### 1. Create your Telegram bot

1. Open Telegram and search for the verified **@BotFather** account.
2. Send `/newbot`.
3. Enter any display name, for example `My Phone Deal Bot`.
4. Enter a unique username ending in `bot`, for example `my_sofia_phone_deals_bot`.
5. BotFather replies with an HTTP API token.

### 2. Save the Telegram bot token

The token looks similar to `123456789:AAExampleLettersAndNumbers`. Temporarily copy it somewhere private. Do **not** paste it into a project file, screenshot it, or send it to another person. You will put it directly into a GitHub Secret in step 5.

If a token is ever exposed, send `/revoke` to BotFather, select the bot, and replace the GitHub Secret with the new token.

### 3. Get your Telegram chat ID

1. Open the bot you just made, tap **Start**, and send it a message such as `hello`.
2. In Safari, open the following address after replacing `<TOKEN>` with the token from BotFather:

   ```text
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

3. Find a section resembling this in the result:

   ```json
   "chat": {"id": 123456789, "first_name": "Your name", "type": "private"}
   ```

4. Copy only the number after `"id":`. Include a leading minus sign if it has one. That number is your `TELEGRAM_CHAT_ID`.
5. Close the page and clear it from Safari history because its address contains the token.

If the response contains `"result":[]`, return to Telegram, send the bot another message, wait a few seconds, and reload. For a group, add the bot, send a message that mentions it, and use the negative ID from that group's `chat` object. Telegram documents this official method as [`getUpdates`](https://core.telegram.org/bots/api#getupdates).

### 4. Create the public GitHub repository and upload the project

1. Sign in at [github.com](https://github.com/) in Safari.
2. Tap **+**, then **New repository**.
3. Name it, for example, `phone-deal-bot`.
4. Select **Public**, then tap **Create repository**.
5. Upload every file from this project and keep the same names and folders. Files such as `.github/workflows/monitor.yml` must not be flattened or renamed.
6. Commit the upload to the default branch (`main`).

On iPhone, GitHub's upload picker may not upload a whole folder. Upload the root files with **Add file → Upload files**. For a nested file, use **Add file → Create new file**, type its full path (for example `scrapers/alo.py`), paste that file's contents, and commit it. Repeat until the repository matches the structure below:

```text
phone-deal-bot/
├── .github/
│   └── workflows/
│       └── monitor.yml
├── bot/
│   ├── __init__.py
│   └── telegram.py
├── scrapers/
│   ├── __init__.py
│   ├── alo.py
│   ├── base.py
│   ├── bazar.py
│   └── olx.py
├── tests/
│   ├── fixtures/
│   │   ├── alo_search.html
│   │   ├── alo_detail.html
│   │   ├── alo_jsonld.html
│   │   ├── bazar_detail.html
│   │   ├── bazar_jsonld.html
│   │   ├── bazar_search.html
│   │   ├── olx_ad_link.html
│   │   ├── olx_detail.html
│   │   ├── olx_jsonld.html
│   │   └── olx_search.html
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_main.py
│   ├── test_prices_filters.py
│   ├── test_scrapers.py
│   ├── test_scraper_transport.py
│   ├── test_state.py
│   └── test_telegram.py
├── utils/
│   ├── __init__.py
│   ├── config.py
│   ├── filters.py
│   ├── prices.py
│   └── state.py
├── config.json
├── main.py
├── README.md
├── requirements.txt
└── seen_listings.json
```

Do not upload a ZIP file as the project: GitHub Actions cannot run the files while they are still inside the ZIP.

### 5. Add the two GitHub Secrets

In your repository:

1. Tap **Settings**.
2. Open **Secrets and variables → Actions**.
3. Tap **New repository secret**.
4. Name it exactly `TELEGRAM_BOT_TOKEN` and paste the BotFather token as its value.
5. Add a second repository secret named exactly `TELEGRAM_CHAT_ID` and paste the numeric chat ID as its value.

Secret names are case-sensitive. Do not add quotes or spaces around either value.

### 6. Enable GitHub Actions and state saving

1. Open **Settings → Actions → General**.
2. Under **Actions permissions**, allow GitHub actions to run. The workflow uses only `actions/checkout` and `actions/setup-python` plus commands from this repository.
3. Under **Workflow permissions**, choose **Read and write permissions**, then tap **Save**. This allows the workflow to commit only its updated `seen_listings.json` state.
4. Open the repository's **Actions** tab. If GitHub shows **I understand my workflows, go ahead and enable them**, tap it.

The workflow itself also requests only `contents: write`; it does not request access to issues, pull requests, packages, or deployments.

### 7. Choose phones and maximum prices

The included `config.json` is already adjusted to watch the phone models with the strongest practical resale demand from the market research. Its alert ceilings are deliberately lower than the observed asking-price medians, so the bot focuses on unusually cheap listings instead of ordinary ones.

Open `config.json`, tap the pencil icon, and edit the values under `searches` whenever you want. Each entry uses:

- `name`: the friendly phone name shown in Telegram.
- `keywords`: one or more search phrases. Every phrase creates a marketplace request, so one accurate phrase is normally best.
- `title_exclude_keywords`: optional model variants that must not appear in the title. For example, the base iPhone 15 search rejects `Pro` and `Plus` titles.
- `max_price_eur`: the highest listing price that may alert you, without the `€` symbol.
- `market_reference`: optional dated research shown in the alert. It does not control whether the alert is sent.

Example:

```json
{
  "enabled_marketplaces": ["bazar", "alo"],
  "searches": [
    {
      "name": "iPhone 15 Pro",
      "keywords": ["iphone 15 pro"],
      "title_exclude_keywords": ["pro max"],
      "max_price_eur": 440,
      "market_reference": {
        "median_price_eur": 490,
        "sample_size": 45,
        "scope": "mixed storage",
        "as_of": "2026-08-25",
        "source": "OLX.bg cleaned active asking-price sample",
        "resale_demand": "very_high"
      }
    }
  ]
}
```

You can omit `market_reference` when adding a phone for which you do not have reliable research; its alerts will simply omit the market-comparison lines. If you include it, `resale_demand` must be `very_high`, `high`, `medium`, or `low`, and `as_of` must use `YYYY-MM-DD`.

The shipped snapshot is:

| Phone search | Alert ceiling | Typical asking-price median | Sample | Demand estimate |
|---|---:|---:|---:|---|
| iPhone 13 | €175 | €200 | 26 | Very high |
| iPhone 13 Pro | €270 | €300 | 28 | High |
| iPhone 13 Pro Max | €300 | €350 | 38 | High |
| iPhone 14 | €250 | €280 | 21 | Very high |
| iPhone 14 Pro | €330 | €374 | 36 | High |
| iPhone 14 Pro Max | €420 | €430 | 28 | High |
| iPhone 15 | €320 | €350 | 30 | Very high |
| iPhone 15 Pro | €440 | €490 | 45 | Very high |
| iPhone 15 Pro Max | €500 | €599 | 34 | Very high |
| iPhone 16 | €450 | €499 | 22 | Very high |
| iPhone 16 Pro | €580 | €615 | 26 | Very high |
| Galaxy S23 Ultra | €380 | €445 | 32 | High |
| Galaxy S24 Ultra | €450 | €500 | 35 | High |
| Galaxy S25 Ultra | €650 | €730 | about 35 | High |

These are cleaned **active asking-price** samples from OLX.bg researched on 25 August 2026, not completed-sale prices. Storage and condition still matter, and the values are a dated snapshot rather than a live price feed. The demand labels are practical resale estimates informed by the sample plus [European refurbished-phone rankings](https://www.recommerce-group.com/de/articles-en/re-index-2025), [European shipment data](https://omdia.tech.informa.com/pr/2026/feb/apple-and-honor-claim-record-market-shares-as-europes-smartphone-shipment-dips-1percent-in-2025), and [Bulgarian active-device share](https://gs.statcounter.com/vendor-market-share/mobile-device/bulgaria). Edit the reference values or remove an outdated `market_reference` block when the market changes.

Keep `"enabled_marketplaces": ["bazar", "alo"]` to monitor Bazar and ALO while OLX remains paused. Add or remove whole search objects as needed, keep commas between objects, and do not add a comma after the last object. The maximum is always configured in EUR; listings written in BGN are converted with exactly `1 EUR = 1.95583 BGN`.

Edit `exclude_title_keywords` for accessory-only titles such as cases, screens, cables, and empty boxes. Edit `exclude_keywords` for serious warnings that may appear anywhere in the title or description, such as broken, replica, MDM, bypass, iCloud lock, cracked glass, or a non-original display. Matching is case-insensitive. Keeping accessory words title-only means a real phone is not rejected merely because its description says a case or charger is included.

Deal labels use the configured percentage below your maximum:

- 🟢 **MATCH**: less than 10% below the limit.
- 🔥 **GOOD DEAL**: at least 10% but less than 20% below the limit.
- 🚨 **GREAT DEAL**: at least 20% below the limit.

The exact percentages can be changed with `good_deal_percent` and `great_deal_percent` in `config.json`.

After editing searches and any exclusions, tap **Commit changes** and commit to `main`. A newly added search (or a materially changed keyword set) gets its own quiet first-run baseline, so advertisements that already existed for that search are not sent as new deals.

### 8. Run the setup test

1. Open **Actions**.
2. Select **Phone deal monitor**.
3. Tap **Run workflow**.
4. Turn on **Send a Telegram setup-test message before monitoring**.
5. Tap the green **Run workflow** button.

Refresh after a few seconds and open the newest run. Every step should become green, and Telegram should receive a setup-test message. The same run records current marketplace listings as its baseline; it intentionally does not send deal alerts for those old listings.

If the run is red, tap it, open the failed step, and use the troubleshooting section below. GitHub hides secret values in logs.

### 9. Confirm automatic monitoring

Return to **Actions → Phone deal monitor** later. Scheduled runs should appear about every five minutes with `schedule` as their trigger. New qualifying listings will now create Telegram alerts; the same marketplace listing ID will not alert twice.

The bot commits changes to `seen_listings.json`, including a periodic heartbeat, so seeing commits made by `github-actions[bot]` is normal. Never manually clear that file unless you intentionally want to create a fresh baseline.

## What an alert contains

An alert includes the configured phone name, marketplace title, original and converted price, marketplace, location when available, your alert ceiling, amount saved, and a tappable direct link. When a `market_reference` is configured, it also shows the typical market asking price in EUR and BGN, exactly how far this listing is below or above that median, the estimated resale demand, and how many advertisements were checked in the dated sample.

The market snapshot is context, not a promise of the phone's condition, authenticity, battery health, or eventual resale price. Always inspect and test a phone before paying.

When the marketplace supplies an image, the bot tries to send it as a photo. If Telegram cannot download the image, the bot automatically sends the same alert as text instead.

Listings with no usable numeric price, listings above your limit, and listings containing an excluded word are skipped. Encountered listings are still remembered so changing a limit later does not turn an old advertisement into a “new” one.

## Optional dry run on a computer

A dry run reads and parses listings but does not send Telegram messages or change `seen_listings.json`:

```bash
python -m pip install -r requirements.txt
python main.py --dry-run --config config.json --state seen_listings.json
```

Run the automated tests with:

```bash
python -m unittest discover -s tests -v
```

## Troubleshooting

### No Telegram setup message

- Confirm that you tapped **Start** in the private chat with your bot.
- Confirm both GitHub Secret names are exact and their values have no quotes or spaces.
- A `401 Unauthorized` error means the bot token is wrong or was revoked.
- A `400 Bad Request: chat not found` error usually means the chat ID is wrong or the bot has never been started in that chat.
- For a group, confirm the bot is still a member and use that group's negative chat ID.

### `getUpdates` is empty

Send the bot a brand-new message and reload the URL. If you previously configured a webhook for this bot, `getUpdates` cannot work until that webhook is removed; using a fresh BotFather bot is usually simplest for a beginner.

### The state-save step says permission denied

Open **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and save. If the repository belongs to an organization, an organization policy can override this setting; use a personal public repository instead.

### There is no Run workflow button

Make sure `.github/workflows/monitor.yml` exists on the default branch, then reload the Actions page. GitHub only shows the button for a workflow on the default branch.

### Runs stopped appearing

- Check that the workflow is enabled under **Actions → Phone deal monitor → ⋯ → Enable workflow**.
- Scheduled workflows run only from the default branch.
- GitHub automatically disables scheduled workflows in a public repository after 60 days with no repository activity. The bot writes a monthly state heartbeat to avoid inactivity where possible, but check the Actions tab occasionally. GitHub explains how to [re-enable a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).
- GitHub can delay or drop an individual scheduled run during very high load. The next run normally catches the listing.

### Why OLX is paused

An OLX HTTP 403 or challenge is an access restriction, not a bad Telegram setup. OLX is therefore absent from the default `enabled_marketplaces` list, while its adapter remains in `scrapers/olx.py`. The bot deliberately does not solve CAPTCHAs, imitate a signed-in user, use proxies, or bypass OLX controls. Bazar and ALO continue to run normally.

### Why Facebook Marketplace is not included

Facebook does not offer this personal bot a supported public API for searching ordinary Marketplace listings. Its official integrations are for eligible partners supplying their own inventory, not for reading all user advertisements. Meta also says it detects and blocks unauthorized automated collection. Adding Facebook would require fragile login-session scraping and could expose your account, so it is intentionally not part of this €0 GitHub Actions setup. See Meta's [Marketplace partner information](https://www.facebook.com/help/463983701520800) and [scraping-protection explanation](https://about.fb.com/news/2021/04/how-we-combat-scraping/).

### Bazar or ALO reports a parsing error

A marketplace can temporarily fail or change its page layout. The bot reports repeated failures in Telegram, preserves the last known state, and continues checking the other enabled marketplace. A page-layout change requires an update to that site's separate file under `scrapers/`.

### No deal alerts after the first run

That is expected until a **new** listing appears. The first run only creates a baseline. Also check that the new listing is at or below `max_price_eur` and does not contain an `exclude_keywords` value.

### A marketplace changed its page

The run log will show which scraper could not parse results. The bot keeps the last state and will not invent listings. Page-layout changes require updating that marketplace's separate file under `scrapers/`; the other enabled marketplace continues independently.

## Privacy and delivery notes

- Telegram Bot API calls are sent directly to Telegram. Marketplace pages are fetched directly from GitHub's runner.
- Only the two encrypted GitHub Secrets contain Telegram credentials. State commits contain listing data, not credentials.
- Failed Telegram deliveries are kept for retry on a later run. A rare crash after Telegram accepts a message but before GitHub commits the new state can still cause one duplicate message; no free stateless scheduled system can guarantee a perfect exactly-once transaction across both services.
- Use this project gently for personal monitoring and comply with each marketplace's current rules. Do not increase the schedule or add aggressive parallel requests.

## License and responsibility

This project is provided for personal, educational use without a guarantee that a marketplace will remain accessible or keep the same HTML. You are responsible for complying with marketplace terms and local law.
