from __future__ import annotations

import json
import urllib.parse
import urllib.request


def send_message(token: str, chat_id: str, text: str) -> None:
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    for chunk in _chunks(text):
        _send_chunk(token, chat_id, chunk)


def _chunks(text: str, limit: int = 3800) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        line_size = len(line) + 1
        if current and size + line_size > limit:
            chunks.append("\n".join(current))
            current = []
            size = 0
        if line_size > limit:
            chunks.append(line[:limit])
            continue
        current.append(line)
        size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:limit]]


def _send_chunk(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as res:
        body = json.loads(res.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(body)


def get_updates(token: str, offset: int | None = None) -> list[dict]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    if offset:
        url += f"?offset={offset}"
    last_exc = None
    for attempt in range(2):  # 1 try + 1 retry
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=30) as res:
                body = json.loads(res.read().decode("utf-8"))
                if body.get("ok"):
                    return body.get("result", [])
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                continue  # retry once
    if last_exc:
        print(f"Error fetching Telegram updates: {last_exc}")
    return []
