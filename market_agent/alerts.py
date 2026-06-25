from __future__ import annotations

import datetime
import logging
import signal
import sys
from dataclasses import dataclass

from market_agent.config import Settings
from market_agent.data import INDEX_SYMBOLS, latest_metric, latest_news, previous_ohlc
from market_agent.models import Metric
from market_agent.telegram import send_message
from market_agent.utils import format_table, format_trend, status_icon

LOGGER = logging.getLogger("market_agent.alerts")

BREAKING_INDEX_NAMES = ("Nifty 50", "Bank Nifty")


@dataclass(frozen=True)
class AlertCheck:
    lines: list[str]
    is_major: bool = False
    major_keys: list[str] | None = None
    metrics: dict[str, Metric] | None = None


def _metric_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _important_news_lines() -> tuple[list[str], list[str]]:
    important_terms = (
        "rbi", "sebi", "nse", "bse", "rate cut", "rate hike", "policy",
        "crash", "plunge", "surge", "war", "sanction", "default", "fraud",
        "ban", "merger", "acquisition", "results", "profit warning", "guidance",
    )
    lines: list[str] = []
    keys: list[str] = []
    for item in latest_news(8):
        title = item.title.strip()
        title_lower = title.lower()
        if any(term in title_lower for term in important_terms):
            lines.append(f"Major news: {title} - {item.source}")
            keys.append(f"news:{title_lower}")
            if len(lines) >= 2:
                break
    return lines, keys


def _get_latest_index(name: str, symbol: str, nse_indices: dict) -> Metric:
    nse_key = None
    if name == "Nifty 50":
        nse_key = "NIFTY 50"
    elif name == "Bank Nifty":
        nse_key = "NIFTY BANK"
    elif name == "India VIX":
        nse_key = "INDIA VIX"

    if nse_key and nse_indices and nse_key in nse_indices:
        from market_agent.data import _fmt
        val = nse_indices[nse_key]["last"]
        pct = nse_indices[nse_key]["percentChange"]
        return Metric(
            name,
            _fmt(float(val)) if val is not None else None,
            "Live",
            "NSE India",
            change_pct=float(pct) if pct is not None else None,
        )
    return latest_metric(name, symbol)


def check_breaking_alerts(settings: Settings) -> AlertCheck:
    from market_agent.data import (
        fii_dii_activity, gift_nifty, market_breadth, nse_live_indices, us_futures,
    )
    nse_indices = nse_live_indices()

    alerts: list[str] = []
    is_major = False
    major_keys: list[str] = []
    metrics_map: dict[str, Metric] = {}
    major_index_move = max(settings.index_break_percent * 2, 1.2)

    for name in BREAKING_INDEX_NAMES:
        latest = _get_latest_index(name, INDEX_SYMBOLS[name], nse_indices)
        metrics_map[name] = latest

        value = _metric_float(latest.value)
        if value is None:
            continue

        if latest.change_pct is not None:
            pct_from_close = latest.change_pct
        else:
            ohlc = previous_ohlc(name, INDEX_SYMBOLS[name])
            raw = ohlc.get("_raw")
            if not raw:
                continue
            _, _, _, _, close = raw
            pct_from_close = ((value - close) / close) * 100
            latest.change_pct = pct_from_close

        if abs(pct_from_close) >= settings.index_break_percent:
            direction = "above" if pct_from_close > 0 else "below"
            icon = "🔺" if pct_from_close > 0 else "🔻"
            alerts.append(
                f"{icon} {name} moved {direction} previous close by {pct_from_close:.2f}% (Price: {latest.value})"
            )
            if abs(pct_from_close) >= major_index_move:
                is_major = True
                major_keys.append(f"index:{name}:{direction}")

        ohlc = previous_ohlc(name, INDEX_SYMBOLS[name])
        raw = ohlc.get("_raw")
        if raw:
            _, _, high, low, _ = raw
            if value > high:
                alerts.append(f"📈 {name} broke previous high of {high:.2f} (Price: {latest.value})")
            if value < low:
                alerts.append(f"📉 {name} broke previous low of {low:.2f} (Price: {latest.value})")

    vix = _get_latest_index("India VIX", INDEX_SYMBOLS["India VIX"], nse_indices)
    metrics_map["India VIX"] = vix

    value = _metric_float(vix.value)
    if value is not None:
        if vix.change_pct is not None:
            pct_from_close = vix.change_pct
        else:
            vix_raw = previous_ohlc("India VIX", INDEX_SYMBOLS["India VIX"]).get("_raw")
            if vix_raw:
                _, _, _, _, close = vix_raw
                pct_from_close = ((value - close) / close) * 100
                vix.change_pct = pct_from_close
            else:
                pct_from_close = 0.0

        if pct_from_close >= settings.vix_spike_percent:
            alerts.append(f"⚠️ India VIX spiked {pct_from_close:.2f}% above previous close (Price: {vix.value})")
            is_major = True
            major_keys.append("vix:spike")

    breadth = market_breadth()
    if breadth and breadth.advances is not None and breadth.declines is not None:
        total = (breadth.advances + breadth.declines) or 1
        adv_share = breadth.advances / total
        if adv_share <= settings.breadth_alert_ratio:
            alerts.append(f"📉 Market breadth weak: only {breadth.advances}/{total} sector indices advancing")
            is_major = True
            major_keys.append("breadth:weak")
        elif adv_share >= 1 - settings.breadth_alert_ratio:
            alerts.append(f"📈 Market breadth strong: {breadth.advances}/{total} sector indices advancing")
            major_keys.append("breadth:strong")

    gift = gift_nifty()
    if gift and gift.change_pct is not None and abs(gift.change_pct) >= settings.gift_nifty_gap_alert:
        direction = "up" if gift.change_pct > 0 else "down"
        alerts.append(f"🌐 GIFT Nifty gap {direction} {gift.change_pct:+.2f}% (last {gift.last})")
        if abs(gift.change_pct) >= settings.gift_nifty_gap_alert * 1.5:
            is_major = True
            major_keys.append(f"gift:{direction}")

    fii = fii_dii_activity()
    fii_shock_keys = {"FII/FPI", "FII", "DII"}
    for row in fii:
        if row.net_value is None:
            continue
        if any(k.lower() in (row.category or "").lower() for k in fii_shock_keys):
            if abs(row.net_value) >= 3000:
                direction = "net buy" if row.net_value > 0 else "net sell"
                alerts.append(f"💰 {row.category} {direction} ₹{abs(row.net_value):,.0f} cr today")
                if abs(row.net_value) >= 5000:
                    is_major = True
                    major_keys.append(f"fii:{direction}")

    news_lines, news_keys = _important_news_lines()
    if news_lines:
        alerts.extend(news_lines)
        major_keys.extend(news_keys)
        is_major = True

    return AlertCheck(alerts, is_major, major_keys, metrics_map)


def send_alerts(settings: Settings, check: AlertCheck | None = None) -> bool:
    check = check or check_breaking_alerts(settings)
    if not check.lines:
        return False

    from market_agent.db import save_alert, get_weekly_alert_count, get_last_alert_time
    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")

    # Smart Deduplication / Anti-Spam Check against database logs
    filtered_lines: list[str] = []
    for line in check.lines:
        target_symbol = None
        for name in BREAKING_INDEX_NAMES:
            if name.lower() in line.lower():
                target_symbol = name
                break
        if "vix" in line.lower():
            target_symbol = "India VIX"

        if target_symbol:
            last_ts_str = get_last_alert_time(target_symbol)
            if last_ts_str:
                try:
                    last_ts = datetime.datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S")
                    if (now - last_ts).total_seconds() < 2400:
                        print(f"Anti-Spam: Skipped repeat alert line for {target_symbol}.")
                        continue
                except Exception as exc:
                    print(f"Error parsing last alert timestamp for {target_symbol}: {exc}")
        filtered_lines.append(line)

    if not filtered_lines:
        return False

    alert_counts_info: list[str] = []
    for name in BREAKING_INDEX_NAMES:
        has_alert = any(name.lower() in line.lower() for line in filtered_lines)
        if has_alert:
            count = get_weekly_alert_count(name)
            alert_msg = "; ".join(filtered_lines)
            save_alert(now_str, date_str, name, "BREAKING", alert_msg)
            if count > 0:
                alert_counts_info.append(f"• {name} has triggered {count + 1} alert(s) this week.")

    if any("vix" in line.lower() for line in filtered_lines):
        save_alert(now_str, date_str, "India VIX", "BREAKING", "; ".join(filtered_lines))
        count = get_weekly_alert_count("India VIX")
        if count > 0:
            alert_counts_info.append(f"• India VIX has triggered {count + 1} alert(s) this week.")

    title = "🚨 Major Breaking Market Alert" if check.is_major else "⚠️ Breaking Market Alert"

    snapshot_rows = []
    for name in ("Nifty 50", "Bank Nifty", "India VIX"):
        metric = check.metrics.get(name) if check.metrics else None
        if metric:
            val_str = "Unavailable" if metric.value is None else str(metric.value)
            today_str = format_trend(metric.change_pct)
            icon = status_icon(metric)
            snapshot_rows.append([name, val_str, today_str, icon])
        else:
            snapshot_rows.append([name, "N/A", "N/A", "⚪"])

    snapshot_table = format_table(
        ["Index", "Value", "Today", "St"],
        snapshot_rows,
        ["left", "right", "right", "center"],
    )

    alert_bullets: list[str] = []
    news_bullets: list[str] = []
    for line in filtered_lines:
        if "major news" in line.lower():
            news_bullets.append(f"• {line}")
        else:
            alert_bullets.append(f"• {line}")

    lines = [title, f"🕒 Triggered: {now.strftime('%d %b %Y | %I:%M %p IST')}", "📌 Style: Simple English, detailed tables, no guessing.", "", "📊 MARKET SNAPSHOT", snapshot_table]

    if alert_bullets:
        lines.extend(["🔔 ALERTS ACTIVE", "\n".join(alert_bullets), ""])

    if news_bullets:
        lines.extend(["🗞️ NEWS FLASH", "\n".join(news_bullets), ""])

    if alert_counts_info:
        lines.extend(["📊 WEEKLY CONTEXT", "\n".join(alert_counts_info), ""])

    text = "\n".join(lines).strip()
    send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    return True
