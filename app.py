"""EDGAR-to-DCF equity valuation dashboard.

Type a ticker; the app pulls that company's audited 10-K history straight from
SEC EDGAR, computes the ratio picture, and runs an interactive DCF with your
assumptions — implied value per share vs. the market price, with sensitivity.

Run locally:
    streamlit run app.py
"""

import difflib

import numpy as np
import pandas as pd
import streamlit as st

import charts
import dcf as dcf_mod
import edgar
import quotes

st.set_page_config(page_title="Equity DCF — 10-K to Value", page_icon="📊", layout="wide")

# --------------------------------------------------------------------------- #
# Cached data access
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=24 * 3600, show_spinner=False)
def ticker_map() -> dict:
    return edgar.load_ticker_map()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_company(ticker: str):
    info = edgar.lookup_company(ticker, ticker_map())
    facts = edgar.fetch_company_facts(info["cik"])
    fin = edgar.annual_financials(facts)
    shares = edgar.shares_outstanding(facts)
    return info, fin, shares


@st.cache_data(ttl=1800, show_spinner=False)
def load_quote(ticker: str):
    return quotes.fetch_quote(ticker)


def table_view(df: pd.DataFrame, label: str = "Table view") -> None:
    """Accessibility twin for each chart: the same numbers as a table."""
    with st.expander(label):
        st.dataframe(df, use_container_width=True)


def money(v: float) -> str:
    """$1.76T / $95B — human scale for headline tiles."""
    if abs(v) >= 1e12:
        return f"${v / 1e12:,.2f}T"
    return f"${v / 1e9:,.0f}B"


# --------------------------------------------------------------------------- #
# Sidebar part 1 — which company
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Company")
    ticker = st.text_input("Ticker (US-listed)", value="AAPL", max_chars=10).strip().upper()

if not ticker:
    st.info("Enter a ticker in the sidebar to begin.")
    st.stop()

try:
    with st.spinner(f"Pulling {ticker} 10-K history from SEC EDGAR…"):
        info, fin, shares = load_company(ticker)
except KeyError:
    st.error(f"**{ticker}** isn't in the SEC's company list.")
    close = difflib.get_close_matches(ticker, list(ticker_map().keys()), n=5, cutoff=0.6)
    if close:
        st.info("Did you mean: " + " · ".join(f"**{c}**" for c in close) + "?")
    st.stop()
except ValueError as e:
    st.error(f"EDGAR has filings for **{ticker}**, but {e}.")
    st.caption("Banks and insurers report under concepts this FCF-based DCF doesn't "
               "use (no capex, negative CFO by design), and IFRS/20-F filers use a "
               "different taxonomy — both are out of scope for this model.")
    st.stop()
except Exception as e:
    st.error(f"Couldn't reach SEC EDGAR ({type(e).__name__}). Try again in a minute.")
    st.stop()

quote = load_quote(ticker)
fcf_cagr = dcf_mod.historical_fcf_cagr(fin["fcf"])

# --------------------------------------------------------------------------- #
# Sidebar part 2 — assumptions, seeded from this company's own history.
# Widget keys include the ticker so switching companies re-seeds the sliders.
# --------------------------------------------------------------------------- #
# Snap to the slider's 0.5 step so the seeded default sits on the grid.
default_growth = round(fcf_cagr * 100 * 2) / 2 if fcf_cagr is not None else 8.0
with st.sidebar:
    st.header("DCF assumptions")
    years = st.slider("Projection horizon (years)", 3, 10, 5, key=f"years_{ticker}")
    wacc_pct = st.slider("Discount rate / WACC (%)", 6.0, 15.0, 9.0, 0.25, key=f"wacc_{ticker}")
    growth_pct = st.slider(
        "FCF growth, year 1 (%)", -10.0, 25.0, float(default_growth), 0.5,
        key=f"growth_{ticker}",
        help="Seeded from this company's historical FCF CAGR (shown under the title) "
             "when it's computable; otherwise 8%.",
    )
    terminal_pct = st.slider("Terminal growth (%)", 0.0, 4.0, 2.5, 0.25, key=f"term_{ticker}",
                             help="Long-run growth into perpetuity; usually at or below "
                                  "nominal GDP growth (~2-3%).")

    st.header("Market price")
    if quote is None:
        st.caption("⚠️ Quote lookup failed (rate limit or unknown symbol) — enter a price "
                   "below to compare against the DCF.")
    else:
        st.caption("Auto-fetched; override if stale.")
    price_override = st.number_input("Price override ($, 0 = use auto quote)",
                                     min_value=0.0, value=0.0, step=1.0)

market_price = price_override if price_override > 0 else (quote.price if quote else None)

# --------------------------------------------------------------------------- #
# Header + hero tiles
# --------------------------------------------------------------------------- #
st.title(f"{info['title']} ({ticker})")
fy_lo, fy_hi = int(fin["fiscal_year"].iloc[0]), int(fin["fiscal_year"].iloc[-1])
share_txt = f"{shares / 1e9:,.2f}B shares" if shares else "shares outstanding unavailable"
cagr_txt = f" · FCF CAGR (hist.) {fcf_cagr * 100:.1f}%" if fcf_cagr is not None else ""
st.caption(f"SEC EDGAR CIK {info['cik']} · fiscal years {fy_lo}–{fy_hi} · {share_txt}{cagr_txt}")

assump = dcf_mod.DCFAssumptions(
    wacc=wacc_pct / 100, growth_start=growth_pct / 100,
    terminal_growth=terminal_pct / 100, years=years,
)

dcf_error = None
try:
    result = dcf_mod.run_dcf(fin, shares, assump)
except ValueError as e:
    dcf_error = str(e)
    result = None

if result:
    upside = (result.implied_price / market_price - 1) * 100 if market_price else None
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("DCF value / share", f"${result.implied_price:,.2f}")
    t2.metric("Market price", f"${market_price:,.2f}" if market_price else "—",
              help=None if market_price else "Quote unavailable — set a price override in the sidebar.")
    t3.metric("Upside vs market", f"{upside:+.1f}%" if upside is not None else "—",
              delta=f"{upside:+.1f}%" if upside is not None else None)
    t4.metric("Enterprise value", money(result.enterprise_value))
    for w in result.warnings:
        # Streamlit renders markdown: a pair of $ signs flips into LaTeX math
        # and mangles dollar amounts — escape them.
        st.warning(w.replace("$", "\\$"))
else:
    st.error(f"DCF not computable with these inputs: {dcf_error}")

# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
tab_val, tab_fin, tab_ratio, tab_data = st.tabs(
    ["Valuation", "Financials", "Ratios", "Data & method"]
)

with tab_val:
    if result:
        st.subheader("Free cash flow — history and projection")
        st.plotly_chart(charts.fig_fcf_projection(fin, result.projection), use_container_width=True)
        proj_tbl = pd.DataFrame({
            "Year": result.projection["year"],
            "Growth %": (result.projection["growth"] * 100).round(2),
            "FCF ($B)": (result.projection["fcf"] / 1e9).round(2),
            "Discount factor": result.projection["discount_factor"].round(3),
            "PV ($B)": (result.projection["pv"] / 1e9).round(2),
        }).set_index("Year")
        table_view(proj_tbl, "Table view — projection")

        st.subheader("Sensitivity — implied price across WACC × terminal growth")
        st.caption("Neutral gray = today's market price; blue = implied value above market, "
                   "red = below. The outlined cell is your current assumptions.")
        grid = dcf_mod.sensitivity_grid(fin, shares, assump)
        st.plotly_chart(charts.fig_sensitivity(grid, market_price), use_container_width=True)
        table_view(grid.round(2).rename(columns=lambda g: f"{g*100:.2f}%",
                                        index=lambda w: f"{w*100:.1f}%"),
                   "Table view — sensitivity grid")

        sens_vals = grid.values[np.isfinite(grid.values.astype(float))]
        if sens_vals.size:
            st.subheader("Where the DCF lands")
            st.plotly_chart(
                charts.fig_football_field(
                    result.implied_price, float(sens_vals.min()), float(sens_vals.max()),
                    quote.week52_low if quote else None, quote.week52_high if quote else None,
                    market_price,
                ),
                use_container_width=True,
            )

        st.subheader("Value bridge")
        bridge = pd.DataFrame({
            "Component": ["PV of explicit FCF", "PV of terminal value", "Enterprise value",
                          "Less: net debt", "Equity value", "÷ shares → per share"],
            "USD": [result.pv_explicit, result.pv_terminal, result.enterprise_value,
                    -result.net_debt, result.equity_value, result.implied_price],
        })
        bridge["USD"] = bridge["USD"].map(
            lambda v: f"-${abs(v)/1e9:,.1f}B" if v < -1e4 else
                      f"${v/1e9:,.1f}B" if v > 1e4 else f"${v:,.2f}")
        st.dataframe(bridge, use_container_width=True, hide_index=True)

with tab_fin:
    st.subheader("Revenue and net income")
    st.plotly_chart(charts.fig_revenue_income(fin), use_container_width=True)
    st.subheader("Margins")
    if fin["gross_profit"].isna().all():
        st.caption(f"{info['title']} doesn't report a GrossProfit line in its filings — "
                   "gross margin is omitted.")
    st.plotly_chart(charts.fig_margins(fin), use_container_width=True)
    fin_tbl = fin[["fiscal_year", "revenue", "gross_profit", "operating_income",
                   "net_income", "cfo", "capex", "fcf"]].set_index("fiscal_year")
    fin_tbl = fin_tbl.dropna(axis=1, how="all")  # hide concepts this filer never reports
    table_view((fin_tbl / 1e9).round(2), "Table view — financials (USD billions)")

with tab_ratio:
    st.subheader("Financial health, at a glance")
    st.plotly_chart(charts.fig_ratio_panels(fin), use_container_width=True)
    ratios = pd.DataFrame({
        "fiscal_year": fin["fiscal_year"],
        "ROE %": (fin["net_income"] / fin["equity"] * 100).round(1),
        "FCF margin %": (fin["fcf"] / fin["revenue"] * 100).round(1),
        "Current ratio": (fin["assets_current"] / fin["liabilities_current"]).round(2),
        "Debt/equity": (fin["total_debt"] / fin["equity"]).round(2),
    }).set_index("fiscal_year").dropna(axis=1, how="all")
    table_view(ratios, "Table view — ratios")

with tab_data:
    st.subheader("Raw annual facts (as filed)")
    st.caption("Every number above traces to these 10-K facts — pulled live from "
               f"[SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={info['cik']}&type=10-K), "
               "no manual inputs.")
    st.dataframe(fin, use_container_width=True)
    st.download_button("Download annual financials (CSV)", fin.to_csv().encode(),
                       file_name=f"{ticker}_annual_financials.csv", mime="text/csv")
    st.subheader("Method and limitations")
    st.markdown(
        """
- **FCF** = cash from operations − capex (levered proxy; no full NOPAT build).
- **Projection**: year-1 growth fades **linearly** to the terminal rate over the horizon.
- **Terminal value**: Gordon growth on the final projected FCF, discounted at WACC.
- **Equity bridge**: enterprise value − net debt (total debt − cash; leases and
  short-term investments excluded — a real model would add both).
- **WACC is an assumption**, not derived from beta/capital structure. Use the
  sensitivity grid, not the point estimate.
- **Shares**: latest cover-page count from DEI facts (includes post-FY buybacks).
- Not investment advice — a transparent, reproducible teaching model.
        """
    )
