"""
Reddit scraper using PRAW.
Scans configured subreddits, extracts tickers, scores sentiment,
and writes raw mention rows to SQLite.

Rate limit notes:
  - PRAW handles OAuth token refresh automatically.
  - We use read-only "script" mode — no login required.
  - Reddit allows ~60 requests/min for authenticated apps.
  - We sleep between subreddit scans to stay well under limits.
  - Duplicate post_id rows are ignored via INSERT OR IGNORE.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import praw
from dotenv import load_dotenv

from . import sentiment as sent
from .db import get_conn
from .tickers import extract_tickers

load_dotenv()
logger = logging.getLogger(__name__)

SUBREDDITS = [
    "wallstreetbets",
    "shortsqueeze",
    "stocks",
    "options",
]

# How many hot/new posts to pull per subreddit per cycle
POST_LIMIT = 50

# Seconds between full scan cycles (adjust to taste)
SCAN_INTERVAL = 300  # 5 minutes


def _build_reddit() -> praw.Reddit:
    return praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent=os.environ.get(
            "REDDIT_USER_AGENT", "wsb-scanner/1.0 by u/your_username"
        ),
    )


def _store_mentions(
    conn,
    tickers: list[str],
    source: str,
    post_id: str,
    upvotes: int,
    comments: int,
    score: float,
    scraped_at: str,
) -> None:
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


def scan_once(reddit: praw.Reddit) -> int:
    """Run one full scan across all subreddits. Returns total mentions stored."""
    conn = get_conn()
    total = 0
    now = datetime.now(timezone.utc).isoformat()

    for sub_name in SUBREDDITS:
        try:
            subreddit = reddit.subreddit(sub_name)

            # Pull hot + new to catch both trending and fresh posts
            posts = list(subreddit.hot(limit=POST_LIMIT)) + list(
                subreddit.new(limit=POST_LIMIT // 2)
            )

            for post in posts:
                text = f"{post.title} {post.selftext}"
                tickers = extract_tickers(text, validate=True)
                if not tickers:
                    continue

                s = sent.score(text)
                _store_mentions(
                    conn,
                    tickers=tickers,
                    source=sub_name,
                    post_id=post.id,
                    upvotes=post.score,
                    comments=post.num_comments,
                    score=s,
                    scraped_at=now,
                )
                total += len(tickers)

            # Also scan top comments on hot posts (captures ticker mentions buried in threads)
            for post in list(subreddit.hot(limit=10)):
                try:
                    post.comments.replace_more(limit=0)  # don't fetch hidden comments
                    for comment in post.comments.list()[:20]:
                        text = comment.body
                        tickers = extract_tickers(text, validate=False)  # faster
                        if not tickers:
                            continue
                        s = sent.score(text)
                        _store_mentions(
                            conn,
                            tickers=tickers,
                            source=sub_name,
                            post_id=f"{post.id}_{comment.id}",
                            upvotes=comment.score,
                            comments=0,
                            score=s,
                            scraped_at=now,
                        )
                        total += len(tickers)
                except Exception as e:
                    logger.debug("Comment error: %s", e)

            logger.info("Scanned r/%s — %d mention rows so far", sub_name, total)
            time.sleep(2)  # polite delay between subs

        except Exception as e:
            logger.warning("Failed to scan r/%s: %s", sub_name, e)

    conn.close()
    return total


def run_loop():
    """Entry point for background scan loop."""
    logging.basicConfig(level=logging.INFO)
    reddit = _build_reddit()
    logger.info("Starting WSB scanner. Scan interval: %ds", SCAN_INTERVAL)

    while True:
        try:
            n = scan_once(reddit)
            logger.info("Scan complete — %d mention entries written", n)
        except Exception as e:
            logger.error("Scan loop error: %s", e)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_loop()
