"""
Reddit scraper — no API key required.
Uses Reddit's public JSON endpoints directly.
Works fine from any server (Railway, Render, etc.) — no CORS issues server-side.

Rate limit strategy:
  - 2 second pause between subreddit requests
  - Retries up to 3 times on 429 (rate limited) with exponential backoff
  - 5 minute scan interval keeps us well within Reddit's limits
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import requests

from . import sentiment as sent
from .db import get_conn
from .tickers import extract_tickers

logger = logging.getLogger(__name__)

SUBREDDITS = [
    "wallstreetbets",
    "shortsqueeze",
    "stocks",
    "options",
]

POST_LIMIT = 100
SCAN_INTERVAL = 300  # 5 minutes

# Reddit needs a User-Agent or it returns 429/403
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; wsb-scanner/1.0)"
}


def _fetch_posts(sub: str, retries: int = 3) -> list:
    """
    Fetch hot posts from a subreddit using Reddit's public JSON.
    Retries on rate limit (429) with exponential backoff.
    """
    url = f"https://www.reddit.com/r/{sub}/hot.json?limit={POST_LIMIT}&raw_json=1"

    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)

            if resp.status_code == 429:
                wait = 10 * (2 ** attempt)  # 10s, 20s, 40s
                logger.warning("Rate limited on r/%s — waiting %ds", sub, wait)
                time.sleep(wait)
                continue

            if resp.status_code == 403:
                logger.warning("r/%s is private or banned", sub)
                return []

            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("children", [])

        except requests.exceptions.Timeout:
            logger.warning("Timeout on r/%s (attempt %d)", sub, attempt + 1)
            time.sleep(5)
        except Exception as e:
            logger.warning("Error fetching r/%s: %s", sub, e)
            time.sleep(5)

    return []


def _store_mentions(conn, tickers, source, post_id, upvotes, comments, score, scraped_at):
    c = conn.cursor()
    for ticker in tickers:
        c.execute(
            """
            INSERT OR IGNORE INTO ticker_mentions
                (ticker, source, post_id, sentiment, upvotes, comments, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, source, post_id, score, upvotes, comments, scraped_at),
        )
    conn.commit()


def scan_once() -> int:
    """
    One full scan across all subreddits.
    Returns total number of mention rows written.
    """
    conn = get_conn()
    total = 0
    now = datetime.now(timezone.utc).isoformat()

    for sub in SUBREDDITS:
        posts = _fetch_posts(sub)
        logger.info("r/%s — got %d posts", sub, len(posts))

        for post in posts:
            data = post.get("data", {})
            text = f"{data.get('title', '')} {data.get('selftext', '')}"
            tickers = extract_tickers(text, validate=False)  # no yfinance on every post
            if not tickers:
                continue

            s = sent.score(text)
            _store_mentions(
                conn,
                tickers=tickers,
                source=sub,
                post_id=data.get("id", ""),
                upvotes=data.get("score", 0),
                comments=data.get("num_comments", 0),
                score=s,
                scraped_at=now,
            )
            total += len(tickers)

        time.sleep(2)  # polite pause between subreddits

    conn.close()
    logger.info("Scan complete — %d mention rows written", total)
    return total


def run_loop():
    """Background scan loop."""
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting scraper. No API key needed.")

    while True:
        try:
            scan_once()
        except Exception as e:
            logger.error("Scan error: %s", e)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_loop()
