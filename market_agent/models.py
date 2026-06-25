from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Metric:
    name: str
    value: float | str | None
    status: str
    source: str
    note: str = ""
    trend: str = ""
    change_pct: float | None = None

    def line(self) -> str:
        value = "Unavailable" if self.value is None else self.value
        extra = f" - {self.note}" if self.note else ""
        trend_val = f" Trend: {self.trend}" if self.trend else ""
        change_val = f" Change: {self.change_pct}%" if self.change_pct is not None else ""
        return f"{self.name}: {value} [{self.status}] Source: {self.source}{extra}{trend_val}{change_val}"


@dataclass
class OHLC:
    open: Metric
    high: Metric
    low: Metric
    close: Metric


@dataclass
class NewsItem:
    title: str
    source: str
    link: str
    impact: str
    sentiment: float = 0.0
    sentiment_label: str = "neutral"


@dataclass
class ReportBundle:
    report_type: str
    generated_at: datetime
    telegram_text: str
    data: dict = field(default_factory=dict)


@dataclass
class FiiDiiRow:
    category: str
    buy_value: float | None
    sell_value: float | None
    net_value: float | None
    date: str = ""


@dataclass
class SectorRow:
    name: str
    last: float | None
    change: float | None
    change_pct: float | None


@dataclass
class BreadthSnapshot:
    advances: int | None
    declines: int | None
    unchanged: int | None
    total: int | None
    ad_ratio: float | None
    source: str
    note: str = ""


@dataclass
class FutureQuote:
    name: str
    symbol: str
    last: float | None
    change_pct: float | None
    status: str
    source: str
    note: str = ""
