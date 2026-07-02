"""Discounted cash flow model on top of EDGAR annual financials.

Standard unlevered-lite DCF taught in valuation coursework:
  FCF = cash from operations - capex
  Project FCF with a growth rate that fades linearly to the terminal rate,
  discount at WACC, add a Gordon-growth terminal value, bridge enterprise
  value to equity with net debt, divide by shares outstanding.

Deliberate simplifications (documented in the app + README):
- FCF is CFO - capex (levered proxy) rather than a full NOPAT build.
- Net debt = total debt - cash & equivalents (no leases, no ST investments).
- WACC is a user assumption, not derived from beta/capital structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class DCFAssumptions:
    wacc: float = 0.09              # discount rate
    growth_start: float = 0.08      # FCF growth in year 1
    terminal_growth: float = 0.025  # perpetuity growth
    years: int = 5                  # explicit projection horizon


@dataclass
class DCFResult:
    assumptions: DCFAssumptions
    base_fcf: float
    projection: pd.DataFrame        # year, growth, fcf, discount_factor, pv
    pv_explicit: float
    terminal_value: float
    pv_terminal: float
    enterprise_value: float
    net_debt: float
    equity_value: float
    shares: float
    implied_price: float
    warnings: list[str] = field(default_factory=list)


def historical_fcf_cagr(fcf: pd.Series, max_years: int = 5) -> float | None:
    """CAGR of free cash flow over up to `max_years`, clamped to a sane band."""
    vals = fcf.dropna()
    if len(vals) < 2:
        return None
    vals = vals.tail(max_years + 1)
    first, last = float(vals.iloc[0]), float(vals.iloc[-1])
    n = len(vals) - 1
    if first <= 0 or last <= 0:
        return None
    cagr = (last / first) ** (1 / n) - 1
    return float(np.clip(cagr, -0.10, 0.25))


def run_dcf(financials: pd.DataFrame, shares: float, a: DCFAssumptions) -> DCFResult:
    if a.wacc <= a.terminal_growth + 0.005:
        raise ValueError("WACC must exceed terminal growth by at least 0.5% (Gordon growth breaks down)")
    if shares is None or shares <= 0:
        raise ValueError("Shares outstanding unavailable — cannot compute per-share value")

    warnings: list[str] = []
    fcf_hist = financials["fcf"].dropna()
    if fcf_hist.empty:
        raise ValueError("No free cash flow history (CFO or capex missing from filings)")
    base_fcf = float(fcf_hist.iloc[-1])
    if len(fcf_hist) >= 2:
        smoothed = float(fcf_hist.tail(2).mean())
        # Smooth a one-year spike/dip: base off the 2-year average when the
        # latest year deviates from it by more than 25%.
        if abs(base_fcf - smoothed) / abs(smoothed) > 0.25:
            warnings.append(
                f"Latest FCF (${base_fcf/1e9:,.1f}B) deviates >25% from the 2-year "
                f"average (${smoothed/1e9:,.1f}B); using the average as the base."
            )
            base_fcf = smoothed
    if base_fcf <= 0:
        raise ValueError("Base free cash flow is negative — a Gordon-growth DCF is not meaningful")

    growths = np.linspace(a.growth_start, a.terminal_growth, a.years)
    fcf, rows = base_fcf, []
    for t, g in enumerate(growths, start=1):
        fcf *= 1 + g
        df_t = (1 + a.wacc) ** -t
        rows.append({"year": t, "growth": g, "fcf": fcf, "discount_factor": df_t, "pv": fcf * df_t})
    proj = pd.DataFrame(rows)

    pv_explicit = float(proj["pv"].sum())
    terminal_value = proj["fcf"].iloc[-1] * (1 + a.terminal_growth) / (a.wacc - a.terminal_growth)
    pv_terminal = float(terminal_value * (1 + a.wacc) ** -a.years)
    ev = pv_explicit + pv_terminal

    last = financials.iloc[-1]
    debt = last.get("total_debt")
    cash = last.get("cash")
    if pd.isna(debt):
        warnings.append("Total debt not found in filings — treated as 0 in the net-debt bridge.")
        debt = 0.0
    if pd.isna(cash):
        warnings.append("Cash & equivalents not found in filings — treated as 0 in the net-debt bridge.")
        cash = 0.0
    net_debt = float(debt) - float(cash)
    equity_value = ev - net_debt

    return DCFResult(
        assumptions=a, base_fcf=base_fcf, projection=proj,
        pv_explicit=pv_explicit, terminal_value=float(terminal_value),
        pv_terminal=pv_terminal, enterprise_value=ev, net_debt=net_debt,
        equity_value=equity_value, shares=shares,
        implied_price=equity_value / shares, warnings=warnings,
    )


def sensitivity_grid(
    financials: pd.DataFrame,
    shares: float,
    base: DCFAssumptions,
    wacc_steps: int = 5,
    growth_steps: int = 5,
) -> pd.DataFrame:
    """Implied price grid over WACC (rows) x terminal growth (cols)."""
    waccs = np.round(np.linspace(base.wacc - 0.02, base.wacc + 0.02, wacc_steps), 4)
    growths = np.round(np.linspace(base.terminal_growth - 0.01, base.terminal_growth + 0.01, growth_steps), 4)
    grid = pd.DataFrame(index=waccs, columns=growths, dtype=float)
    for w in waccs:
        for g in growths:
            if w <= g + 0.005:
                grid.loc[w, g] = np.nan
                continue
            a = DCFAssumptions(wacc=float(w), growth_start=base.growth_start,
                               terminal_growth=float(g), years=base.years)
            try:
                grid.loc[w, g] = run_dcf(financials, shares, a).implied_price
            except ValueError:
                grid.loc[w, g] = np.nan
    grid.index.name = "WACC"
    grid.columns.name = "Terminal growth"
    return grid
