from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "work" / "market_history.db"


def get_db_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_history (
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL,
                change_pct REAL,
                PRIMARY KEY (date, symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS index_history (
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL,
                PRIMARY KEY (date, symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts_history (
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fii_dii_history (
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                buy_value REAL,
                sell_value REAL,
                net_value REAL,
                PRIMARY KEY (date, category)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        conn.commit()


def cache_get(key: str) -> str | None:
    init_db()
    now = time.time()
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < now:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return row["value"]


def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    init_db()
    expires_at = time.time() + ttl_seconds
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO cache (key, value, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                expires_at=excluded.expires_at
            """,
            (key, value, expires_at),
        )
        conn.commit()


def save_watchlist_snapshot(date_str: str, symbol: str, price: float | None, change_pct: float | None) -> None:
    if price is None:
        return
    init_db()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO watchlist_history (date, symbol, price, change_pct)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date, symbol) DO UPDATE SET
                price=excluded.price,
                change_pct=excluded.change_pct
            """,
            (date_str, symbol, price, change_pct),
        )
        conn.commit()


def save_index_snapshot(date_str: str, symbol: str, price: float | None) -> None:
    if price is None:
        return
    init_db()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO index_history (date, symbol, price)
            VALUES (?, ?, ?)
            ON CONFLICT(date, symbol) DO UPDATE SET
                price=excluded.price
            """,
            (date_str, symbol, price),
        )
        conn.commit()


def save_alert(timestamp_str: str, date_str: str, symbol: str, alert_type: str, message: str) -> None:
    init_db()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO alerts_history (timestamp, date, symbol, alert_type, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (timestamp_str, date_str, symbol, alert_type, message),
        )
        conn.commit()


def save_fii_dii(date_str: str, category: str, buy_value: float | None, sell_value: float | None, net_value: float | None) -> None:
    init_db()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO fii_dii_history (date, category, buy_value, sell_value, net_value)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date, category) DO UPDATE SET
                buy_value=excluded.buy_value,
                sell_value=excluded.sell_value,
                net_value=excluded.net_value
            """,
            (date_str, category, buy_value, sell_value, net_value),
        )
        conn.commit()


def get_average_price(symbol: str, days: int = 5, table: str = "watchlist_history") -> float | None:
    init_db()
    if table not in ("watchlist_history", "index_history"):
        raise ValueError("Invalid table name for historical price query")
    with get_db_connection() as conn:
        row = conn.execute(
            f"""
            SELECT AVG(price) as avg_price FROM (
                SELECT price FROM {table}
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT ?
            )
            """,
            (symbol, days),
        ).fetchone()
        return row["avg_price"] if row and row["avg_price"] is not None else None


def get_weekly_alert_count(symbol: str) -> int:
    init_db()
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) as alert_count FROM alerts_history
            WHERE symbol = ? AND date >= date('now', '-7 days')
            """,
            (symbol,),
        ).fetchone()
        return row["alert_count"] if row else 0


def get_last_alert_time(symbol: str) -> str | None:
    init_db()
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT timestamp FROM alerts_history
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        return row["timestamp"] if row else None


def get_previous_price(symbol: str, table: str = "index_history") -> float | None:
    init_db()
    if table not in ("watchlist_history", "index_history"):
        raise ValueError("Invalid table name")
    with get_db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT price FROM {table}
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT 2
            """,
            (symbol,),
        ).fetchall()
        if len(rows) >= 2:
            return rows[1]["price"]
        return None


def get_weekly_summary_stats() -> dict:
    init_db()
    stats = {}
    with get_db_connection() as conn:
        for idx in ("Nifty 50", "Sensex", "India VIX"):
            row = conn.execute(
                """
                SELECT MAX(price) as max_p, MIN(price) as min_p FROM index_history
                WHERE symbol = ? AND date >= date('now', '-7 days')
                """,
                (idx,),
            ).fetchone()
            if row and row["max_p"] is not None:
                stats[idx] = {"max": row["max_p"], "min": row["min_p"]}
            else:
                stats[idx] = {"max": None, "min": None}

        row = conn.execute(
            """
            SELECT COUNT(*) as alert_count FROM alerts_history
            WHERE date >= date('now', '-7 days')
            """
        ).fetchone()
        stats["alerts_count"] = row["alert_count"] if row else 0

        symbols_rows = conn.execute("SELECT DISTINCT symbol FROM watchlist_history").fetchall()
        watchlist_perf = []
        for s_row in symbols_rows:
            sym = s_row["symbol"]
            oldest = conn.execute(
                """
                SELECT price FROM watchlist_history
                WHERE symbol = ? AND date >= date('now', '-7 days')
                ORDER BY date ASC LIMIT 1
                """,
                (sym,),
            ).fetchone()
            newest = conn.execute(
                """
                SELECT price FROM watchlist_history
                WHERE symbol = ? AND date >= date('now', '-7 days')
                ORDER BY date DESC LIMIT 1
                """,
                (sym,),
            ).fetchone()
            if oldest and newest and oldest["price"] > 0:
                change = ((newest["price"] - oldest["price"]) / oldest["price"]) * 100
                watchlist_perf.append((sym, change, oldest["price"], newest["price"]))

        if watchlist_perf:
            watchlist_perf.sort(key=lambda x: x[1], reverse=True)
            stats["watchlist_perf"] = watchlist_perf
            stats["worst_stock"] = watchlist_perf[-1]
            stats["best_stock"] = watchlist_perf[0]
        else:
            stats["watchlist_perf"] = []
            stats["worst_stock"] = None
            stats["best_stock"] = None

        fii_row = conn.execute(
            """
            SELECT category, net_value FROM fii_dii_history
            WHERE date >= date('now', '-7 days')
            ORDER BY date DESC
            """
        ).fetchall()
        stats["fii_dii_recent"] = [dict(r) for r in fii_row]

    return stats
