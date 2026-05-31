"""
Meme Score Engine
=================
Produces a 0–100 score and a human-readable signal tag for each ticker.

Score components (weights sum to 100):
  30 pts  Mention velocity          — how fast mentions are growing
  20 pts  Sentiment                 — bullish lean of discussion
  20 pts  Volume spike              — unusual trading volume vs avg
  15 pts  Price momentum            — recent price move
  15 pts  Cross-subreddit spread    — appearing in multiple communities

Signal tags (mutually exclusive, highest-priority wins):
  🔥 Possible Breakout   meme_score >= 80
  📈 Momentum Building   meme_score 60–79
  👀 Early Momentum      meme_score 40–59
  💀 Dead Meme           mentions_24h < 5 and declining
  🚨 Exit Liquidity      score >= 70 AND price already up > 100% 1d
  📊 Noise               everything else (< 40)
"""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class TickerData:
    ticker: str
    mentions_1h: int = 0
    mentions_24h: int = 0
    mentions_7d: int = 0
    mentions_24h_prior: int = 0   # 24h window from 48h–24h ago
    sentiment_avg: float = 0.0
    upvote_sum: int = 0
    comment_sum: int = 0
    sources: list[str] = None     # list of subreddits seen in

    price: float = 0.0
    price_change_1d: float = 0.0  # % change
    volume: int = 0
    avg_volume: int = 1           # prevent div/0
    short_interest: float = 0.0   # % float short, 0 if unknown

    def __post_init__(self):
        if self.sources is None:
            self.sources = []


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _mention_velocity_score(d: TickerData) -> float:
    """
    30 pts max.
    Compares mentions_24h vs mentions_24h_prior.
    A 3x jump → full score. Uses log to avoid runaway compression.
    Also rewards raw 1h spike (early signal).
    """
    prior = max(d.mentions_24h_prior, 1)
    growth_ratio = d.mentions_24h / prior

    # log2(3) ≈ 1.58 → full score at 3x growth
    growth_pts = _clamp(math.log2(max(growth_ratio, 1)) / math.log2(3)) * 20

    # 1h spike bonus: if 1h mentions > 20% of 24h total, up-weight recency
    recency_ratio = d.mentions_1h / max(d.mentions_24h, 1)
    recency_pts = _clamp(recency_ratio / 0.25) * 10   # full at 25% in 1h

    return growth_pts + recency_pts  # max 30


def _sentiment_score(d: TickerData) -> float:
    """20 pts max. Converts -1..1 sentiment to 0..20."""
    # Neutral (0) → 10 pts; full bullish (1.0) → 20 pts; full bearish → 0
    return _clamp((d.sentiment_avg + 1) / 2) * 20


def _volume_score(d: TickerData) -> float:
    """
    20 pts max.
    volume_ratio = today_volume / avg_volume.
    2x normal → ~10 pts. 5x normal → full 20 pts.
    """
    ratio = d.volume / max(d.avg_volume, 1)
    pts = _clamp(math.log2(max(ratio, 1)) / math.log2(5)) * 20
    return pts


def _price_momentum_score(d: TickerData) -> float:
    """
    15 pts max.
    Reward meaningful but not parabolic moves.
    +5% to +30% → linearly scales to 15 pts.
    > +50% starts getting penalized (exit liquidity risk).
    """
    pct = d.price_change_1d
    if pct <= 0:
        # Small penalty for red days, but don't zero out
        return _clamp(1 + pct / 20) * 5
    elif pct <= 30:
        return _clamp(pct / 30) * 15
    elif pct <= 50:
        return 15  # Full pts in 30–50% range
    else:
        # Parabolic — score fades, already moved
        return _clamp(1 - (pct - 50) / 100) * 15


def _cross_subreddit_score(d: TickerData) -> float:
    """
    15 pts max.
    Appearing in 1 sub → 5 pts, 2 subs → 10 pts, 3+ subs → 15 pts.
    Cross-sub momentum is a strong early signal.
    """
    n = len(set(d.sources))
    return min(n, 3) * 5.0


def compute_meme_score(d: TickerData) -> tuple[float, str]:
    """
    Returns (meme_score 0–100, signal_tag).
    """
    raw = (
        _mention_velocity_score(d)
        + _sentiment_score(d)
        + _volume_score(d)
        + _price_momentum_score(d)
        + _cross_subreddit_score(d)
    )
    score = round(_clamp(raw, 0, 100), 1)

    # Short interest bonus (informational, doesn't inflate score)
    # High SI + rising mentions = squeeze candidate — add up to 5 bonus pts
    si_bonus = _clamp(d.short_interest / 30) * 5   # full at 30% float short
    score = min(score + si_bonus, 100)

    # ── Signal tags ──────────────────────────────────────────────────
    if d.price_change_1d > 100 and score >= 70:
        tag = "🚨 Exit Liquidity"
    elif d.mentions_24h < 5 and d.mentions_24h <= d.mentions_24h_prior:
        tag = "💀 Dead Meme"
    elif score >= 80:
        tag = "🔥 Possible Breakout"
    elif score >= 60:
        tag = "📈 Momentum Building"
    elif score >= 40:
        tag = "👀 Early Momentum"
    else:
        tag = "📊 Noise"

    return score, tag


def score_breakdown(d: TickerData) -> dict:
    """Return component scores for debugging/UI display."""
    return {
        "mention_velocity": round(_mention_velocity_score(d), 1),
        "sentiment":        round(_sentiment_score(d), 1),
        "volume_spike":     round(_volume_score(d), 1),
        "price_momentum":   round(_price_momentum_score(d), 1),
        "cross_subreddit":  round(_cross_subreddit_score(d), 1),
    }
