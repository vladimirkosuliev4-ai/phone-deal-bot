"""Compact Telegram reports for repair-and-resale opportunities."""

from __future__ import annotations

import html

from bot.telegram import Notification
from scrapers.base import Listing
from utils.prices import format_money, format_price

from .domain import FlipAnalysis


CLASS_EMOJI = {
    "GOOD FLIP": "🔧💰",
    "MAYBE": "🟡",
    "HIGH RISK": "⚠️",
    "SKIP": "⛔",
}


def build_repair_notification(listing: Listing, analysis: FlipAnalysis) -> Notification:
    emoji = CLASS_EMOJI.get(analysis.classification, "🔧")
    model = analysis.phone.name
    if analysis.storage_gb is not None:
        storage = "1TB" if analysis.storage_gb == 1024 else f"{analysis.storage_gb}GB"
        model = f"{model} {storage}"
    issues = ", ".join(analysis.detection.labels) or "Unclear fault"
    baseline = analysis.baseline
    source_price = format_price(listing.price_amount, listing.currency)

    lines = [
        f"<b>{emoji} {html.escape(analysis.classification)} • {analysis.score}/100</b>",
        f"📱 <b>{html.escape(model)}</b>",
        f"📝 {html.escape(_shorten(listing.title, 150))}",
        f"💰 Listing: <b>{html.escape(source_price)}</b>",
        f"🛠 Issue: <b>{html.escape(_shorten(issues, 130))}</b>",
    ]

    if baseline.working_median_eur is None:
        lines.append("⚠️ <b>LOW CONFIDENCE – insufficient comparable listings</b>")
        lines.append("📊 Working market price: not calculated")
    else:
        working_range = " / ".join(
            html.escape(format_money(value, "EUR"))
            for value in (
                baseline.working_low_eur,
                baseline.working_median_eur,
                baseline.working_high_eur,
            )
            if value is not None
        )
        lines.append(f"📊 Working low / median / high: <b>{working_range}</b>")
        lines.append(
            "📊 Working median converted: "
            f"<b>{html.escape(format_price(baseline.working_median_eur, 'EUR'))}</b>"
        )
    if baseline.broken_median_eur is not None:
        lines.append(
            "🔩 Broken market median: "
            f"<b>{html.escape(format_money(baseline.broken_median_eur, 'EUR'))}</b>"
        )
    else:
        lines.append("🔩 Broken market median: insufficient similar ads")

    if analysis.repair is not None:
        repair = analysis.repair
        lines.append(
            f"🔧 Repair: <b>~{html.escape(format_money(repair.expected_eur, 'EUR'))}</b> "
            f"({html.escape(format_money(repair.low_eur, 'EUR'))}–"
            f"{html.escape(format_money(repair.high_eur, 'EUR'))})"
            f" • other: {html.escape(format_money(analysis.other_costs_eur, 'EUR'))}"
        )
    if analysis.expected_resale_eur is not None:
        lines.append(
            f"🎯 Expected resale: <b>{html.escape(format_money(analysis.expected_resale_eur, 'EUR'))}</b>"
            f" • invested: <b>{html.escape(format_money(analysis.total_investment_eur, 'EUR'))}</b>"
        )
        lines.append(
            f"💵 Profit: <b>{html.escape(format_money(analysis.estimated_profit_eur, 'EUR'))}</b>"
            f" • ROI: <b>{analysis.roi_percent}%</b>"
        )
        broken_discount = (
            f"{analysis.broken_discount_percent}%"
            if analysis.broken_discount_percent is not None
            else "n/a"
        )
        lines.append(
            f"📉 Discount vs working: <b>{analysis.working_discount_percent}%</b>"
            f" • vs broken: <b>{broken_discount}</b>"
        )

    lines.append(
        f"⚠️ Risk: <b>{html.escape(analysis.risk)}</b>"
        f" • Confidence: <b>{html.escape(analysis.confidence)}</b>"
        f" • Demand: <b>{html.escape(analysis.phone.liquidity.replace('_', ' ').upper())}</b>"
    )
    lines.append(
        f"🔎 Comparables: {baseline.working_count} working / {baseline.broken_count} broken"
        f" • {html.escape(baseline.storage_scope)}"
    )
    lines.append(f"💡 {html.escape(_shorten(analysis.reason, 190))}")
    lines.extend(
        [
            f"🏪 {html.escape(listing.marketplace.upper())}",
            "",
            f'👉 <a href="{html.escape(listing.url, quote=True)}"><b>OPEN LISTING</b></a>',
        ]
    )
    return Notification(
        key=f"repair:{listing.marketplace}:{listing.listing_id}",
        text="\n".join(lines),
        image_url=listing.image_url,
    )


def _shorten(value: str, maximum: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= maximum else compact[: maximum - 1].rstrip() + "…"
