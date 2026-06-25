from __future__ import annotations

import json
import logging
import signal
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any
from zoneinfo import ZoneInfo

from market_agent.ai import complete
from market_agent.config import REPORT_DIR, Settings
from market_agent.data import latest_news, market_snapshot
from market_agent.models import Metric, ReportBundle

# Structured logging: stdout + rotating file
_LOGGER = logging.getLogger("market_agent")
_LOGGER.setLevel(logging.INFO)
_LOG_FMT = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.StreamHandler(sys.stdout).setFormatter(_LOG_FMT)
_LOGGER.addHandler(logging.StreamHandler(sys.stdout))
try:
    _LOG_DIR = REPORT_DIR.parent / "logs"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOGGER.addHandler(RotatingFileHandler(_LOG_DIR / "agent.log", maxBytes=2_000_000, backupCount=3))
except Exception:
    pass
log = _LOGGER

# Shared formatters
from market_agent.utils import format_table


def _clean_val(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        val_float = float(str(value).replace(",", ""))
        if abs(val_float) >= 100:
            return f"{val_float:,.0f}"
        return f"{val_float:,.2f}"
    except Exception:
        return str(value)


def _clean_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        val_float = float(str(value).replace(",", "").replace("%", ""))
        if val_float == 0:
            return "0.00%"
        return f"{val_float:+.2f}%"
    except Exception:
        return str(value)


def _combined_status(change_pct: float | None, status_str: str) -> str:
    dir_icon = "🟡" if change_pct is None else "🟢" if change_pct > 0.1 else "🔴" if change_pct < -0.1 else "🟡"
    status_lower = status_str.lower()
    live_icon = "🟢" if "live" in status_lower else "🟡" if "prev" in status_lower else "⚪"
    return f"{dir_icon} {live_icon}"


def _metric_value(metric: Metric) -> str:
    return "Unavailable" if metric.value is None else str(metric.value)


def _status_icon(metric: Metric) -> str:
    status = metric.status.lower()
    if "live" in status:
        return "🟢"
    if "prev" in status:
        return "🟡"
    return "⚪"


def _friendly_note(note: str) -> str:
    lower = note.lower()
    if "urlopen" in lower or "nodename" in lower or "timed out" in lower:
        return "Data fetch failed. Check internet connection or source availability."
    if len(note) > 140:
        return note[:137].rstrip() + "..."
    return note


def _metric_bullet(metric: Metric) -> str:
    note = f"\n  - Note: {_friendly_note(metric.note)}" if metric.note else ""
    return (
        f"• {_status_icon(metric)} {metric.name}: {_metric_value(metric)}\n"
        f"  - Status: {metric.status}\n"
        f"  - Source: {metric.source}"
        f"{note}"
    )


def _section(lines: list[str], title: str, symbol: str) -> None:
    lines.extend(["", f"{symbol} {title}", ""])


def _available_count(metrics: list[Metric]) -> int:
    return sum(1 for metric in metrics if metric.value is not None)


def _format_trend(pct: float | None) -> str:
    if pct is None:
        return "N/A"
    if pct > 0.1:
        return f"🟢▲ +{pct:.2f}%"
    elif pct < -0.1:
        return f"🔴▼ {pct:.2f}%"
    return "🟡▶ Stable"


def build_report(settings: Settings, report_type: str) -> ReportBundle:
    from market_agent.db import save_index_snapshot, save_watchlist_snapshot, get_average_price, get_previous_price

    now = datetime.now(ZoneInfo(settings.timezone))
    data = market_snapshot(settings.watchlist, settings.watchlist_names, settings.sector_watchlist)
    date_str = now.strftime("%Y-%m-%d")

    # 1. Save data to SQLite database
    for name, metric in data.get("indices", {}).items():
        if metric.value is not None:
            try:
                val = float(str(metric.value).replace(",", ""))
                save_index_snapshot(date_str, name, val)
            except Exception as exc:
                log.error("Error saving index snapshot for %s: %s", name, exc)
    for name, metric in data.get("global", {}).items():
        if metric.value is not None:
            try:
                val = float(str(metric.value).replace(",", ""))
                save_index_snapshot(date_str, name, val)
            except Exception as exc:
                log.error("Error saving global snapshot for %s: %s", name, exc)
    for name, metric in data.get("watchlist", {}).items():
        if metric.value is not None:
            try:
                val = float(str(metric.value).replace(",", ""))
                save_watchlist_snapshot(date_str, name, val, change_pct=None)
            except Exception as exc:
                log.error("Error saving watchlist snapshot for %s: %s", name, exc)
    for label, metrics in data.get("fno", {}).items():
        for metric in metrics:
            if metric.value is not None and ("PCR" in metric.name or "Max Pain" in metric.name):
                try:
                    val = float(str(metric.value).replace(",", ""))
                    save_index_snapshot(date_str, metric.name, val)
                except Exception as exc:
                    log.error("Error saving F&O snapshot for %s: %s", metric.name, exc)

    # 2. Enrich watchlist metrics with 5-day trend indicators
    for name, metric in data.get("watchlist", {}).items():
        if metric.value is not None:
            try:
                val = float(str(metric.value).replace(",", ""))
                avg_5d = get_average_price(name, 5, table="watchlist_history")
                if avg_5d is not None and avg_5d > 0:
                    diff_pct = ((val - avg_5d) / avg_5d) * 100
                    trend_symbol = _format_trend(diff_pct)
                    metric.trend = trend_symbol
                    trend_note = f"Trending {trend_symbol} vs 5-day avg (₹{avg_5d:,.2f})."
                    metric.note = f"{trend_note} Note: {metric.note}" if metric.note else trend_note
            except Exception as exc:
                log.error("Error calculating trend for %s: %s", name, exc)
    # 3. Enrich indices with 5-day trend indicators
    for name, metric in data.get("indices", {}).items():
        if metric.value is not None:
            try:
                val = float(str(metric.value).replace(",", ""))
                avg_5d = get_average_price(name, 5, table="index_history")
                if avg_5d is not None and avg_5d > 0:
                    diff_pct = ((val - avg_5d) / avg_5d) * 100
                    trend_symbol = _format_trend(diff_pct)
                    metric.trend = trend_symbol
                    trend_note = f"Trending {trend_symbol} vs 5-day avg ({avg_5d:,.2f})."
                    metric.note = f"{trend_note} Note: {metric.note}" if metric.note else trend_note
            except Exception as exc:
                log.error("Error calculating trend for index %s: %s", name, exc)
    # 4. Enrich globals with 5-day trend indicators
    for name, metric in data.get("global", {}).items():
        if metric.value is not None:
            try:
                val = float(str(metric.value).replace(",", ""))
                avg_5d = get_average_price(name, 5, table="index_history")
                if avg_5d is not None and avg_5d > 0:
                    diff_pct = ((val - avg_5d) / avg_5d) * 100
                    trend_symbol = _format_trend(diff_pct)
                    metric.trend = trend_symbol
                    trend_note = f"Trending {trend_symbol} vs 5-day avg ({avg_5d:,.2f})."
                    metric.note = f"{trend_note} Note: {metric.note}" if metric.note else trend_note
            except Exception as exc:
                log.error("Error calculating trend for global %s: %s", name, exc)
    # 5. Enrich F&O metrics with 5-day trend indicators
    for label, metrics in data.get("fno", {}).items():
        for metric in metrics:
            if metric.value is not None and ("PCR" in metric.name or "Max Pain" in metric.name):
                try:
                    val = float(str(metric.value).replace(",", ""))
                    avg_5d = get_average_price(metric.name, 5, table="index_history")
                    if avg_5d is not None and avg_5d > 0:
                        diff_pct = ((val - avg_5d) / avg_5d) * 100
                        trend_symbol = _format_trend(diff_pct)
                        metric.trend = trend_symbol
                        trend_note = f"Trending {trend_symbol} vs 5-day avg ({avg_5d:,.2f})."
                        metric.note = f"{trend_note} Note: {metric.note}" if metric.note else trend_note
                    prev_val = get_previous_price(metric.name, table="index_history")
                    if prev_val is not None and prev_val > 0:
                        metric.change_pct = ((val - prev_val) / prev_val) * 100
                except Exception as exc:
                    log.error("Error calculating trend for F&O metric %s: %s", metric.name, exc)

    news_limit = 15 if report_type == "morning" else 8
    news = latest_news(news_limit)
    try:
        import market_agent.sentiment as _sent
        news = _sent.annotate(news)
        sentiment_agg = _sent.aggregate(news)
    except Exception:
        sentiment_agg = {"avg": 0.0, "label": "neutral", "bullish": 0, "bearish": 0, "neutral": len(news)}

    if settings.enable_newsapi:
        try:
            from market_agent.data import newsapi_headlines
            extra = newsapi_headlines(settings.newsapi_key, "India stock market NSE BSE")
            seen_titles = {n.title.lower() for n in news}
            for n in _sent.annotate(extra) if False else extra:
                if n.title.lower() not in seen_titles:
                    news.append(n)
                    seen_titles.add(n.title.lower())
        except Exception as exc:
            log.error("NewsAPI integration skipped: %s", exc)

    if settings.enable_finnhub and settings.watchlist:
        try:
            from market_agent.data import finnhub_company_news
            for sym in settings.watchlist[:3]:
                extra = finnhub_company_news(settings.finnhub_key, sym)
                seen_titles = {n.title.lower() for n in news}
                for n in extra:
                    if n.title.lower() not in seen_titles:
                        news.append(n)
                        seen_titles.add(n.title.lower())
        except Exception as exc:
            log.error("Finnhub integration skipped: %s", exc)

    try:
        from market_agent.data import gdelt_news
        gdelt_items = gdelt_news("India market OR Nifty OR RBI", limit=5)
        seen_titles = {n.title.lower() for n in news}
        for n in gdelt_items:
            if n.title.lower() not in seen_titles:
                news.append(n)
                seen_titles.add(n.title.lower())
    except Exception as exc:
        log.error("GDELT integration skipped: %s", exc)

    try:
        from market_agent.data import macro_calendar
        macro = macro_calendar()
    except Exception as exc:
        log.error("Macro calendar fetch skipped: %s", exc)
        macro = []

    fii_rows = data.get("fii_dii", [])
    if fii_rows:
        try:
            from market_agent.db import save_fii_dii
            for r in fii_rows:
                save_fii_dii(date_str, r.category, r.buy_value, r.sell_value, r.net_value)
        except Exception as exc:
            log.error("Error saving FII/DII: %s", exc)

    ai_prompt = _analysis_prompt(report_type, data, news, sentiment_agg, macro, fii_rows)
    ai_summary = complete(settings, ai_prompt, role="writer")
    telegram = _telegram_text(report_type, now, data, news, ai_summary, sentiment_agg, macro)
    bundle = ReportBundle(report_type=report_type, generated_at=now, telegram_text=telegram, data={"snapshot": _serializable(data), "sentiment": sentiment_agg, "macro": macro})
    _save(bundle)
    log.info("Generated %s report at %s", report_type, now.isoformat())
    return bundle


def _analysis_prompt(report_type: str, data: dict, news: list, sentiment_agg: dict, macro: list, fii_rows: list) -> str:
    lines = [
        f"Create a concise {report_type} Indian market analyst view.",
        "Rules: simple English, one line per insight, no guesses, mention missing data as unavailable.",
        "Use only the provided data.",
        "",
        "Market data:",
    ]
    for section in ("indices", "global", "watchlist"):
        lines.append(section.upper())
        for metric in data.get(section, {}).values():
            lines.append(metric.line())
    if fii_rows:
        lines.append("FII/DII")
        for r in fii_rows:
            lines.append(f"{r.category}: net {r.net_value}")
    breadth = data.get("breadth")
    if breadth and breadth.advances is not None:
        lines.append(f"Market breadth: {breadth.advances} adv / {breadth.declines} dec / {breadth.unchanged} unc")
    gift = data.get("gift_nifty")
    if gift and gift.last is not None:
        lines.append(f"GIFT Nifty: {gift.last} ({gift.change_pct}%)")
    if macro:
        lines.append("Macro / RBI")
        for m in macro[:5]:
            lines.append(f"{m.get('title')} | {m.get('source')}")
    lines.append("News:")
    for item in news:
        lines.append(f"{item.title} | {item.source} | sentiment: {item.sentiment_label} ({item.sentiment}) | {item.link}")
    lines.append(f"News sentiment aggregate: {sentiment_agg}")
    return "\n".join(lines)


def _telegram_text(report_type: str, now: datetime, data: dict, news: list, ai_summary: str, sentiment_agg: dict | None = None, macro: list | None = None) -> str:
    title = "🌅 Indian Market Morning Full Report" if report_type == "morning" else "🌙 Indian Market Closing Report"
    all_metrics = (
        list(data["indices"].values())
        + list(data["global"].values())
        + list(data["watchlist"].values())
        + [metric for metrics in data["technicals"].values() for metric in metrics]
        + [metric for metrics in data["pivots"].values() for metric in metrics]
        + [metric for metrics in data["fno"].values() for metric in metrics]
    )
    available = _available_count(all_metrics)
    missing = len(all_metrics) - available
    lines = [
        title,
        f"🕒 Generated: {now.strftime('%d %b %Y | %I:%M %p IST')}",
        "📌 Style: Simple English, detailed tables, no guessing.",
        "",
        "🔎 QUICK READ",
        f"• Data points checked: {len(all_metrics)}",
        f"• Available values: {available}",
        f"• Unavailable values: {missing}",
        "• St column displays status: 🟢 live vs 🟡 prev close.",
    ]

    market_status = data.get("market_status") or {}
    if market_status:
        lines.append(f"• NSE market status: {market_status.get('marketStatus', 'N/A')} | trade date: {market_status.get('tradeDate', 'N/A')}")

    holidays = data.get("holidays") or []
    upcoming = [h for h in holidays if h.get("date")]
    if upcoming:
        nxt = upcoming[0]
        lines.append(f"• Upcoming holiday: {nxt.get('date')} — {nxt.get('description')}")

    if report_type == "morning":
        _section(lines, "PRE-MARKET CUES", "🌐")
        pre_rows = []
        gift = data.get("gift_nifty")
        if gift:
            val = _clean_val(gift.last)
            chg = _format_trend(gift.change_pct)
            icon = "🟢" if "live" in gift.status.lower() else "🟡" if "prev" in gift.status.lower() else "⚪"
            pre_rows.append(["GIFT Nifty", val, chg, icon])
        for fq in data.get("futures", []):
            val = _clean_val(fq.last)
            chg = _format_trend(fq.change_pct)
            icon = "🟢" if "live" in fq.status.lower() else "🟡" if "prev" in fq.status.lower() else "⚪"
            pre_rows.append([fq.name, val, chg, icon])
        lines.append(format_table(["Instrument", "Value", "Change", "St"], pre_rows, ["left", "right", "right", "center"]) if pre_rows else "• Pre-market data unavailable.")

    _section(lines, "MARKET SNAPSHOT", "📊")
    indices_rows = []
    for metric in data["indices"].values():
        val_str = _clean_val(metric.value)
        today_str = _format_trend(metric.change_pct)
        trend_str = metric.trend if metric.trend else "N/A"
        icon = "🟢" if "live" in metric.status.lower() else "🟡" if "prev" in metric.status.lower() else "⚪"
        indices_rows.append([metric.name, val_str, today_str, trend_str, icon])
    lines.append(format_table(["Index", "Value", "Today", "5D Trend", "St"], indices_rows, ["left", "right", "right", "right", "center"]))

    _section(lines, "KEY LEVELS", "🧭")
    levels_rows = []
    labels = [("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"), ("Pivot", "Pivot"), ("R1", "R1"), ("S1", "S1"), ("R2", "R2"), ("S2", "S2")]
    for row_name, key in labels:
        row = [row_name]
        for name in ("Nifty 50", "Bank Nifty", "Sensex"):
            val = "N/A"
            if key in ("open", "high", "low", "close"):
                metric = data["ohlc"].get(name, {}).get(key)
                if metric and metric.value is not None:
                    val = _clean_val(metric.value)
            else:
                for p in data["pivots"].get(name, []):
                    if p.name.endswith(f" {key}"):
                        val = _clean_val(p.value) if p.value is not None else "N/A"
                        break
            row.append(val)
        levels_rows.append(row)
    lines.append(format_table(["Level", "Nifty 50", "Bank Nifty", "Sensex"], levels_rows, ["left", "right", "right", "right"]))

    _section(lines, "TECHNICALS", "🧪")
    tech_rows = []
    tech_labels = [("20 DMA", "20 DMA"), ("50 DMA", "50 DMA"), ("200 DMA", "200 DMA"), ("RSI 14", "RSI 14"), ("MACD", "MACD")]
    for row_name, key in tech_labels:
        row = [row_name]
        for name in ("Nifty 50", "Bank Nifty", "Sensex"):
            val = "N/A"
            for t in data["technicals"].get(name, []):
                if t.name.endswith(f" {key}"):
                    if t.value is not None:
                        val = _clean_val(t.value)
                        if "RSI" in key and t.note:
                            note_map = {"neutral": "Neu", "overbought": "OB", "oversold": "OS"}
                            val = f"{val} ({note_map.get(t.note.lower(), t.note[:3])})"
                        elif "MACD" in key and t.note:
                            note_map = {"positive": "Bull", "negative": "Bear"}
                            val = f"{val} ({note_map.get(t.note.lower(), t.note[:4])})"
                    break
            row.append(val)
        tech_rows.append(row)
    lines.append(format_table(["Indicator", "Nifty 50", "Bank Nifty", "Sensex"], tech_rows, ["left", "right", "right", "right"]))

    _section(lines, "GLOBAL MARKETS + COMMODITIES", "🌍")
    global_rows = []
    name_map = {"US Dollar Index": "USD Index", "Shanghai Composite": "Shanghai", "Russell 2000": "Russell 2K", "US 10Y Yield": "US 10Y", "Brent Crude": "Brent", "WTI Crude": "WTI", "Natural Gas": "Nat Gas"}
    for metric in data["global"].values():
        asset_name = name_map.get(metric.name, metric.name)
        val_str = _clean_val(metric.value)
        today_str = _format_trend(metric.change_pct)
        trend_str = metric.trend if metric.trend else "N/A"
        icon = "🟢" if "live" in metric.status.lower() else "🟡" if "prev" in metric.status.lower() else "⚪"
        global_rows.append([asset_name, val_str, today_str, trend_str, icon])
    lines.append(format_table(["Asset", "Value", "Today", "5D Trend", "St"], global_rows, ["left", "right", "right", "right", "center"]))

    _section(lines, "WATCHLIST", "⭐")
    watchlist_rows = []
    for metric in data["watchlist"].values():
        val_str = _clean_val(metric.value)
        today_str = _format_trend(metric.change_pct)
        trend_str = metric.trend if metric.trend else "N/A"
        icon = "🟢" if "live" in metric.status.lower() else "🟡" if "prev" in metric.status.lower() else "⚪"
        watchlist_rows.append([metric.name, val_str, today_str, trend_str, icon])
    lines.append(format_table(["Stock", "Value", "Today", "5D Trend", "St"], watchlist_rows, ["left", "right", "right", "right", "center"]) if watchlist_rows else "• No watchlist symbols configured.")

    _section(lines, "F&O DATA", "🧾")
    fno_pcr_rows = []
    for label in ("Nifty 50", "Bank Nifty"):
        metrics = data["fno"].get(label, [])
        underlying = pcr = pcr_today = pcr_trend = "N/A"
        for m in metrics:
            if "Underlying" in m.name:
                underlying = _clean_val(m.value)
            elif "PCR" in m.name:
                pcr = _clean_val(m.value)
                pcr_today = _format_trend(m.change_pct)
                pcr_trend = m.trend if m.trend else "N/A"
        fno_pcr_rows.append([label, underlying, pcr, pcr_today, pcr_trend])
    lines.append("📊 PCR Summary")
    lines.append(format_table(["Index", "Underlying", "PCR", "Today", "5D Trend"], fno_pcr_rows, ["left", "right", "right", "right", "right"]))

    fno_pain_rows = []
    for label in ("Nifty 50", "Bank Nifty"):
        metrics = data["fno"].get(label, [])
        max_pain = max_pain_today = max_pain_trend = "N/A"
        for m in metrics:
            if "Max Pain" in m.name:
                max_pain = _clean_val(m.value)
                max_pain_today = _format_trend(m.change_pct)
                max_pain_trend = m.trend if m.trend else "N/A"
        fno_pain_rows.append([label, max_pain, max_pain_today, max_pain_trend])
    lines.append("\n🧭 Max Pain Summary")
    lines.append(format_table(["Index", "MaxPain", "Today", "5D Trend"], fno_pain_rows, ["left", "right", "right", "right"]))

    fno_oi_rows = []
    for label in ("Nifty 50", "Bank Nifty"):
        metrics = data["fno"].get(label, [])
        call_oi = put_oi = "N/A"
        for m in metrics:
            if "Call" in m.name:
                call_oi = _clean_val(m.value) if m.value else "N/A"
            elif "Put" in m.name:
                put_oi = _clean_val(m.value) if m.value else "N/A"

        def clean_oi(oi_str: str) -> str:
            if not oi_str or "|" not in oi_str:
                return oi_str
            strike, oi_part = oi_str.split("|", 1)
            strike = strike.replace("strike", "").strip()
            oi_num = oi_part.replace("OI", "").replace(",", "").strip()
            try:
                return f"{strike} ({int(oi_num) // 1000}k)"
            except Exception:
                return strike

        fno_oi_rows.append([label, clean_oi(call_oi), clean_oi(put_oi)])
    lines.append("\n🧾 Heavy Open Interest Zones")
    lines.append(format_table(["Index", "Top Call (Resist)", "Top Put (Support)"], fno_oi_rows, ["left", "left", "left"]))

    _section(lines, "FII / DII ACTIVITY (₹ CR)", "💰")
    fii_rows = data.get("fii_dii") or []
    if fii_rows:
        fii_table_rows = []
        for r in fii_rows:
            buy, sell, net = _clean_val(r.buy_value), _clean_val(r.sell_value), _clean_val(r.net_value)
            net_icon = "🟢" if (r.net_value or 0) > 0 else "🔴" if (r.net_value or 0) < 0 else "🟡"
            cat = "CapMkt" if (r.category or "").lower() == "capital-market" else r.category
            fii_table_rows.append([cat, buy, sell, net, net_icon])
        lines.append(format_table(["Category", "Buy", "Sell", "Net", ""], fii_table_rows, ["left", "right", "right", "right", "center"]))
    else:
        lines.append("• FII/DII data unavailable for today.")

    _section(lines, "MARKET BREADTH", "📊")
    breadth = data.get("breadth")
    if breadth and breadth.advances is not None:
        ratio = f"{breadth.ad_ratio:.2f}" if breadth.ad_ratio is not None else "N/A"
        ad_icon = "🟢" if (breadth.ad_ratio or 0) > 1 else "🔴" if (breadth.ad_ratio or 0) < 1 else "🟡"
        lines.append(format_table(["Adv", "Dec", "Unc", "Total", "A/D", ""], [[str(breadth.advances), str(breadth.declines), str(breadth.unchanged), str(breadth.total), ratio, ad_icon]], ["right", "right", "right", "right", "right", "center"]))
        lines.append(f"• Source: {breadth.source}")
    else:
        lines.append(f"• Breadth unavailable: {breadth.note if breadth else 'no data'}")

    _section(lines, "SECTORAL HEATMAP", "🧬")
    sectors = data.get("sectors") or []
    if sectors:
        sec_rows = []
        for s in sectors:
            sec_rows.append([s.name.replace("NIFTY ", "").strip(), _clean_val(s.last), _format_trend(s.change_pct)])
        lines.append(format_table(["Sector", "Last", "Today"], sec_rows, ["left", "right", "right"]))
    else:
        lines.append("• Sectoral data unavailable.")

    _section(lines, "MACRO / RBI WATCH", "🏛️")
    if macro:
        for idx, item in enumerate(macro, 1):
            lines.append(f"{idx}. {item.get('title')}")
            if item.get("link"):
                lines.append(f"   • Link: {item.get('link')}")
    else:
        lines.append("• No RBI / macro headlines available.")

    _section(lines, "NEWS TO WATCH", "🗞️")
    if news:
        for idx, item in enumerate(news, 1):
            icon = "🟢" if item.sentiment_label == "bullish" else "🔴" if item.sentiment_label == "bearish" else "🟡"
            lines.extend([f"{idx}. {icon} {item.title}", f"   • Source: {item.source} | Sentiment: {item.sentiment_label} ({item.sentiment})", f"   • Why it matters: {item.impact}"])
        if sentiment_agg:
            agg_icon = "🟢" if sentiment_agg.get("label") == "bullish" else "🔴" if sentiment_agg.get("label") == "bearish" else "🟡"
            lines.append(f"• Aggregate news tone: {agg_icon} {sentiment_agg.get('label')} (avg {sentiment_agg.get('avg')}, {sentiment_agg.get('bullish')} bull / {sentiment_agg.get('bearish')} bear / {sentiment_agg.get('neutral')} neutral)")
    else:
        lines.append("• No trusted latest news fetched.")

    _section(lines, "FINAL VERDICT", "🎯")
    lines.append(ai_summary.strip() or "No AI summary was returned.")

    _section(lines, "DATA QUALITY NOTES", "🛡️")
    lines.extend(["• This report uses only fetched market data and listed news items.", "• Missing values are shown clearly as Unavailable.", "• Calculated levels use Yahoo Finance data where mentioned in the source.", "• Treat this as a market preparation note, not as a trade instruction."])
    return "\n".join(lines)


def _serializable(data: dict) -> dict:
    from market_agent.models import BreadthSnapshot, FiiDiiRow, FutureQuote, SectorRow

    def convert(value):
        if isinstance(value, (Metric, BreadthSnapshot, FiiDiiRow, FutureQuote, SectorRow)):
            return value.__dict__
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items() if k != "_raw"}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return value

    return convert(data)


def _save(bundle: ReportBundle) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = bundle.generated_at.strftime("%Y%m%d_%H%M")
    (REPORT_DIR / "latest.json").write_text(json.dumps(bundle.data, indent=2))
    (REPORT_DIR / f"{bundle.report_type}_{stamp}.json").write_text(json.dumps(bundle.data, indent=2))


def build_weekly_report(settings: Settings) -> ReportBundle:
    from market_agent.db import get_weekly_summary_stats

    stats = get_weekly_summary_stats()
    now = datetime.now(ZoneInfo(settings.timezone))

    idx_rows = []
    for idx in ("Nifty 50", "Sensex", "India VIX"):
        data = stats.get(idx, {})
        idx_rows.append([idx, _clean_val(data.get("min")), _clean_val(data.get("max"))])
    idx_table = format_table(["Index", "Weekly Min", "Weekly Max"], idx_rows, ["left", "right", "right"])

    watchlist_perf = stats.get("watchlist_perf", [])
    watch_rows = [[sym, _clean_val(oldest), _clean_val(newest), _format_trend(change)] for sym, change, oldest, newest in watchlist_perf]
    watch_table = format_table(["Stock", "Start Price", "End Price", "Return"], watch_rows, ["left", "right", "right", "right"]) if watch_rows else "• No watchlist performance data available."

    lines = ["📊 WEEKLY PERFORMANCE SUMMARY", f"🕒 Generated: {now.strftime('%d %b %Y | %I:%M %p IST')}", "📌 Style: Simple English, detailed tables, no guessing.", "", "📈 INDEX LEVELS (LAST 7 DAYS)", idx_table, "", "⭐ WATCHLIST PERFORMANCE (LAST 7 DAYS)", watch_table, "", "🔔 ALERTS ACTIVITY", f"• Total breaking alerts triggered this week: {stats.get('alerts_count', 0)}"]

    telegram_text = "\n".join(lines)
    bundle = ReportBundle(report_type="weekly", generated_at=now, telegram_text=telegram_text, data=stats)
    _save(bundle)
    log.info("Generated weekly report at %s", now.isoformat())
    return bundle
