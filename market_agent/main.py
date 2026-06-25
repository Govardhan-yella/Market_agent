from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from market_agent.alerts import check_breaking_alerts, send_alerts
from market_agent.config import LOG_DIR, get_settings, Settings
from market_agent.report import build_report
from market_agent.telegram import send_message

_LOGGER = logging.getLogger("market_agent.main")
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _LOGGER.addHandler(_h)
    try:
        _LOGGER.addHandler(RotatingFileHandler(LOG_DIR / "agent.log", maxBytes=2_000_000, backupCount=3))
    except Exception:
        pass
log = _LOGGER


def _notify(settings: Settings, text: str) -> None:
    try:
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    except Exception as exc:
        log.error("Telegram notify failed: %s", exc)


def run_report(settings: Settings, kind: str, *, send: bool = True) -> None:
    if kind == "weekly":
        from market_agent.report import build_weekly_report
        bundle = build_weekly_report(settings)
    else:
        bundle = build_report(settings, kind)
    if send:
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, bundle.telegram_text)
    log.info("Generated %s report. Latest: work/reports/latest.json", kind)


def run_news(send: bool = True) -> None:
    from market_agent.data import gdelt_news, latest_news, newsapi_headlines
    from market_agent.sentiment import aggregate, annotate

    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.timezone))
    seen_titles: list[str] = []
    items = []
    try:
        items = annotate(latest_news(12)[:12])
        seen_titles.extend(n.title.lower() for n in items)
    except Exception as exc:
        log.error("latest_news failed: %s", exc)
    if settings.enable_newsapi and settings.newsapi_key:
        try:
            extra = newsapi_headlines(settings.newsapi_key, "India market NSE BSE")
            for n in annotate(extra):
                if n.title.lower() not in seen_titles:
                    items.append(n)
                    seen_titles.append(n.title.lower())
        except Exception as exc:
            log.error("NewsAPI skipped: %s", exc)
    try:
        extra = gdelt_news("India market OR RBI OR Nifty", limit=6)
        for n in annotate(extra):
            if n.title.lower() not in seen_titles:
                items.append(n)
                seen_titles.append(n.title.lower())
    except Exception as exc:
        log.error("GDELT skipped: %s", exc)
    items = items[:18]
    agg = aggregate(items)
    lines = [
        "🗞️ On-Demand News with Sentiment",
        f"🕒 {now.strftime('%d %b %Y | %I:%M %p IST')}",
        f"• Aggregate: {agg.get('label')} (avg {agg.get('avg')}, {agg.get('bullish')} bull / {agg.get('bearish')} bear / {agg.get('neutral')} neutral)",
        "",
    ]
    for idx, n in enumerate(items, 1):
        icon = "🟢" if n.sentiment_label == "bullish" else "🔴" if n.sentiment_label == "bearish" else "🟡"
        lines.append(f"{idx}. {icon} {n.title} [{n.source}] (sent {n.sentiment})")
    text = "\n".join(lines)
    if send:
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    log.info("On-demand news generated.")


def run_breadth(send: bool = True) -> None:
    from market_agent.data import fii_dii_activity, gift_nifty, market_breadth, sector_indices, us_futures
    from market_agent.utils import format_table, format_trend

    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.timezone))
    breadth = market_breadth()
    sectors = sector_indices(settings.sector_watchlist)
    gift = gift_nifty()
    futures = us_futures()
    fii = fii_dii_activity()

    lines = ["📊 On-Demand Snapshot", f"🕒 {now.strftime('%d %b %Y | %I:%M %p IST')}", "", "🌐 PRE-MARKET"]
    if gift and gift.last is not None:
        lines.append(f"• GIFT Nifty: {gift.last} ({format_trend(gift.change_pct)}) [{gift.status}]")
    for fq in futures:
        if fq.last is not None:
            lines.append(f"• {fq.name}: {fq.last} ({format_trend(fq.change_pct)}) [{fq.status}]")
    lines += ["", "📈 MARKET BREADTH"]
    if breadth and breadth.advances is not None:
        ratio = f"{breadth.ad_ratio:.2f}" if breadth.ad_ratio is not None else "N/A"
        lines.append(f"• Advances: {breadth.advances} | Declines: {breadth.declines} | Unchanged: {breadth.unchanged} | A/D: {ratio}")
    else:
        lines.append("• Breadth unavailable")
    lines += ["", "🧬 SECTORS"]
    if sectors:
        rows = [[s.name, format_trend(s.change_pct)] for s in sectors]
        lines.append(format_table(["Sector", "Today"], rows, ["left", "right"]))
    else:
        lines.append("• Sector data unavailable")
    lines += ["", "💰 FII / DII"]
    if fii:
        rows = [[r.category, f"{r.net_value:,.0f}" if r.net_value is not None else "N/A"] for r in fii]
        lines.append(format_table(["Category", "Net ₹ cr"], rows, ["left", "right"]))
    else:
        lines.append("• FII/DII unavailable")
    text = "\n".join(lines)
    if send:
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    log.info("On-demand snapshot generated.")


def is_market_active(now: datetime, settings: Settings) -> bool:
    if now.weekday() >= 5:
        return False
    try:
        from market_agent.data import nse_holidays
        holidays = nse_holidays() or []
        if holidays:
            today_str = now.strftime("%d-%b-%Y").lower()
            return not any(h.get("date", "").strip().lower() == today_str for h in holidays)
    except Exception as exc:
        log.warning("Holiday check skipped: %s", exc)
    return True


def run_scheduler() -> None:
    from market_agent.db import init_db
    init_db()
    settings = get_settings()
    now_local = datetime.now(ZoneInfo(settings.timezone))
    if not is_market_active(now_local, settings):
        if settings.auto_shutdown:
            msg = "Market agent: Today is a holiday/weekend. Skipping automated runs (auto-shutdown enabled)."
            _LOGGER.info(msg)
            _notify(settings, msg)
            return

    any_day_done: set[str] = set()
    last_alert_key: dict[str, datetime] = {}
    major_alert_keys: set[str] = set()
    telegram_cmd_offset = None
    shutdown = False

    def _handle_signal(signum, _frame) -> None:
        nonlocal shutdown
        shutdown = True
        _LOGGER.info("Scheduler stop requested (signal=%s).", signum)
        _notify(settings, "Market agent is shutting down.")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    log.info("Scheduler started. Keep this terminal open.")
    _notify(settings, f"Market agent started.\nStarted: {now_local.strftime('%d %b %Y | %I:%M %p IST')}\nKeep Mac awake and terminal open.")

    while not shutdown:
        now = datetime.now(ZoneInfo(settings.timezone))
        hm = now.strftime("%H:%M")

        # Handle Telegram commands
        try:
            from market_agent.telegram import get_updates
            updates = get_updates(settings.telegram_bot_token, offset=telegram_cmd_offset)
            for update in updates:
                upd_id = update.get("update_id")
                telegram_cmd_offset = upd_id + 1
                msg = (update.get("message") or {}).get("text", "")
                chat = (update.get("message") or {}).get("chat") or {}
                if str(chat.get("id")) != settings.telegram_chat_id:
                    continue
                if msg.strip().lower() == "/morning":
                    run_report(settings, "morning", send=True)
                elif msg.strip().lower() == "/closing":
                    run_report(settings, "closing", send=True)
                elif msg.strip().lower() == "/weekly":
                    run_report(settings, "weekly", send=True)
                elif msg.strip().lower() == "/news":
                    run_news(send=True)
                elif msg.strip().lower() == "/breadth":
                    run_breadth(send=True)
        except Exception as exc:
            log.error("Telegram command loop error: %s", exc)

        if not is_market_active(now, settings):
            if settings.auto_shutdown:
                msg = "Market agent: Today is a holiday/weekend. Skipping automated runs (auto-shutdown enabled)."
                _LOGGER.info(msg)
                _notify(settings, msg)
                break
            time.sleep(30)
            continue

        day_key = now.strftime("%Y-%m-%d")
        k_morning = lambda ts: ts.strftime("%Y-%m-%d") + ":morning"
        k_closing = lambda ts: ts.strftime("%Y-%m-%d") + ":closing"
        k_weekly = lambda ts: ts.strftime("%Y-%m-%d") + ":weekly"

        # Morning report
        if hm >= settings.report_time and k_morning(now) not in any_day_done:
            try:
                run_report(settings, "morning", send=True)
                any_day_done.add(k_morning(now))
            except Exception as exc:
                log.error("Morning report failed: %s", exc)

        # Closing report
        if hm >= settings.closing_report_time and k_closing(now) not in any_day_done:
            try:
                run_report(settings, "closing", send=True)
                any_day_done.add(k_closing(now))
                _notify(settings, "Market agent: Closing report sent.")
                if settings.auto_shutdown and now.weekday() not in (4, 5, 6):
                    log.info("Auto-shutdown requested after market close.")
                    _notify(settings, "Market agent auto-shutdown triggered.")
                    break
            except Exception as exc:
                log.error("Closing report failed: %s", exc)

        # Weekly report
        if now.weekday() == 4 and hm >= "17:00" and k_weekly(now) not in any_day_done:
            try:
                run_report(settings, "weekly", send=True)
                any_day_done.add(k_weekly(now))
                _notify(settings, "Market agent: Weekly report sent.")
                if settings.auto_shutdown:
                    log.info("Auto-shutdown requested after Friday weekly report.")
                    _notify(settings, "Market agent auto-shutdown triggered after weekly report.")
                    break
            except Exception as exc:
                log.error("Weekly report failed: %s", exc)

        # Breaking alerts
        now_minutes = now
        send_ok = True
        for key, ts in last_alert_key.items():
            if now_minutes - ts < timedelta(seconds=max(settings.alert_poll_seconds, 300)):
                send_ok = False
                break
        if send_ok and getattr(settings, "alert_market_start", "09:15") <= hm <= getattr(settings, "alert_market_end", "15:30"):
            try:
                check = check_breaking_alerts(settings)
                if check.lines and send_alerts(settings, check):
                    last_alert_key["index"] = now_minutes
                    if check.is_major:
                        major_alert_keys.update(check.major_keys or [])
            except Exception as exc:
                log.error("Alert check failed: %s", exc)

        time.sleep(30)

    log.info("Scheduler exited cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Indian market research agent")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("scheduler", help="Run Telegram-command-driven scheduler loop")
    report = sub.add_parser("report", help="Generate and optionally send a report")
    report.add_argument("kind", choices=["morning", "closing", "weekly"])
    report.add_argument("--no-send", action="store_true", help="Generate report without sending to Telegram")
    sub.add_parser("news", help="Fetch latest market news with sentiment")
    sub.add_parser("breath", help="Fetch on-demand breadth/sector snapshot")
    sub.add_parser("test-telegram", help="Send a Telegram test message")
    args = parser.parse_args()
    if not hasattr(args, "command"):
        print("Usage: python -m market_agent.main [scheduler | report morning|closing|weekly | news | breath | test-telegram]")
        return
    settings = get_settings()
    if args.command == "scheduler":
        run_scheduler()
    elif args.command == "report":
        run_report(settings, args.kind, send=not args.no_send)
    elif args.command == "news":
        run_news(send=True)
    elif args.command == "breath":
        run_breadth(send=True)
    elif args.command == "test-telegram":
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, "Market agent Telegram test: configuration OK.")
        print("Telegram test message sent.")


if __name__ == "__main__":
    main()
