"""
SQLite database setup and schema.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "scanner.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS ticker_mentions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            source      TEXT NOT NULL,          -- subreddit name
            post_id     TEXT NOT NULL,
            sentiment   REAL DEFAULT 0,         -- -1 to 1
            upvotes     INTEGER DEFAULT 0,
            comments    INTEGER DEFAULT 0,
            scraped_at  TEXT NOT NULL            -- ISO8601
        );

        CREATE INDEX IF NOT EXISTS idx_ticker_time
            ON ticker_mentions(ticker, scraped_at);

        CREATE TABLE IF NOT EXISTS ticker_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            snapped_at      TEXT NOT NULL,
            mentions_1h     INTEGER DEFAULT 0,
            mentions_24h    INTEGER DEFAULT 0,
            mentions_7d     INTEGER DEFAULT 0,
            sentiment_avg   REAL DEFAULT 0,
            upvote_sum      INTEGER DEFAULT 0,
            comment_sum     INTEGER DEFAULT 0,
            sources         TEXT DEFAULT '',    -- JSON list of subreddits
            price           REAL,
            price_change_1d REAL,
            volume          INTEGER,
            avg_volume      INTEGER,
            volume_ratio    REAL,               -- volume / avg_volume
            short_interest  REAL,               -- % float short (if available)
            meme_score      REAL DEFAULT 0,
            signal_tag      TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_snap_ticker_time
            ON ticker_snapshots(ticker, snapped_at);
    """)

    conn.commit()
    conn.close()
