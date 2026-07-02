"""SEC EDGAR client: ticker -> CIK -> company facts -> tidy annual financials.

Uses the free, no-key EDGAR XBRL "companyfacts" API. SEC requires a descriptive
User-Agent identifying the caller. All numbers come straight from 10-K filings —
nothing is hardcoded or estimated at this layer.

Gotchas this module handles (verified against live API responses):
- `form == "10-K" and fp == "FY"` still includes *quarterly* duration facts
  (e.g. Apple's Q4 revenue carries fp="FY"). Annual duration facts are filtered
  by span: 330-400 days between `start` and `end`.
- Tag coverage drifts across years (Apple's `Revenues` stops in FY2018;
  `RevenueFromContractWithCustomerExcludingAssessedTax` takes over). Each metric
  has a fallback chain and years are merged across tags, earlier tags winning.
- Balance-sheet (instant) facts carry no annual `frame`; they are deduped by
  period end, latest filing wins.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable

import pandas as pd
import requests

# SEC's WAF 403s User-Agents containing parentheses/URLs — keep it plain text.
USER_AGENT = "equity-dcf portfolio project phanha.tranluong@gmail.com"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "40-F"}

# Fallback chains per metric, most-preferred first. "duration" metrics are
# flows (income statement / cash flow); "instant" metrics are balances.
DURATION_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "cfo": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
}
INSTANT_TAGS: dict[str, list[str]] = {
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    # Debt is split into three non-overlapping buckets that get summed.
    # Within each bucket the chain is ordered composite-first so a filer that
    # reports both a composite and its parts (e.g. KO's NotesAndLoansPayable
    # alongside CommercialPaper) is never double-counted.
    "lt_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
    ],
    "st_debt": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "other_st_debt": [
        "NotesAndLoansPayable",
        "ShortTermBorrowings",
        "CommercialPaper",
        "DebtCurrent",
    ],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities_current": ["LiabilitiesCurrent"],
}


def _get_json(url: str) -> dict:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def load_ticker_map() -> dict[str, dict]:
    """Ticker -> {cik, title} for every EDGAR filer."""
    raw = _get_json(TICKER_MAP_URL)
    return {
        row["ticker"].upper(): {"cik": str(row["cik_str"]).zfill(10), "title": row["title"]}
        for row in raw.values()
    }


def lookup_company(ticker: str, ticker_map: dict[str, dict] | None = None) -> dict:
    """Resolve a ticker to {ticker, cik, title}. Raises KeyError if unknown."""
    ticker = ticker.strip().upper()
    tmap = ticker_map or load_ticker_map()
    if ticker not in tmap:
        raise KeyError(f"Ticker {ticker!r} not found in SEC EDGAR company list")
    return {"ticker": ticker, **tmap[ticker]}


def fetch_company_facts(cik: str) -> dict:
    return _get_json(FACTS_URL.format(cik=cik))


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _is_annual_duration(row: dict) -> bool:
    if row.get("form") not in ANNUAL_FORMS or "start" not in row:
        return False
    span = (_parse_date(row["end"]) - _parse_date(row["start"])).days
    return 330 <= span <= 400


def _dedupe_by_end(rows: list[dict]) -> dict[dt.date, float]:
    """Keep one value per period end; the most recently filed wins."""
    best: dict[dt.date, dict] = {}
    for row in rows:
        end = _parse_date(row["end"])
        if end not in best or row.get("filed", "") > best[end].get("filed", ""):
            best[end] = row
    return {end: row["val"] for end, row in best.items()}


def _annual_series(gaap: dict, tags: list[str], keep: Callable[[dict], bool]) -> dict[dt.date, float]:
    """Merge annual values across a tag fallback chain (earlier tags win)."""
    merged: dict[dt.date, float] = {}
    for tag in reversed(tags):  # later (less preferred) tags first, overwritten by earlier
        fact = gaap.get(tag)
        if not fact:
            continue
        units = fact.get("units", {})
        # A dual-currency filer reports the same concept in several units;
        # merging them would mix currencies. Take USD when present, otherwise
        # the single unit the filer uses.
        unit_key = "USD" if "USD" in units else next(iter(units), None)
        if unit_key is None:
            continue
        rows = [r for r in units[unit_key] if keep(r)]
        merged.update(_dedupe_by_end(rows))
    return merged


def shares_outstanding(facts: dict) -> float | None:
    """Latest common shares outstanding from DEI (10-K/10-Q cover pages)."""
    fact = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding")
    if not fact:
        return None
    rows = [r for r in fact["units"].get("shares", []) if r.get("val")]
    if not rows:
        return None
    return float(max(rows, key=lambda r: r["end"])["val"])


def annual_financials(facts: dict, years: int = 10) -> pd.DataFrame:
    """Tidy per-fiscal-year DataFrame of the metrics DCF + ratios need.

    Index: fiscal year end date. Values in USD. Missing tags -> NaN columns.
    """
    all_facts = facts.get("facts", {})
    gaap = all_facts.get("us-gaap", {})
    if not gaap:
        if "ifrs-full" in all_facts:
            raise ValueError(
                "this company files under IFRS (20-F), not US GAAP — IFRS concept "
                "mapping isn't supported yet"
            )
        raise ValueError("no us-gaap facts in this filing history")

    columns: dict[str, dict[dt.date, float]] = {}
    for name, tags in DURATION_TAGS.items():
        columns[name] = _annual_series(gaap, tags, _is_annual_duration)
    fy_ends = set(columns["revenue"]) | set(columns["net_income"])

    def is_fy_end_instant(row: dict) -> bool:
        return row.get("form") in ANNUAL_FORMS and _parse_date(row["end"]) in fy_ends

    for name, tags in INSTANT_TAGS.items():
        columns[name] = _annual_series(gaap, tags, is_fy_end_instant)

    df = pd.DataFrame(columns).sort_index()
    df = df[df.index.isin(sorted(fy_ends))]
    if df.empty:
        raise ValueError("No annual 10-K facts found for this company")

    debt_parts = ["lt_debt", "st_debt", "other_st_debt"]
    df["total_debt"] = df[debt_parts].sum(axis=1, min_count=1)
    df["fcf"] = df["cfo"] - df["capex"]
    df["fiscal_year"] = [d.year for d in df.index]
    return df.tail(years)
