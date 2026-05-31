"""
Ticker extraction and validation.

Detects $TICKER patterns and bare uppercase words, then filters against
a known valid-ticker list fetched from yfinance to remove common English
words and invalid symbols.
"""
import re
import functools
import yfinance as yf

# Words that look like tickers but aren't — common false positives on WSB
TICKER_BLACKLIST = {
    "A", "I", "AM", "AN", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO",
    "UP", "US", "WE", "AI", "ALL", "ANY", "ARE", "ATH", "ATM", "BIG",
    "BUT", "BUY", "CAN", "DD", "DIP", "EPS", "ETF", "EV", "FED", "FOR",
    "GG", "GET", "GOOD", "GOT", "GUH", "HAVE", "HIGH", "HOW", "IMO",
    "ITS", "IRS", "ITM", "JFC", "JUST", "LMAO", "LMFAO", "LOL", "LOW",
    "MAKE", "MUCH", "NEW", "NOT", "NOW", "ONLY", "OTC", "OTM", "OUT",
    "OVER", "PDT", "PEG", "PER", "PM", "PNL", "PRE", "PUT", "RN", "ROI",
    "SEC", "SET", "SOME", "SOON", "SPY", "STO", "STOP", "THAN", "THE",
    "THIS", "TIME", "TOS", "VWAP", "WAIT", "WAS", "WHAT", "WHEN",
    "WITH", "WSB", "YOY", "YTD", "USA", "CEO", "CFO", "COO", "CTO",
    "IPO", "APR", "APY", "NAV", "MOM", "BOT", "TAX", "EDIT", "TLDR",
    "FOMO", "YOLO", "RIP", "FAQ", "ATH", "URL", "IMO", "HODL",
}

# Regex: $TICKER or 1-5 uppercase letters as standalone word
_DOLLAR_RE = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_RE = re.compile(r"\b([A-Z]{2,5})\b")


@functools.lru_cache(maxsize=2048)
def _is_valid_ticker(symbol: str) -> bool:
    """
    Quick yfinance validity check — cached so we don't hammer the API.
    A ticker is 'valid' if yfinance can find a shortName for it.
    """
    try:
        info = yf.Ticker(symbol).fast_info
        # fast_info raises or returns empty on invalid tickers
        return hasattr(info, "last_price") and info.last_price is not None
    except Exception:
        return False


def extract_tickers(text: str, validate: bool = True) -> list[str]:
    """
    Extract and return deduplicated ticker symbols from a block of text.
    Dollar-sign tickers ($GME) are always included (if not blacklisted).
    Bare uppercase words are validated against yfinance if validate=True.
    """
    found: set[str] = set()

    # $TICKER — high confidence
    for match in _DOLLAR_RE.finditer(text):
        sym = match.group(1).upper()
        if sym not in TICKER_BLACKLIST:
            found.add(sym)

    # BARE UPPERCASE — lower confidence, needs validation
    for match in _BARE_RE.finditer(text):
        sym = match.group(1).upper()
        if sym not in TICKER_BLACKLIST and sym not in found:
            if not validate or _is_valid_ticker(sym):
                found.add(sym)

    return sorted(found)
