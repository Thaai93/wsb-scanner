"""
FastAPI application — WSB Meme Stock Scanner
Endpoints:
  GET /api/tickers        — ranked ticker list (latest snapshot)
  GET /api/ticker/{sym}   — detail for one ticker (history + breakdown)
  GET /api/status         — scanner health / last scan time
  POST /api/scan          — trigger manual scan (dev use)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .aggregator import run_aggregation
from .db import get_conn, init_db
from .scraper import scan_once, SCAN_INTERVAL

logger = logging.getLogger(__name__)

app = FastAPI(title="WSB Meme Scanner", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://*.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared state
_state = {
    "last_scan": None,
    "last_agg": None,
    "scanning": False,
    "ticker_cache": [],
}


# ── Background workers ────────────────────────────────────────────────────────

def _scraper_loop():
    """Runs in a background thread."""
    while True:
        try:
            _state["scanning"] = True
            scan_once()
            _state["last_scan"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logger.error("Scraper error: %s", e)
        finally:
            _state["scanning"] = False
        threading.Event().wait(SCAN_INTERVAL)


def _aggregator_loop():
    """Runs in a second background thread, offset by 30s from scraper."""
    threading.Event().wait(30)
    while True:
        try:
            results = run_aggregation()
            _state["ticker_cache"] = results
            _state["last_agg"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logger.error("Aggregator error: %s", e)
        threading.Event().wait(SCAN_INTERVAL)


@app.on_event("startup")
def startup():
    init_db()
    threading.Thread(target=_scraper_loop, daemon=True).start()
    threading.Thread(target=_aggregator_loop, daemon=True).start()
    logger.info("Scanner started.")


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/tickers")
def get_tickers(
    sort: str = Query("meme_score", description="Column to sort by"),
    filter: Optional[str] = Query(None, description="Filter by signal_tag keyword"),
    limit: int = Query(50, le=200),
):
    """
    Returns the latest scored ticker list.
    sort options: meme_score, mentions_24h, volume_ratio, price_change_1d, short_interest
    """
    data = _state["ticker_cache"]

    if filter:
        data = [t for t in data if filter.lower() in t.get("signal_tag", "").lower()]

    valid_sorts = {"meme_score", "mentions_24h", "volume_ratio", "price_change_1d", "short_interest", "mentions_1h"}
    if sort in valid_sorts:
        data = sorted(data, key=lambda x: x.get(sort, 0), reverse=True)

    return {
        "tickers": data[:limit],
        "total": len(data),
        "last_updated": _state["last_agg"],
        "scanning": _state["scanning"],
    }


@app.get("/api/ticker/{symbol}")
def get_ticker_detail(symbol: str):
    """
    Returns history for a single ticker from ticker_snapshots.
    Useful for sparkline / trend charts.
    """
    symbol = symbol.upper()
    conn = get_conn()
    c = conn.cursor()

    # Last 24h of snapshots
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = c.execute("""
        SELECT * FROM ticker_snapshots
        WHERE ticker = ? AND snapped_at >= ?
        ORDER BY snapped_at ASC
    """, (symbol, cutoff)).fetchall()

    if not rows:
        # Try all time
        rows = c.execute("""
            SELECT * FROM ticker_snapshots
            WHERE ticker = ?
            ORDER BY snapped_at DESC
            LIMIT 20
        """, (symbol,)).fetchall()

    conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for {symbol}")

    history = [dict(r) for r in rows]

    # Parse sources JSON
    for h in history:
        try:
            h["sources"] = json.loads(h["sources"])
        except Exception:
            pass

    latest = history[-1]
    return {
        "ticker": symbol,
        "latest": latest,
        "history": history,
    }


@app.get("/api/status")
def get_status():
    return {
        "status": "ok",
        "last_scan": _state["last_scan"],
        "last_aggregation": _state["last_agg"],
        "scanning": _state["scanning"],
        "ticker_count": len(_state["ticker_cache"]),
        "scan_interval_seconds": SCAN_INTERVAL,
    }


@app.post("/api/scan")
def trigger_scan():
    """Manually trigger one scrape + aggregate cycle (dev only)."""
    def _run():
        try:
            scan_once()
            results = run_aggregation()
            _state["ticker_cache"] = results
            _state["last_scan"] = datetime.now(timezone.utc).isoformat()
            _state["last_agg"] = _state["last_scan"]
        except Exception as e:
            logger.error("Manual scan error: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {"message": "Scan triggered in background."}
