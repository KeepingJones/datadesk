"""
News Monitor — polls public RSS feeds and Alpaca News for relevant headlines.

Sources (no API key required):
  - RSS: Reuters Business, MarketWatch Top Stories, FT Markets
  - Alpaca News API (if ALPACA_API_KEY is set): ticker-scoped news

Headlines are scored for sentiment (keyword heuristic) and, if Ollama is
running locally, passed to phi3:mini for a structured signal summary.

Signals are NOT submitted to OMS here — the monitor logs actionable headlines
so the human can act, and records them in analyst_reports for the strategy
analyst to review. High-conviction events (company-specific, score ≥ 0.6)
are forwarded to agent_worker via a shared queue if one is wired.
"""

import logging
import os
import time
from datetime import datetime, UTC
from typing import Optional

logger = logging.getLogger(__name__)

# Polling interval — check for new headlines every 5 minutes during session
POLL_INTERVAL = 300

# RSS feeds (no authentication, free-tier rate-limit friendly)
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.marketwatch.com/rss/topstories",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",     # WSJ Markets
]

# Simple sentiment keywords — enough to surface obvious movers
_BULLISH_WORDS = {
    "beat", "beats", "record", "surge", "rally", "upgrade", "buyback",
    "profit", "acquisition", "merger", "deal", "raised", "boost", "positive",
}
_BEARISH_WORDS = {
    "miss", "misses", "cut", "downgrade", "layoff", "recall", "fine",
    "sanction", "probe", "lawsuit", "loss", "decline", "warning", "risk",
    "tariff", "ban", "investigation",
}


def _score_headline(text: str) -> float:
    """Return a sentiment score: +1 = very bullish, -1 = very bearish, 0 = neutral."""
    words = set(text.lower().split())
    bull = len(words & _BULLISH_WORDS)
    bear = len(words & _BEARISH_WORDS)
    if bull == 0 and bear == 0:
        return 0.0
    return (bull - bear) / max(bull + bear, 1)


def _parse_rss(url: str, timeout: int = 8) -> list[dict]:
    """Fetch an RSS feed and return a list of {title, link, published} dicts."""
    try:
        import urllib.request
        import xml.etree.ElementTree as ET

        req = urllib.request.Request(url, headers={"User-Agent": "datadesk/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()

        root = ET.fromstring(data)
        items = []

        # Standard RSS <item> elements
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"title": title, "link": link, "published": pub, "source": url})

        # Atom <entry> elements
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = (link_el.get("href", "") if link_el is not None else "").strip()
            pub = (entry.findtext("{http://www.w3.org/2005/Atom}updated") or "").strip()
            if title:
                items.append({"title": title, "link": link, "published": pub, "source": url})

        return items[:20]  # cap per feed to avoid flooding
    except Exception as e:
        logger.debug(f"[NEWS] RSS fetch failed ({url}): {e}")
        return []


def _fetch_alpaca_news(tickers: list[str], limit: int = 30) -> list[dict]:
    """Fetch ticker-scoped news from Alpaca News API (requires API key)."""
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not api_secret:
        return []
    try:
        import urllib.request
        import json

        symbols = ",".join(tickers[:10])  # API cap ~10 per request
        url = f"https://data.alpaca.markets/v1beta1/news?symbols={symbols}&limit={limit}&sort=desc"
        req = urllib.request.Request(
            url,
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        items = []
        for article in data.get("news", []):
            items.append({
                "title": article.get("headline", ""),
                "link": article.get("url", ""),
                "published": article.get("created_at", ""),
                "source": "Alpaca",
                "tickers": article.get("symbols", []),
            })
        return items
    except Exception as e:
        logger.debug(f"[NEWS] Alpaca news fetch failed: {e}")
        return []


def _llm_signal(headline: str) -> Optional[str]:
    """Ask local Ollama (phi3:mini) for a one-line trading signal. Returns None on failure."""
    try:
        import urllib.request, json
        prompt = (
            f"Headline: {headline}\n\n"
            "Reply with ONE line only: BUY <TICKER>, SELL <TICKER>, or MONITOR. "
            "If the headline is market-moving for a specific stock, name it. "
            "No explanation."
        )
        body = json.dumps({"model": "phi3:mini", "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        return result.get("response", "").strip()
    except Exception:
        return None


class NewsMonitor:
    def __init__(self):
        self.running = False
        self.last_run = "Never"
        self._seen: set[str] = set()  # deduplicate by headline text

    def start(self):
        self.running = True
        logger.info("[NEWS] monitor started — polling RSS + Alpaca News every 5 min")
        while self.running:
            try:
                self._poll()
            except Exception as e:
                logger.exception(f"[NEWS] poll error: {e}")
            time.sleep(POLL_INTERVAL)

    def stop(self):
        self.running = False
        logger.info("[NEWS] monitor stopped")

    def _poll(self) -> list[dict]:
        from datadesk.live.universe import get_active_universe

        universe = get_active_universe()
        headlines: list[dict] = []

        # 1. RSS feeds
        for feed_url in RSS_FEEDS:
            headlines.extend(_parse_rss(feed_url))

        # 2. Alpaca News (ticker-scoped)
        headlines.extend(_fetch_alpaca_news(universe))

        actionable = []
        for item in headlines:
            title = item.get("title", "")
            if not title or title in self._seen:
                continue
            self._seen.add(title)

            score = _score_headline(title)
            item["sentiment_score"] = round(score, 3)

            # Only log / process headlines with clear sentiment
            if abs(score) < 0.2:
                continue

            direction = "BULLISH" if score > 0 else "BEARISH"
            logger.info(f"[NEWS] {direction} ({score:+.2f}): {title}")

            # Optional LLM signal
            signal = _llm_signal(title)
            if signal:
                item["llm_signal"] = signal
                logger.info(f"[NEWS] LLM → {signal}")

            actionable.append(item)

        if actionable:
            self._save_to_reports(actionable)

        self.last_run = datetime.now().strftime("%H:%M:%S")
        return actionable

    def _save_to_reports(self, items: list[dict]) -> None:
        try:
            from datadesk.db import save_report
            lines = [f"News scan — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC\n"]
            for item in items:
                score = item.get("sentiment_score", 0)
                signal = item.get("llm_signal", "")
                sig_str = f"  → {signal}" if signal else ""
                lines.append(f"  [{score:+.2f}] {item['title']}{sig_str}")
            save_report(
                analyst="news",
                title=f"News scan {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                body="\n".join(lines),
                data={"items": items[:50]},
            )
        except Exception as e:
            logger.debug(f"[NEWS] save_report failed: {e}")
