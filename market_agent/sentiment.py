from __future__ import annotations

from market_agent.models import NewsItem

_SIA = None
_VADER = None
_VADER_OK = True


def _get_vader():
    global _SIA, _VADER, _VADER_OK
    if not _VADER_OK:
        return None
    if _VADER is not None:
        return _VADER
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _VADER = SentimentIntensityAnalyzer()
        return _VADER
    except Exception:
        _VADER_OK = False
        return None


def _get_sia():
    global _SIA
    if _SIA is not None:
        return _SIA
    sia = _get_vader()
    _SIA = sia
    return sia


_FIN_LEXICON = {
    "surge": 1.2, "plunge": -1.4, "rally": 1.3, "crash": -1.5,
    "beat": 1.1, "miss": -1.2, "downgrade": -1.3, "upgrade": 1.3,
    "profit": 1.0, "loss": -1.1, "warning": -1.2, "guidance": 0.4,
    "merger": 0.8, "acquisition": 0.7, "fraud": -1.6, "scandal": -1.4,
    "rate cut": 1.2, "rate hike": -0.8, "dovish": 0.9, "hawkish": -0.7,
    "bullish": 1.2, "bearish": -1.2, "record high": 1.1, "record low": -1.2,
    "buyback": 1.0, "dividend": 0.6, "default": -1.5, "ban": -1.3,
    "sanction": -0.9, "war": -1.0, "ceasefire": 0.7, "rbi": 0.0,
    "sebi": 0.0, "nse": 0.0, "bse": 0.0, "fii": 0.0, "dii": 0.0,
    "inflation": -0.5, "deflation": -0.6, "gdp": 0.0, "unemployment": -0.6,
}


def score_text(text: str) -> float:
    sia = _get_sia()
    base = 0.0
    if sia is not None:
        try:
            base = sia.polarity_scores(text)["compound"]
        except Exception:
            base = 0.0
    lower = text.lower()
    boost = 0.0
    for term, weight in _FIN_LEXICON.items():
        if term in lower:
            boost += weight * 0.15
    if boost > 1.0:
        boost = 1.0
    if boost < -1.0:
        boost = -1.0
    combined = base + boost
    if combined > 1.0:
        combined = 1.0
    if combined < -1.0:
        combined = -1.0
    return round(combined, 3)


def label_for(score: float) -> str:
    if score >= 0.35:
        return "bullish"
    if score <= -0.35:
        return "bearish"
    return "neutral"


def annotate(news: list[NewsItem]) -> list[NewsItem]:
    for item in news:
        s = score_text(item.title)
        item.sentiment = s
        item.sentiment_label = label_for(s)
    return news


def aggregate(news: list[NewsItem]) -> dict:
    if not news:
        return {"avg": 0.0, "label": "neutral", "bullish": 0, "bearish": 0, "neutral": 0}
    bullish = sum(1 for n in news if n.sentiment_label == "bullish")
    bearish = sum(1 for n in news if n.sentiment_label == "bearish")
    neutral = len(news) - bullish - bearish
    avg = sum(n.sentiment for n in news) / len(news)
    return {
        "avg": round(avg, 3),
        "label": label_for(avg),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
    }
