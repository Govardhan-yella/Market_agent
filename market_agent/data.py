from __future__ import annotations

import json
import math
import statistics
import time as _time
from http.cookiejar import CookieJar
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from market_agent.models import (
    BreadthSnapshot,
    FiiDiiRow,
    FutureQuote,
    Metric,
    NewsItem,
    SectorRow,
)
from market_agent.cache import cached_json


YAHOO_SOURCE = "Yahoo Finance"
NSE_SOURCE = "NSE India option chain"
IST = ZoneInfo("Asia/Kolkata")


INDEX_SYMBOLS = {
    "Nifty 50": "^NSEI",
    "Sensex": "^BSESN",
    "Bank Nifty": "^NSEBANK",
    "India VIX": "^INDIAVIX",
}

GLOBAL_SYMBOLS = {
    "Dow Jones": "^DJI",
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Russell 2000": "^RUT",
    "FTSE 100": "^FTSE",
    "DAX": "^GDAXI",
    "CAC 40": "^FCHI",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "Shanghai Composite": "000001.SS",
    "US Dollar Index": "DX-Y.NYB",
    "US 10Y Yield": "^TNX",
    "USD/INR": "INR=X",
    "Brent Crude": "BZ=F",
    "WTI Crude": "CL=F",
    "Gold": "GC=F",
    "Silver": "SI=F",
    "Copper": "HG=F",
    "Natural Gas": "NG=F",
}


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


_nse_jar = CookieJar()
_nse_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_nse_jar))
_nse_last_seed_time = 0.0


def _nse_json(url: str, timeout: int = 20) -> dict:
    global _nse_last_seed_time
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
        "Connection": "keep-alive",
        "DNT": "1",
    }
    
    now = _time.time()
    has_cookies = any(cookie for cookie in _nse_jar)
    if not has_cookies or (now - _nse_last_seed_time > 300):
        try:
            _nse_opener.open(urllib.request.Request("https://www.nseindia.com/option-chain", headers=headers), timeout=timeout)
            _nse_last_seed_time = now
        except Exception as exc:
            if not has_cookies:
                raise ValueError(f"Failed to seed cookies from NSE option-chain: {exc}") from exc
                
    try:
        with _nse_opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            _nse_jar.clear()
            _nse_opener.open(urllib.request.Request("https://www.nseindia.com/option-chain", headers=headers), timeout=timeout)
            _nse_last_seed_time = _time.time()
            with _nse_opener.open(urllib.request.Request(url, headers=headers), timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        raise



def _chart(symbol: str, range_: str = "5d", interval: str = "1d") -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?range={range_}&interval={interval}&includePrePost=true"
    )
    return _get_json(url)


def _result(payload: dict) -> dict | None:
    result = payload.get("chart", {}).get("result") or []
    return result[0] if result else None


def _fmt(value: float | None, decimals: int = 2) -> str | None:
    if value is None or math.isnan(value):
        return None
    return f"{value:,.{decimals}f}"


def _fmt_int(value: int | float | None) -> str | None:
    if value is None:
        return None
    return f"{value:,.0f}"


def _live_status(meta: dict) -> str:
    import time
    reg_time = meta.get("regularMarketTime")
    if reg_time is not None:
        try:
            if abs(time.time() - float(reg_time)) <= 3600:
                return "Live"
        except Exception:
            pass
    market_state = str(meta.get("marketState", "")).upper()
    return "Live" if market_state in {"REGULAR", "PRE", "POST"} else "Prev Close"


def latest_metric(name: str, symbol: str) -> Metric:
    try:
        data = _result(_chart(symbol, "1d", "5m"))
        if not data:
            return Metric(name, None, "Unavailable", YAHOO_SOURCE)
        meta = data.get("meta", {})
        value = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev_close = meta.get("previousClose")
        pct = None
        if value is not None and prev_close is not None and float(prev_close) > 0:
            pct = ((float(value) - float(prev_close)) / float(prev_close)) * 100
        status = _live_status(meta)
        return Metric(name, _fmt(float(value)) if value is not None else None, status, YAHOO_SOURCE, change_pct=pct)
    except Exception as exc:
        return Metric(name, None, "Unavailable", YAHOO_SOURCE, str(exc))


def previous_ohlc(name: str, symbol: str) -> dict[str, Metric]:
    try:
        data = _result(_chart(symbol, "10d", "1d"))
        quote = (data.get("indicators", {}).get("quote") or [{}])[0]
        rows = []
        timestamps = data.get("timestamp") or []
        for idx, ts in enumerate(timestamps):
            open_ = quote.get("open", [None])[idx]
            high = quote.get("high", [None])[idx]
            low = quote.get("low", [None])[idx]
            close = quote.get("close", [None])[idx]
            if all(v is not None for v in (open_, high, low, close)):
                rows.append((ts, float(open_), float(high), float(low), float(close)))
        if not rows:
            raise ValueError("No completed daily OHLC rows")
        row = rows[-2] if len(rows) >= 2 else rows[-1]
        return {
            "open": Metric(f"{name} Previous Open", _fmt(row[1]), "Prev Close", YAHOO_SOURCE),
            "high": Metric(f"{name} Previous High", _fmt(row[2]), "Prev Close", YAHOO_SOURCE),
            "low": Metric(f"{name} Previous Low", _fmt(row[3]), "Prev Close", YAHOO_SOURCE),
            "close": Metric(f"{name} Previous Close", _fmt(row[4]), "Prev Close", YAHOO_SOURCE),
            "_raw": row,
        }
    except Exception as exc:
        return {
            "open": Metric(f"{name} Previous Open", None, "Unavailable", YAHOO_SOURCE, str(exc)),
            "high": Metric(f"{name} Previous High", None, "Unavailable", YAHOO_SOURCE, str(exc)),
            "low": Metric(f"{name} Previous Low", None, "Unavailable", YAHOO_SOURCE, str(exc)),
            "close": Metric(f"{name} Previous Close", None, "Unavailable", YAHOO_SOURCE, str(exc)),
        }


def pivots(name: str, ohlc: dict[str, Metric]) -> list[Metric]:
    raw = ohlc.get("_raw")
    if not raw:
        return [Metric(f"{name} Pivot Levels", None, "Unavailable", YAHOO_SOURCE)]
    _, _, high, low, close = raw
    pivot = (high + low + close) / 3
    r1 = (2 * pivot) - low
    s1 = (2 * pivot) - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)
    source = f"{YAHOO_SOURCE}; calculated from previous OHLC"
    return [
        Metric(f"{name} Pivot", _fmt(pivot), "Prev Close", source),
        Metric(f"{name} R1", _fmt(r1), "Prev Close", source),
        Metric(f"{name} R2", _fmt(r2), "Prev Close", source),
        Metric(f"{name} S1", _fmt(s1), "Prev Close", source),
        Metric(f"{name} S2", _fmt(s2), "Prev Close", source),
    ]


def technicals(name: str, symbol: str) -> list[Metric]:
    source = f"{YAHOO_SOURCE}; calculated from daily closes"
    try:
        data = _result(_chart(symbol, "1y", "1d"))
        quote = (data.get("indicators", {}).get("quote") or [{}])[0]
        closes = [float(v) for v in quote.get("close", []) if v is not None]
        if len(closes) < 60:
            raise ValueError("Not enough close history")
        metrics = []
        for window in (20, 50, 200):
            if len(closes) >= window:
                metrics.append(Metric(f"{name} {window} DMA", _fmt(statistics.fmean(closes[-window:])), "Prev Close", source))
            else:
                metrics.append(Metric(f"{name} {window} DMA", None, "Unavailable", source))
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(delta, 0) for delta in deltas[-14:]]
        losses = [abs(min(delta, 0)) for delta in deltas[-14:]]
        avg_gain = statistics.fmean(gains)
        avg_loss = statistics.fmean(losses)
        rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss)))
        rsi_note = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral"
        metrics.append(Metric(f"{name} RSI 14", _fmt(rsi), "Prev Close", source, rsi_note))
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_series = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
        signal = _ema(macd_series, 9)[-1]
        macd = macd_series[-1]
        note = "positive" if macd > signal else "negative"
        metrics.append(Metric(f"{name} MACD", _fmt(macd), "Prev Close", source, note))
        return metrics
    except Exception as exc:
        return [Metric(f"{name} Technicals", None, "Unavailable", source, str(exc))]


def _ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    ema = [values[0]]
    for value in values[1:]:
        ema.append((value * k) + (ema[-1] * (1 - k)))
    return ema


def option_chain_metrics(label: str, symbol: str) -> list[Metric]:
    try:
        encoded = urllib.parse.quote(symbol, safe="")
        base_url = f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={encoded}"
        seed = _nse_json(f"{base_url}&expiry=01-Jan-1970")
        seed_records = seed.get("records", {})
        expiries = seed_records.get("expiryDates") or []
        expiry = expiries[0] if expiries else None
        if not expiry:
            raise ValueError("No option expiry found")
        data = _nse_json(f"{base_url}&expiry={urllib.parse.quote(expiry, safe='')}")
        records = data.get("records", {})
        rows = records.get("data") or []
        timestamp = records.get("timestamp") or "latest available"
        underlying = records.get("underlyingValue")
        if not rows:
            raise ValueError(f"No option rows found for {expiry}")
        ce_oi = sum(int((row.get("CE") or {}).get("openInterest") or 0) for row in rows)
        pe_oi = sum(int((row.get("PE") or {}).get("openInterest") or 0) for row in rows)
        pcr = pe_oi / ce_oi if ce_oi else None
        ce_top = _top_option_oi(rows, "CE")
        pe_top = _top_option_oi(rows, "PE")
        max_pain = _max_pain(rows)
        source = f"{NSE_SOURCE}; nearest expiry {expiry}; timestamp {timestamp}"
        return [
            Metric(f"{label} Option Underlying", _fmt(float(underlying)) if underlying else None, "Live", source, "NSE option-chain underlying value."),
            Metric(f"{label} PCR", _fmt(pcr), "Live", source, "Above 1 means put OI is higher than call OI; below 1 means call OI is higher."),
            Metric(f"{label} Max Pain", _fmt(max_pain, 0), "Live", source, "Estimated expiry level where option buyers face maximum combined pain."),
            Metric(f"{label} Top Call OI", ce_top, "Live", source, "Heavy call OI can behave like resistance if price stays below it."),
            Metric(f"{label} Top Put OI", pe_top, "Live", source, "Heavy put OI can behave like support if price stays above it."),
        ]
    except Exception as exc:
        return [
            Metric(f"{label} Option Underlying", None, "Unavailable", NSE_SOURCE, str(exc)),
            Metric(f"{label} PCR", None, "Unavailable", NSE_SOURCE, str(exc)),
            Metric(f"{label} Max Pain", None, "Unavailable", NSE_SOURCE, str(exc)),
            Metric(f"{label} Top Call OI", None, "Unavailable", NSE_SOURCE, str(exc)),
            Metric(f"{label} Top Put OI", None, "Unavailable", NSE_SOURCE, str(exc)),
        ]


def _top_option_oi(rows: list[dict], side: str) -> str | None:
    best_strike = None
    best_oi = -1
    for row in rows:
        option = row.get(side) or {}
        oi = int(option.get("openInterest") or 0)
        if oi > best_oi:
            best_oi = oi
            best_strike = option.get("strikePrice") or row.get("strikePrice")
    if best_strike is None or best_oi <= 0:
        return None
    return f"{_fmt(float(best_strike), 0)} strike | OI {_fmt_int(best_oi)}"


def _max_pain(rows: list[dict]) -> float | None:
    strikes = sorted(
        {
            float(row.get("strikePrice"))
            for row in rows
            if row.get("strikePrice") is not None and (row.get("CE") or row.get("PE"))
        }
    )
    if not strikes:
        return None
    best_strike = None
    best_pain = None
    for settlement in strikes:
        pain = 0.0
        for row in rows:
            strike = float(row.get("strikePrice") or 0)
            ce_oi = float((row.get("CE") or {}).get("openInterest") or 0)
            pe_oi = float((row.get("PE") or {}).get("openInterest") or 0)
            pain += max(0, settlement - strike) * ce_oi
            pain += max(0, strike - settlement) * pe_oi
        if best_pain is None or pain < best_pain:
            best_pain = pain
            best_strike = settlement
    return best_strike


def nse_live_indices() -> dict[str, dict]:
    try:
        url = "https://www.nseindia.com/api/allIndices"
        data = _nse_json(url)
        rows = data.get("data") or []
        res = {}
        for r in rows:
            idx_name = r.get("index")
            if idx_name:
                res[idx_name.upper()] = {
                    "last": r.get("last"),
                    "percentChange": r.get("percentChange"),
                }
        return res
    except Exception as exc:
        print(f"Error fetching NSE live indices: {exc}")
        return {}


def market_snapshot(watchlist: list[str], watchlist_names: list[str], sector_watch: list[str] | None = None) -> dict:
    nse_indices = nse_live_indices()

    indices = {}
    for name, symbol in INDEX_SYMBOLS.items():
        nse_key = None
        if name == "Nifty 50":
            nse_key = "NIFTY 50"
        elif name == "Bank Nifty":
            nse_key = "NIFTY BANK"
        elif name == "India VIX":
            nse_key = "INDIA VIX"

        if nse_key and nse_indices and nse_key in nse_indices:
            val = nse_indices[nse_key]["last"]
            pct = nse_indices[nse_key]["percentChange"]
            source = "NSE India"
            indices[name] = Metric(name, _fmt(float(val)) if val is not None else None, "Live", source, change_pct=float(pct) if pct is not None else None)
        else:
            indices[name] = latest_metric(name, symbol)

    globals_ = {name: latest_metric(name, symbol) for name, symbol in GLOBAL_SYMBOLS.items()}
    ohlc = {name: previous_ohlc(name, symbol) for name, symbol in INDEX_SYMBOLS.items() if name != "India VIX"}
    pivot_map = {name: pivots(name, values) for name, values in ohlc.items()}
    tech = {name: technicals(name, symbol) for name, symbol in INDEX_SYMBOLS.items() if name != "India VIX"}
    fno = {
        "Nifty 50": option_chain_metrics("Nifty 50", "NIFTY"),
        "Bank Nifty": option_chain_metrics("Bank Nifty", "BANKNIFTY"),
    }
    watch = {}
    for idx, symbol in enumerate(watchlist):
        display = watchlist_names[idx] if idx < len(watchlist_names) else symbol
        watch[display] = latest_metric(display, symbol)

    sectors_raw = sector_indices(sector_watch)
    sectors = [
        SectorRow(name=s.name, last=s.last, change=s.change, change_pct=s.change_pct)
        for s in sectors_raw
    ]

    breadth = market_breadth()
    gift = gift_nifty()
    futures = us_futures()
    fii = fii_dii_activity()
    holidays = nse_holidays()
    market_status = nse_market_status_now()

    try:
        import market_agent.sentiment as _sent
        from market_agent.models import NewsItem
        base_news = latest_news(15)
        base_news = _sent.annotate(base_news)
    except Exception:
        pass

    return {
        "indices": indices,
        "global": globals_,
        "ohlc": ohlc,
        "pivots": pivot_map,
        "technicals": tech,
        "fno": fno,
        "watchlist": watch,
        "sectors": sectors,
        "breadth": breadth,
        "gift_nifty": gift,
        "futures": futures,
        "fii_dii": fii,
        "holidays": holidays,
        "market_status": market_status,
    }


def latest_news(limit: int = 12) -> list[NewsItem]:
    queries = [
        "Indian stock market NSE BSE when:12h",
        "RBI SEBI NSE BSE India market when:12h",
        "Nifty Bank Nifty Indian market when:12h",
        "Reliance HDFC Bank ICICI Bank TCS Infosys results India when:12h",
    ]
    trusted = ("Reuters", "Moneycontrol", "Economic Times", "CNBC", "Business Standard", "Mint", "Hindu BusinessLine", "Financial Express")
    items: list[NewsItem] = []
    seen: set[str] = set()
    for query in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as res:
                xml = res.read()
            root = ET.fromstring(xml)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                source_el = item.find("source")
                source = source_el.text.strip() if source_el is not None and source_el.text else "Google News"
                key = title.lower()
                if not title or key in seen:
                    continue
                if trusted and not any(t.lower() in source.lower() or t.lower() in title.lower() for t in trusted):
                    continue
                seen.add(key)
                items.append(NewsItem(title=title, source=source, link=link, impact="Impact: Watch for sector or index reaction after market confirmation."))
                if len(items) >= limit:
                    return items
        except Exception:
            continue
    return items[:limit]


SECTOR_INDEX_KEYS = {
    "NIFTY BANK",
    "NIFTY IT",
    "NIFTY AUTO",
    "NIFTY PHARMA",
    "NIFTY FMCG",
    "NIFTY METAL",
    "NIFTY REALTY",
    "NIFTY ENERGY",
    "NIFTY INFRA",
    "NIFTY PSU BANK",
    "NIFTY PRIVATE BANK",
    "NIFTY MEDIA",
    "NIFTY HEALTHCARE",
    "NIFTY CONSUMER DURABLES",
    "NIFTY OIL & GAS",
    "NIFTY FINANCIAL SERVICES",
}


def sector_indices(watch_keys: list[str] | None = None) -> list[SectorRow]:
    try:
        data = _nse_json("https://www.nseindia.com/api/allIndices")
        rows = data.get("data") or []
        wanted = set((watch_keys or list(SECTOR_INDEX_KEYS)))
        wanted = {w.upper() for w in wanted}
        out: list[SectorRow] = []
        for r in rows:
            name = (r.get("index") or "").upper()
            if not name:
                continue
            if wanted and name not in wanted:
                continue
            out.append(SectorRow(
                name=name,
                last=r.get("last"),
                change=r.get("change"),
                change_pct=r.get("percentChange"),
            ))
        out.sort(key=lambda x: (x.change_pct is None, -(x.change_pct or 0.0)))
        return out
    except Exception as exc:
        print(f"Error fetching NSE sector indices: {exc}")
        return []


def market_breadth() -> BreadthSnapshot:
    advances = declines = unchanged = 0
    source = "NSE India (sector indices proxy)"
    try:
        data = _nse_json("https://www.nseindia.com/api/allIndices")
        rows = data.get("data") or []
        adv = dec = unc = 0
        for r in rows:
            pct = r.get("percentChange")
            if pct is None:
                continue
            if pct > 0.05:
                adv += 1
            elif pct < -0.05:
                dec += 1
            else:
                unc += 1
        advances, declines, unchanged = adv, dec, unc
        source = "NSE India (sectoral breadth proxy)"
    except Exception as exc:
        return BreadthSnapshot(
            advances=None, declines=None, unchanged=None, total=None,
            ad_ratio=None, source=source,
            note=f"Breadth fetch failed: {exc}",
        )
    total = advances + declines + unchanged
    ratio = (advances / declines) if declines else None
    return BreadthSnapshot(
        advances=advances, declines=declines, unchanged=unchanged,
        total=total, ad_ratio=ratio, source=source,
    )


def fii_dii_activity() -> list[FiiDiiRow]:
    out: list[FiiDiiRow] = []
    try:
        data = _nse_json("https://www.nseindia.com/api/merged-daily-reports?key=favCapital")
        rows = data or []
        for r in rows:
            cat = (r.get("category") or "").strip()
            if not cat:
                continue
            try:
                buy = float(r.get("buyValue") or 0)
                sell = float(r.get("sellValue") or 0)
            except Exception:
                continue
            net = buy - sell
            date_str = r.get("date") or ""
            out.append(FiiDiiRow(category=cat, buy_value=buy, sell_value=sell, net_value=net, date=date_str))
        if not out:
            raise ValueError("No FII/DII rows returned")
        return out
    except Exception as exc:
        print(f"Error fetching FII/DII data: {exc}")
        return out


@cached_json("nse_holidays", 86400)
def nse_holidays() -> list[dict]:
    try:
        data = _nse_json("https://www.nseindia.com/api/holiday-master?type=trading")
        rows = []
        if isinstance(data, dict):
            if "CM" in data:
                rows = data["CM"]
            elif "data" in data:
                rows = data["data"]
            else:
                for val in data.values():
                    if isinstance(val, list):
                        rows = val
                        break
        else:
            rows = data
        
        out = []
        for r in (rows or []):
            date_str = r.get("tradingDate") or r.get("date") or ""
            out.append({
                "date": date_str,
                "description": (r.get("description") or "").strip(),
                "type": r.get("type") or "",
            })
        return out
    except Exception as exc:
        print(f"Error fetching NSE holidays: {exc}")
        return []


def nse_market_status_now() -> dict:
    try:
        data = _nse_json("https://www.nseindia.com/api/marketStatus")
        ms = data.get("marketState") or []
        if not ms:
            return {}
        first = ms[0]
        return {
            "market": first.get("market"),
            "marketStatus": first.get("marketStatus"),
            "tradeDate": first.get("tradeDate"),
        }
    except Exception as exc:
        print(f"Error fetching NSE market status: {exc}")
        return {}


def expiry_dates(underlying: str) -> list[str]:
    try:
        encoded = urllib.parse.quote(underlying, safe="")
        base_url = f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={encoded}"
        seed = _nse_json(f"{base_url}&expiry=01-Jan-1970")
        expiries = (seed.get("records") or {}).get("expiryDates") or []
        return list(expiries)
    except Exception as exc:
        print(f"Error fetching expiry dates for {underlying}: {exc}")
        return []


FUTURES_SYMBOLS = {
    "Dow Futures": "YM=F",
    "S&P 500 Futures": "ES=F",
    "Nasdaq Futures": "NQ=F",
    "Russell 2000 Futures": "RTY=F",
    "VIX Futures": "VX=F",
}

GIFT_NIFTY_CANDIDATES = [
    "GIFTNIFTY.NS",
    "GIFTNIFTY1!",
    "NG1!",
    "NIFTY_FUT.NS",
]


def _future_metric(name: str, symbol: str) -> FutureQuote:
    try:
        data = _result(_chart(symbol, "1d", "5m"))
        if not data:
            return FutureQuote(name, symbol, None, None, "Unavailable", YAHOO_SOURCE, "no data")
        meta = data.get("meta", {})
        value = meta.get("regularMarketPrice")
        prev = meta.get("previousClose")
        pct = None
        if value is not None and prev not in (None, 0):
            pct = ((float(value) - float(prev)) / float(prev)) * 100
        return FutureQuote(
            name=name,
            symbol=symbol,
            last=_fmt(float(value)) if value is not None else None,
            change_pct=pct,
            status=_live_status(meta),
            source=YAHOO_SOURCE,
        )
    except Exception as exc:
        return FutureQuote(name, symbol, None, None, "Unavailable", YAHOO_SOURCE, str(exc))


def us_futures() -> list[FutureQuote]:
    return [_future_metric(n, s) for n, s in FUTURES_SYMBOLS.items()]


def gift_nifty() -> FutureQuote:
    for sym in GIFT_NIFTY_CANDIDATES:
        fq = _future_metric("GIFT Nifty", sym)
        if fq.last is not None:
            return fq
    return FutureQuote("GIFT Nifty", GIFT_NIFTY_CANDIDATES[0], None, None, "Unavailable", YAHOO_SOURCE, "All candidates failed")


def macro_calendar() -> list[dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    last_exc = None
    url = "https://www.rbi.org.in/pressreleases_rss.xml"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as res:
                xml_data = res.read()
            root = ET.fromstring(xml_data)
            out = []
            for item in root.findall(".//item")[:8]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub = (item.findtext("pubDate") or "").strip()
                if title:
                    out.append({"title": title, "link": link, "published": pub, "source": "RBI Press Release"})
            if out:
                return out
            break  # Parsed OK but no items — no point retrying
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                _time.sleep(2 ** attempt)  # 1s, 2s backoff
    if last_exc:
        print(f"Error fetching RBI press releases: {last_exc}")
    return []



def _series_from_chart(symbol: str, range_: str = "1y", interval: str = "1d") -> list[float]:
    data = _result(_chart(symbol, range_, interval))
    if not data:
        return []
    quote = (data.get("indicators", {}).get("quote") or [{}])[0]
    return [float(v) for v in quote.get("close", []) if v is not None]


def enhanced_technicals(name: str, symbol: str) -> list[Metric]:
    source = f"{YAHOO_SOURCE}; pandas-ta"
    try:
        import pandas as pd
    except Exception:
        return [Metric(f"{name} Enhanced Technicals", None, "Unavailable", source, "pandas missing")]

    closes = _series_from_chart(symbol, "1y", "1d")
    if len(closes) < 60:
        return [Metric(f"{name} Enhanced Technicals", None, "Unavailable", source, "insufficient history")]

    df = pd.DataFrame({"close": closes})

    use_pta = True
    try:
        import pandas_ta as pta
    except Exception:
        use_pta = False

    out: list[Metric] = []

    try:
        if use_pta:
            bbands = pta.bbands(df["close"], length=20, std=2)
            if bbands is not None and not bbands.empty:
                upper_col = next((c for c in bbands.columns if c.startswith("BBU")), None)
                lower_col = next((c for c in bbands.columns if c.startswith("BBL")), None)
                if upper_col:
                    out.append(Metric(f"{name} BB Upper", _fmt(float(bbands[upper_col].iloc[-1])), "Prev Close", source, "20-period, 2 std dev"))
                if lower_col:
                    out.append(Metric(f"{name} BB Lower", _fmt(float(bbands[lower_col].iloc[-1])), "Prev Close", source, "20-period, 2 std dev"))

            atr = pta.atr(high=df["close"], low=df["close"], close=df["close"], length=14)
            if atr is not None and not atr.empty:
                out.append(Metric(f"{name} ATR 14", _fmt(float(atr.iloc[-1])), "Prev Close", source, "Average True Range, volatility proxy"))

            adx = pta.adx(high=df["close"], low=df["close"], close=df["close"], length=14)
            if adx is not None and not adx.empty:
                adx_col = next((c for c in adx.columns if c.startswith("ADX_")), None)
                if adx_col:
                    out.append(Metric(f"{name} ADX 14", _fmt(float(adx[adx_col].iloc[-1])), "Prev Close", source, "Trend strength"))

            st = pta.supertrend(high=df["close"], low=df["close"], close=df["close"], length=10, multiplier=3.0)
            if st is not None and not st.empty:
                st_col = next((c for c in st.columns if c.startswith("SUPERT_")), None)
                if st_col:
                    direction = "bullish" if df["close"].iloc[-1] > float(st[st_col].iloc[-1]) else "bearish"
                    out.append(Metric(f"{name} Supertrend", _fmt(float(st[st_col].iloc[-1])), "Prev Close", source, direction))

            obv = pta.obv(close=df["close"], volume=df["close"])
            if obv is not None and not obv.empty:
                last = float(obv.iloc[-1])
                out.append(Metric(f"{name} OBV", _fmt_int(last), "Prev Close", source, "On-Balance Volume, trend confirmation"))
        else:
            for w in (20, 50, 200):
                if len(closes) >= w:
                    out.append(Metric(f"{name} {w} DMA", _fmt(statistics.fmean(closes[-w:])), "Prev Close", source))
    except Exception as exc:
        out.append(Metric(f"{name} Enhanced Technicals", None, "Unavailable", source, str(exc)))

    return out or [Metric(f"{name} Enhanced Technicals", None, "Unavailable", source, "no indicators computed")]


def newsapi_headlines(api_key: str, query: str = "India stock market", limit: int = 8) -> list[NewsItem]:
    if not api_key:
        return []
    try:
        url = (
            "https://newsapi.org/v2/everything?"
            + urllib.parse.urlencode({
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": str(limit),
            })
        )
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        items: list[NewsItem] = []
        for a in (data.get("articles") or [])[:limit]:
            title = (a.get("title") or "").strip()
            if not title:
                continue
            src = (a.get("source") or {}).get("name") or "NewsAPI"
            items.append(NewsItem(
                title=title,
                source=src,
                link=a.get("url") or "",
                impact="Impact: Cross-check against trusted Indian outlets before reacting.",
            ))
        return items
    except Exception as exc:
        print(f"NewsAPI fetch failed: {exc}")
        return []


def finnhub_company_news(api_key: str, symbol: str, days_back: int = 3, limit: int = 5) -> list[NewsItem]:
    if not api_key:
        return []
    try:
        today = datetime.now(timezone.utc).date()
        frm = today - timedelta(days=days_back)
        url = (
            "https://finnhub.io/api/v1/company-news?"
            + urllib.parse.urlencode({
                "symbol": symbol,
                "from": frm.isoformat(),
                "to": today.isoformat(),
                "token": api_key,
            })
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
            rows = json.loads(res.read().decode("utf-8")) or []
        items: list[NewsItem] = []
        for a in rows[:limit]:
            title = (a.get("headline") or "").strip()
            if not title:
                continue
            src = a.get("source") or "Finnhub"
            items.append(NewsItem(
                title=title,
                source=src,
                link=a.get("url") or "",
                impact="Impact: Stock-specific catalyst. Watch the watchlist ticker for confirmation.",
            ))
        return items
    except Exception as exc:
        print(f"Finnhub fetch failed for {symbol}: {exc}")
        return []


def fred_observations(series_id: str, api_key: str, limit: int = 5) -> list[dict]:
    if not api_key:
        return []
    try:
        url = (
            "https://api.stlouisfed.org/fred/series/observations?"
            + urllib.parse.urlencode({
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": str(limit),
            })
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        return data.get("observations", []) or []
    except Exception as exc:
        print(f"FRED fetch failed for {series_id}: {exc}")
        return []


def gdelt_news(query: str, limit: int = 8) -> list[NewsItem]:
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        + urllib.parse.urlencode({
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(limit),
            "format": "json",
            "sort": "datedesc",
        })
    )
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as res:
                xml_data = res.read().decode("utf-8")
                data = json.loads(xml_data)
            items: list[NewsItem] = []
            for a in (data.get("articles") or [])[:limit]:
                title = (a.get("title") or "").strip()
                if not title:
                    continue
                items.append(NewsItem(
                    title=title,
                    source=a.get("domain") or "GDELT",
                    link=a.get("url") or "",
                    impact="Impact: Global event monitor. Use to spot cross-market contagion risk.",
                ))
            return items
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 and attempt < 2:
                wait = 2 ** (attempt + 1)  # 2s, 4s backoff
                print(f"GDELT rate-limited (429), retrying in {wait}s...")
                _time.sleep(wait)
                continue
            break
        except json.JSONDecodeError as exc:
            last_exc = ValueError("Response was not valid JSON (possibly rate-limited or blocked by provider)")
            if attempt < 2:
                wait = 2 ** (attempt + 1)  # 2s, 4s backoff
                print(f"GDELT returned non-JSON response, retrying in {wait}s...")
                _time.sleep(wait)
                continue
            break
        except Exception as exc:
            last_exc = exc
            break
    print(f"GDELT fetch failed: {last_exc}")
    return []



def worldbank_indicator(country: str, indicator: str) -> list[dict]:
    try:
        url = (
            f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
            "?format=json&per_page=10&date=2020:2026"
        )
        with urllib.request.urlopen(url, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        return (data[1] if isinstance(data, list) and len(data) > 1 else []) or []
    except Exception as exc:
        print(f"World Bank fetch failed for {country}/{indicator}: {exc}")
        return []
