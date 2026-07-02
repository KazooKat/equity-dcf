"""Market quote lookup (price, 52-week range) via Yahoo Finance's chart API.

Best-effort only: Yahoo rate-limits anonymous callers, so every consumer must
tolerate a None return and let the user type a price manually. The valuation
itself never depends on this module — EDGAR fundamentals drive the DCF; the
quote is only the comparison point.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


@dataclass
class Quote:
    price: float
    currency: str
    week52_high: float | None
    week52_low: float | None


def fetch_quote(ticker: str) -> Quote | None:
    try:
        resp = requests.get(CHART_URL.format(ticker=ticker), headers=UA, timeout=15)
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        if not price:
            return None
        return Quote(
            price=float(price),
            currency=meta.get("currency", "USD"),
            week52_high=meta.get("fiftyTwoWeekHigh"),
            week52_low=meta.get("fiftyTwoWeekLow"),
        )
    except Exception:
        return None
