"""
Aggregator: reads raw ticker_mentions from SQLite, computes stats,
fetches market data, runs the scorer, and writes ticker_snapshots.
Called periodically (every 5–10 min) by the FastAPI background task.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from .db import get_conn
from .market import get_batch_market_data
from .scorer import TickerData, compute_meme_score

logger = logging.getLogger(__name__)

# Minimum mentions in 24h to be worth scoring (filter pure noise)
MIN_MENTIONS_24H = 3


def _fetch_mention_stats(conn) -> dict[str, dict]:
    """
    Pull aggregated mention counts from ticker_mentions.
    Returns dict keyed by ticker.
    """
    now = datetime.now(timezone.utc)
    t_1h  = (now - timedelta(hours=1)).isoformat()
    t_24h = (now - timedelta(hours=24)).isoformat()
    t_48h = (now - timedelta(hours=48)).isoformat()
    t_7d  = (now - timedelta(days=7)).isoformat()

    c = conn.cursor()

    # 24h mentions + sentiment + upvotes + subreddits
    rows = c.execute("""
        SELECT
            ticker,
            COUNT(*)                    AS mentions_24h,
            AVG(sentiment)              AS sentiment_avg,
            SUM(upvotes)                AS upvote_sum,
            SUM(comments)              AS comment_sum,
            GROUP_CONCAT(DISTINCT source) AS sources
        FROM ticker_mentions
        WHERE scraped_at >= ?
        GROUP BY ticker
        HAVING mentions_24h >= ?
        ORDER BY mentions_24h DESC
    """, (t_24h, MIN_MENTIONS_24H)).fetchall()

    stats = {}
    for row in rows:
        ticker = row["ticker"]
        stats[ticker] = {
            "mentions_24h":  row["mentions_24h"],
            "sentiment_avg": round(row["sentiment_avg"] or 0, 3),
            "upvote_sum":    row["upvote_sum"] or 0,
            "comment_sum":   row["comment_sum"] or 0,
            "sources":       list(set((row["sources"] or "").split(","))),
        }

    # 1h mentions
    rows_1h = c.execute("""
        SELECT ticker, COUNT(*) AS cnt
        FROM ticker_mentions
        WHERE scraped_at >= ?
        GROUP BY ticker
    """, (t_1h,)).fetchall()
    for row in rows_1h:
        if row["ticker"] in stats:
            stats[row["ticker"]]["mentions_1h"] = row["cnt"]

    # Prior 24h window (48h→24h) for velocity calculation
    rows_prior = c.execute("""
        SELECT ticker, COUNT(*) AS cnt
        FROM ticker_mentions
        WHERE scraped_at >= ? AND scraped_at < ?
        GROUP BY ticker
    """, (t_48h, t_24h)).fetchall()
    for row in rows_prior:
        if row["ticker"] in stats:
            stats[row["ticker"]]["mentions_24h_prior"] = row["cnt"]

    # 7d mentions
    rows_7d = c.execute("""
        SELECT ticker, COUNT(*) AS cnt
        FROM ticker_mentions
        WHERE scraped_at >= ?
        GROUP BY ticker
    """, (t_7d,)).fetchall()
    for row in rows_7d:
        if row["ticker"] in stats:
            stats[row["ticker"]]["mentions_7d"] = row["cnt"]

    return stats


def run_aggregation() -> list[dict]:
    """
    Full aggregation pass. Returns list of scored ticker dicts.
    Also writes results to ticker_snapshots table.
    """
    conn = get_conn()
    mention_stats = _fetch_mention_stats(conn)

    if not mention_stats:
        logger.info("No mention data to aggregate.")
        conn.close()
        return []

    # Batch-fetch market data for all active tickers
    tickers = list(mention_stats.keys())
    market_data = get_batch_market_data(tickers)

    now_iso = datetime.now(timezone.utc).isoformat()
    results = []
    c = conn.cursor()

    for ticker, ms in mention_stats.items():
        md = market_data.get(ticker, {})

        td = TickerData(
            ticker=ticker,
            mentions_1h=ms.get("mentions_1h", 0),
            mentions_24h=ms["mentions_24h"],
            mentions_7d=ms.get("mentions_7d", 0),
            mentions_24h_prior=ms.get("mentions_24h_prior", 0),
            sentiment_avg=ms["sentiment_avg"],
            upvote_sum=ms["upvote_sum"],
            comment_sum=ms["comment_sum"],
            sources=ms["sources"],
            price=md.get("price", 0),
            price_change_1d=md.get("price_change_1d", 0),
            volume=md.get("volume", 0),
            avg_volume=md.get("avg_volume", 1),
            short_interest=md.get("short_interest", 0),
        )

        meme_score, signal_tag = compute_meme_score(td)

        row = {
            "ticker":           ticker,
            "meme_score":       meme_score,
            "signal_tag":       signal_tag,
            "mentions_1h":      td.mentions_1h,
            "mentions_24h":     td.mentions_24h,
            "mentions_7d":      td.mentions_7d,
            "sentiment_avg":    td.sentiment_avg,
            "upvote_sum":       td.upvote_sum,
            "comment_sum":      td.comment_sum,
            "sources":          td.sources,
            "price":            td.price,
            "price_change_1d":  td.price_change_1d,
            "volume":           td.volume,
            "avg_volume":       td.avg_volume,
            "volume_ratio":     round(td.volume / max(td.avg_volume, 1), 2),
            "short_interest":   td.short_interest,
            "snapped_at":       now_iso,
        }
        results.append(row)

        c.execute("""
            INSERT INTO ticker_snapshots
                (ticker, snapped_at, mentions_1h, mentions_24h, mentions_7d,
                 sentiment_avg, upvote_sum, comment_sum, sources,
                 price, price_change_1d, volume, avg_volume, volume_ratio,
                 short_interest, meme_score, signal_tag)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ticker, now_iso,
            td.mentions_1h, td.mentions_24h, td.mentions_7d,
            td.sentiment_avg, td.upvote_sum, td.comment_sum,
            json.dumps(td.sources),
            td.price, td.price_change_1d, td.volume, td.avg_volume,
            round(td.volume / max(td.avg_volume, 1), 2),
            td.short_interest, meme_score, signal_tag,
        ))

    conn.commit()
    conn.close()

    # Sort by meme score descending
    results.sort(key=lambda x: x["meme_score"], reverse=True)
    logger.info("Aggregation done — %d tickers scored", len(results))
    return results
