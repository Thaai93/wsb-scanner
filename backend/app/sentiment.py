"""
Lightweight sentiment scoring using VADER — no API key required.
Falls back gracefully if nltk data isn't downloaded yet.
"""
import re

try:
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    import nltk

    try:
        _sia = SentimentIntensityAnalyzer()
    except LookupError:
        nltk.download("vader_lexicon", quiet=True)
        _sia = SentimentIntensityAnalyzer()

    # WSB-specific lexicon overrides
    _sia.lexicon.update({
        "moon": 3.0,
        "mooning": 3.0,
        "rocket": 2.5,
        "tendies": 2.0,
        "apes": 1.5,
        "squeeze": 2.0,
        "short squeeze": 3.0,
        "yolo": 1.5,
        "calls": 1.0,
        "puts": -1.0,
        "puts": -1.0,
        "rekt": -3.0,
        "bagholding": -2.5,
        "bagholder": -2.5,
        "crash": -2.5,
        "dump": -2.0,
        "dumping": -2.0,
        "rug": -3.0,
        "rug pull": -3.5,
        "dead": -2.0,
        "dead cat": -2.5,
        "sell": -1.0,
        "selling": -1.0,
        "panic": -2.0,
        "bull": 2.0,
        "bullish": 2.5,
        "bear": -2.0,
        "bearish": -2.5,
        "gamma": 1.5,
        "gamma squeeze": 3.0,
        "short interest": 1.0,    # high SI is bullish on WSB
        "float": 0.5,
        "low float": 1.5,
    })

    def score(text: str) -> float:
        """Return compound sentiment -1.0 (bearish) to 1.0 (bullish)."""
        clean = re.sub(r"http\S+", "", text)  # strip URLs
        return _sia.polarity_scores(clean)["compound"]

except ImportError:
    def score(text: str) -> float:  # type: ignore[misc]
        """Fallback: neutral if VADER not available."""
        return 0.0
