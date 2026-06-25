from __future__ import annotations

import json
from typing import Any


def format_table(headers: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            if idx < len(widths):
                widths[idx] = max(widths[idx], len(str(val)))

    lines: list[str] = []
    # Header
    h_parts = []
    for idx, h in enumerate(headers):
        a = align[idx] if align else "left"
        if a == "right":
            h_parts.append(h.rjust(widths[idx]))
        elif a == "center":
            h_parts.append(h.center(widths[idx]))
        else:
            h_parts.append(h.ljust(widths[idx]))
    lines.append(" | ".join(h_parts))

    # Separator
    s_parts = ["-" * w for w in widths]
    lines.append("-|-".join(s_parts))

    # Rows
    for row in rows:
        r_parts = []
        for idx, val in enumerate(row):
            a = align[idx] if align else "left"
            val_str = str(val)
            if a == "right":
                r_parts.append(val_str.rjust(widths[idx]))
            elif a == "center":
                r_parts.append(val_str.center(widths[idx]))
            else:
                r_parts.append(val_str.ljust(widths[idx]))
        lines.append(" | ".join(r_parts))

    return "```\n" + "\n".join(lines) + "\n```"


def format_trend(pct: float | None) -> str:
    if pct is None:
        return "N/A"
    if pct > 0.1:
        return f"🟢▲ +{pct:.2f}%"
    elif pct < -0.1:
        return f"🔴▼ {pct:.2f}%"
    else:
        return "🟡▶ Stable"


def status_icon(metric: Any) -> str:
    status = str(getattr(metric, "status", "")).lower()
    if "live" in status:
        return "🟢"
    if "prev" in status:
        return "🟡"
    return "⚪"
