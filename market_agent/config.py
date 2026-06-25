from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "work" / "reports"
LOG_DIR = ROOT / "work" / "logs"


def load_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    ai_provider: str
    openai_api_key: str
    openai_model: str
    local_writer_model: str
    local_helper_model: str
    ollama_url: str
    report_time: str
    closing_report_time: str
    timezone: str
    watchlist: list[str]
    watchlist_names: list[str]
    alert_poll_seconds: int
    alert_market_start: str
    alert_market_end: str
    vix_spike_percent: float
    index_break_percent: float
    auto_shutdown: bool
    sector_watchlist: list[str]
    cache_ttl_seconds: int
    breadth_alert_ratio: float
    gift_nifty_gap_alert: float
    newsapi_key: str
    finnhub_key: str
    alpha_vantage_key: str
    enable_pandas_ta: bool
    enable_newsapi: bool
    enable_finnhub: bool
    fred_api_key: str


def get_settings() -> Settings:
    load_env()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        ai_provider=os.getenv("AI_PROVIDER", "local").lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        local_writer_model=os.getenv("LOCAL_WRITER_MODEL", "llama3:latest"),
        local_helper_model=os.getenv("LOCAL_HELPER_MODEL", "qwen3:4b"),
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        report_time=os.getenv("REPORT_TIME", "08:50"),
        closing_report_time=os.getenv("CLOSING_REPORT_TIME", "16:00"),
        timezone=os.getenv("TIMEZONE", "Asia/Kolkata"),
        watchlist=_split(os.getenv("WATCHLIST", "")),
        watchlist_names=_split(os.getenv("WATCHLIST_NAMES", "")),
        alert_poll_seconds=int(os.getenv("ALERT_POLL_SECONDS", "2400")),
        alert_market_start=os.getenv("ALERT_MARKET_START", "09:15"),
        alert_market_end=os.getenv("ALERT_MARKET_END", "15:30"),
        vix_spike_percent=float(os.getenv("VIX_SPIKE_PERCENT", "8")),
        index_break_percent=float(os.getenv("INDEX_BREAK_PERCENT", "0.6")),
        auto_shutdown=os.getenv("AUTO_SHUTDOWN", "false").lower() in ("true", "1", "yes"),
        sector_watchlist=_split(os.getenv(
            "SECTOR_WATCHLIST",
            "NIFTY BANK,NIFTY IT,NIFTY AUTO,NIFTY PHARMA,NIFTY FMCG,NIFTY METAL,NIFTY REALTY,NIFTY ENERGY,NIFTY INFRA,NIFTY PSU BANK,NIFTY PRIVATE BANK,NIFTY MEDIA",
        )),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "60")),
        breadth_alert_ratio=float(os.getenv("BREADTH_ALERT_RATIO", "0.3")),
        gift_nifty_gap_alert=float(os.getenv("GIFT_NIFTY_GAP_ALERT", "0.5")),
        newsapi_key=os.getenv("NEWSAPI_KEY", ""),
        finnhub_key=os.getenv("FINNHUB_KEY", ""),
        alpha_vantage_key=os.getenv("ALPHA_VANTAGE_KEY", ""),
        enable_pandas_ta=os.getenv("ENABLE_PANDAS_TA", "true").lower() in ("true", "1", "yes"),
        enable_newsapi=os.getenv("ENABLE_NEWSAPI", "false").lower() in ("true", "1", "yes"),
        enable_finnhub=os.getenv("ENABLE_FINNHUB", "false").lower() in ("true", "1", "yes"),
        fred_api_key=os.getenv("FRED_API_KEY", ""),
    )
