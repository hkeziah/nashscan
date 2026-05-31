"""
nashscan — Scan stocks for Tom Nash's "Price Down, Business Up" mispricing gap.

For each ticker, pulls quarterly fundamentals and price data, then scores how
strongly the stock fits the quadrant where business quality is improving while
the stock price lags. Higher score = stronger "rubber band" setup.

Usage:
    nashscan TSLA,PLTR,AMZN                 # comma-separated tickers
    nashscan TSLA,PLTR -n 10                # show top 10
    nashscan TSLA,PLTR -o report.md         # write markdown to file
    nashscan TSLA,PLTR --detail             # show per-ticker breakdown

Install:
    pip install yfinance
"""

import argparse
import sys
from dataclasses import dataclass
from typing import Optional

import yfinance as yf


@dataclass
class NashScore:
    ticker: str
    name: str
    price: float
    price_12m_change_pct: float
    rev_growth_score: float       # 0-25: accelerating revenue growth
    margin_score: float           # 0-25: expanding operating margins
    fcf_score: float              # 0-20: growing free cash flow
    balance_sheet_score: float    # 0-15: cash-rich, low debt
    price_score: float            # 0-15: price is flat or down (gap potential)
    total: float                  # 0-100 composite
    capped: bool = False          # True if score was capped due to price run-up
    detail: dict = None           # underlying metrics


def safe_get(series, idx, default=None):
    """Safely get a value from a pandas Series by index."""
    try:
        val = series.iloc[idx]
        if hasattr(val, 'item'):
            return val.item()
        return float(val) if val is not None else None
    except (IndexError, KeyError, TypeError, ValueError):
        return default


def score_ticker(ticker_str: str) -> Optional[NashScore]:
    """Pull data and compute Nash score for a single ticker."""
    try:
        stock = yf.Ticker(ticker_str.strip().upper())
        info = stock.info
    except Exception:
        return None

    name = info.get("longName") or info.get("shortName") or ticker_str
    price = info.get("currentPrice") or info.get("regularMarketPreviousClose")
    if price is None or price == 0:
        return None

    # ── Price performance ────────────────────────────────────────────────
    try:
        hist = stock.history(period="1y")
        if len(hist) < 2:
            price_12m_change = 0.0
        else:
            price_12m_change = ((hist["Close"].iloc[-1] / hist["Close"].iloc[0]) - 1) * 100
    except Exception:
        price_12m_change = 0.0

    # ── Skip ETFs (they lack standard financial statements) ──────────────
    quote_type = info.get("quoteType", "").upper()
    sector = info.get("sector", "")
    is_etf = quote_type == "ETF"
    is_financial = "FINANCIAL" in sector.upper()
    if is_etf:
        detail = {"note": "ETF — skipped (no standard financial statements)"}
        return NashScore(
            ticker=ticker_str.strip().upper(), name=name, price=price,
            price_12m_change_pct=round(price_12m_change, 1),
            rev_growth_score=0, margin_score=0, fcf_score=0,
            balance_sheet_score=0, price_score=0, total=0, detail=detail,
        )

    # ── Quarterly financials ─────────────────────────────────────────────
    try:
        q_income = stock.quarterly_income_stmt
        q_balance = stock.quarterly_balance_sheet
        q_cashflow = stock.quarterly_cashflow
    except Exception:
        q_income = q_balance = q_cashflow = None

    detail = {
        "market_cap": info.get("marketCap"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "pe_compression": None,
    }

    # ── Revenue growth score (0-25) ──────────────────────────────────────
    rev_score = 0.0
    rev_yoy_growths = []
    if q_income is not None and "Total Revenue" in q_income.index:
        rev = q_income.loc["Total Revenue"]
        rev_vals = [safe_get(rev, i) for i in range(min(8, len(rev)))]
        rev_vals = [v for v in rev_vals if v is not None and v > 0]
        if len(rev_vals) >= 2:
            # YoY comparisons (Q vs Q-4)
            for i in range(len(rev_vals) - 4):
                if rev_vals[i + 4] and rev_vals[i + 4] > 0:
                    growth = ((rev_vals[i] / rev_vals[i + 4]) - 1) * 100
                    rev_yoy_growths.append(growth)

    if rev_yoy_growths:
        avg_growth = sum(rev_yoy_growths) / len(rev_yoy_growths)
        # Acceleration: compare avg of 2 most recent YoY quarters vs avg of 2 older
        if len(rev_yoy_growths) >= 4:
            recent_2 = sum(rev_yoy_growths[:2]) / 2
            older_2 = sum(rev_yoy_growths[2:4]) / 2
            accel = recent_2 - older_2
        elif len(rev_yoy_growths) >= 2:
            recent = rev_yoy_growths[0]
            older = sum(rev_yoy_growths[1:]) / max(len(rev_yoy_growths) - 1, 1)
            accel = recent - older
        else:
            accel = 0

        # Growth rate scoring
        if avg_growth >= 30:
            rev_score += 12
        elif avg_growth >= 15:
            rev_score += 8
        elif avg_growth >= 5:
            rev_score += 4
        elif avg_growth > 0:
            rev_score += 2

        # Acceleration bonus
        if accel > 10:
            rev_score += 13
        elif accel > 5:
            rev_score += 10
        elif accel > 0:
            rev_score += 7
        elif accel > -5:
            rev_score += 3

        detail["rev_yoy_avg_pct"] = round(avg_growth, 1)
        detail["rev_accel_pct"] = round(accel, 1)
    else:
        detail["rev_yoy_avg_pct"] = None
        detail["rev_accel_pct"] = None

    # ── Margin score (0-25) ──────────────────────────────────────────────
    margin_score = 0.0
    if q_income is not None and "Total Revenue" in q_income.index and "Operating Income" in q_income.index:
        rev = q_income.loc["Total Revenue"]
        op_income = q_income.loc["Operating Income"]
        margins = []
        for i in range(min(8, min(len(rev), len(op_income)))):
            r, oi = safe_get(rev, i), safe_get(op_income, i)
            if r and r > 0 and oi is not None:
                margins.append((oi / r) * 100)

        if margins:
            recent_margin = margins[0] if margins else 0
            older_margins = margins[1:4] if len(margins) > 1 else [0]
            avg_older = sum(older_margins) / max(len(older_margins), 1)

            # Absolute margin
            if recent_margin >= 30:
                margin_score += 12
            elif recent_margin >= 20:
                margin_score += 9
            elif recent_margin >= 10:
                margin_score += 6
            elif recent_margin > 0:
                margin_score += 3

            # Margin expansion
            margin_change = recent_margin - avg_older
            if margin_change > 10:
                margin_score += 13
            elif margin_change > 5:
                margin_score += 10
            elif margin_change > 2:
                margin_score += 7
            elif margin_change > 0:
                margin_score += 4

            detail["op_margin_pct"] = round(recent_margin, 1)
            detail["margin_change_pct"] = round(margin_change, 1)

    # ── FCF score (0-20) ─────────────────────────────────────────────────
    fcf_score = 0.0
    if q_cashflow is not None:
        # Try multiple field names
        fcf = None
        for field in ["Free Cash Flow", "FreeCashFlow"]:
            if field in q_cashflow.index:
                fcf = q_cashflow.loc[field]
                break
        if fcf is None:
            # Calculate: Operating CF - CapEx
            if "Operating Cash Flow" in q_cashflow.index and "Capital Expenditure" in q_cashflow.index:
                ocf = q_cashflow.loc["Operating Cash Flow"]
                capex = q_cashflow.loc["Capital Expenditure"]
                fcf_vals = []
                for i in range(min(8, min(len(ocf), len(capex)))):
                    o, c = safe_get(ocf, i), safe_get(capex, i)
                    if o is not None and c is not None:
                        fcf_vals.append(o + c)  # capex is negative
                if fcf_vals:
                    fcf_score += 10 if fcf_vals[0] > 0 else 0
                    if len(fcf_vals) >= 5:
                        growth = ((fcf_vals[0] / abs(fcf_vals[4])) - 1) * 100 if fcf_vals[4] != 0 else 0
                        if growth > 50:
                            fcf_score += 10
                        elif growth > 20:
                            fcf_score += 7
                        elif growth > 0:
                            fcf_score += 4
                        detail["fcf_growth_pct"] = round(growth, 1)
        elif hasattr(fcf, 'iloc'):
            fcf_val = safe_get(fcf, 0)
            if fcf_val is not None and fcf_val > 0:
                fcf_score += 10
            if len(fcf) >= 5:
                curr, prev = safe_get(fcf, 0), safe_get(fcf, 4)
                if curr and prev and prev != 0:
                    growth = ((curr / abs(prev)) - 1) * 100
                    if growth > 50:
                        fcf_score += 10
                    elif growth > 20:
                        fcf_score += 7
                    elif growth > 0:
                        fcf_score += 4
                    detail["fcf_growth_pct"] = round(growth, 1)

    # ── Balance sheet score (0-15) ───────────────────────────────────────
    bs_score = 0.0
    total_cash = info.get("totalCash") or info.get("cashAndShortInvestments")
    total_debt = info.get("totalDebt")
    if total_cash and total_debt:
        ratio = total_cash / total_debt if total_debt > 0 else 999
        if ratio >= 3:
            bs_score += 8
        elif ratio >= 1.5:
            bs_score += 5
        elif ratio >= 1:
            bs_score += 3
        detail["cash_debt_ratio"] = round(ratio, 2)
    elif total_cash and not total_debt:
        bs_score += 8
        detail["cash_debt_ratio"] = 999
    if total_cash and info.get("marketCap"):
        cash_pct = (total_cash / info["marketCap"]) * 100
        if cash_pct > 20:
            bs_score += 7
        elif cash_pct > 10:
            bs_score += 4
        elif cash_pct > 5:
            bs_score += 2
        detail["cash_pct_mcap"] = round(cash_pct, 1)

    # ── Price gap score (0-15) ───────────────────────────────────────────
    price_gap_score = 0.0
    # Stock is flat or down = higher gap potential
    if price_12m_change <= -20:
        price_gap_score += 12
    elif price_12m_change <= -10:
        price_gap_score += 9
    elif price_12m_change <= 0:
        price_gap_score += 6
    elif price_12m_change <= 10:
        price_gap_score += 3

    # P/E compression bonus
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    if trailing_pe and forward_pe and trailing_pe > 0 and forward_pe > 0:
        pe_compression = ((trailing_pe - forward_pe) / trailing_pe) * 100
        detail["pe_compression"] = round(pe_compression, 1)
        if pe_compression > 30:
            price_gap_score += 3
        elif pe_compression > 15:
            price_gap_score += 2
        elif pe_compression > 5:
            price_gap_score += 1

    total = rev_score + margin_score + fcf_score + bs_score + price_gap_score

    # ── Price ceiling penalty ────────────────────────────────────────────
    # Stocks up massively can't have a "Price Down" gap regardless of
    # fundamentals. Cap total score for stocks that have already run.
    capped = False
    if price_12m_change > 200:
        total = min(total, 20)
        capped = True
    elif price_12m_change > 100:
        total = min(total, 30)
        capped = True
    elif price_12m_change > 50:
        total = min(total, 42)
        capped = True
    elif price_12m_change > 30:
        total = min(total, 55)
        capped = True

    # ── Financial sector margin note ─────────────────────────────────────
    if is_financial:
        detail["financial_note"] = "Financial sector — operating margins not comparable to industrials"

    return NashScore(
        ticker=ticker_str.strip().upper(),
        name=name,
        price=price,
        price_12m_change_pct=round(price_12m_change, 1),
        rev_growth_score=round(rev_score, 1),
        margin_score=round(margin_score, 1),
        fcf_score=round(fcf_score, 1),
        balance_sheet_score=round(bs_score, 1),
        price_score=round(price_gap_score, 1),
        total=round(total, 1),
        capped=capped,
        detail=detail,
    )


def build_markdown(scores: list[NashScore]) -> str:
    """Generate a markdown report from scored tickers."""
    lines = [
        "# NashScan — Price Down, Business Up",
        "",
        "Stocks ranked by Tom Nash's mispricing framework: improving fundamentals + lagging price.",
        "",
        "| # | Ticker | Name | Score | Price | 12m Chg | Rev Growth | Margin | FCF | Balance | Gap |",
        "|---|--------|------|-------|-------|---------|------------|--------|-----|---------|-----|",
    ]
    for i, s in enumerate(scores):
        emoji = "🟢" if s.total >= 60 else "🟡" if s.total >= 40 else "🔴"
        cap_mark = " ⚠" if s.capped else ""
        lines.append(
            f"| {i+1} | **{s.ticker}** | {s.name[:30]} | "
            f"{emoji} {s.total:.0f}{cap_mark} | ${s.price:.2f} | "
            f"{s.price_12m_change_pct:+.1f}% | "
            f"{s.rev_growth_score:.0f}/25 | {s.margin_score:.0f}/25 | "
            f"{s.fcf_score:.0f}/20 | {s.balance_sheet_score:.0f}/15 | "
            f"{s.price_score:.0f}/15 |"
        )

    lines.append("")
    lines.append("## Scoring Framework")
    lines.append("| Category | Max | What It Measures |")
    lines.append("|----------|-----|------------------|")
    lines.append("| Revenue Growth | 25 | YoY growth rate + acceleration")
    lines.append("| Margin Quality | 25 | Operating margin level + expansion trend")
    lines.append("| Free Cash Flow | 20 | Positive FCF + growth trajectory")
    lines.append("| Balance Sheet | 15 | Cash/debt ratio + cash as % of market cap")
    lines.append("| Price Gap | 15 | Price decline/flatness + P/E compression")
    lines.append("| **Total** | **100** | Higher = stronger rubber-band setup")

    lines.append("")
    lines.append("## Nash Framework Interpretation")
    lines.append("- **70+**: Text-book 'Price Down, Business Up' — strong fundamentals, beaten-down price")
    lines.append("- **50-69**: Significant gap forming — worth deeper research")
    lines.append("- **30-49**: Mixed signals — some strengths, some weaknesses")
    lines.append("- **<30**: Weak fundamentals or already fully priced")

    return "\n".join(lines) + "\n"


def build_detail(scores: list[NashScore]) -> str:
    """Generate per-ticker detail section."""
    lines = ["", "## Per-Ticker Detail", ""]
    for s in scores:
        d = s.detail
        lines.append(f"### {s.ticker} — {s.name}")
        lines.append(f"- **Price:** ${s.price:.2f} | **12m:** {s.price_12m_change_pct:+.1f}%")
        if d.get("rev_yoy_avg_pct") is not None:
            lines.append(f"- **Rev Growth (avg YoY):** {d['rev_yoy_avg_pct']:+.1f}% | **Acceleration:** {d.get('rev_accel_pct', 0):+.1f}pp")
        if d.get("op_margin_pct") is not None:
            lines.append(f"- **Op Margin:** {d['op_margin_pct']:.1f}% | **Change:** {d.get('margin_change_pct', 0):+.1f}pp")
        if d.get("fcf_growth_pct") is not None:
            lines.append(f"- **FCF Growth:** {d['fcf_growth_pct']:+.1f}%")
        if d.get("cash_debt_ratio") is not None:
            lines.append(f"- **Cash/Debt:** {d['cash_debt_ratio']:.1f}x | **Cash % MCap:** {d.get('cash_pct_mcap', 0):.1f}%")
        trailing_pe = d.get("trailing_pe", "N/A")
        forward_pe = d.get("forward_pe", "N/A")
        pe_comp = d.get("pe_compression")
        pe_str = f"{pe_comp:+.1f}%" if pe_comp is not None else "N/A"
        lines.append(f"- **P/E:** TTM {trailing_pe} → Fwd {forward_pe} | **Compression:** {pe_str}")
        lines.append(f"- **Scores:** Rev {s.rev_growth_score:.0f}/25 | Margin {s.margin_score:.0f}/25 | FCF {s.fcf_score:.0f}/20 | BS {s.balance_sheet_score:.0f}/15 | Gap {s.price_score:.0f}/15 | **Total {s.total:.0f}/100**")
        if s.capped:
            lines.append(f"- ⚠ **Score capped** — stock is up {s.price_12m_change_pct:+.0f}% in 12 months (too extended for 'Price Down' thesis)")
        if d.get("financial_note"):
            lines.append(f"- ⚠ {d['financial_note']}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Scan stocks for 'Price Down, Business Up' mispricing (Tom Nash framework)."
    )
    parser.add_argument("tickers", help="Comma-separated ticker symbols")
    parser.add_argument("-n", "--top", type=int, default=0, help="Show only top N results")
    parser.add_argument("-o", "--output", default=None, help="Write markdown report to file")
    parser.add_argument("--detail", action="store_true", help="Include per-ticker detail section")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.replace(" ", "").split(",") if t.strip()]
    if not tickers:
        print("Error: No tickers provided.", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {len(tickers)} tickers...", file=sys.stderr)

    # Fetch all tickers
    scores = []
    failed = []
    for t in tickers:
        try:
            result = score_ticker(t)
            if result:
                scores.append(result)
            else:
                failed.append(t)
        except Exception as e:
            failed.append(f"{t} ({e})")

    # Sort by total score descending
    scores.sort(key=lambda s: s.total, reverse=True)

    if args.top and args.top > 0:
        scores = scores[:args.top]

    # Build report
    report = build_markdown(scores)
    if args.detail:
        report += build_detail(scores)

    if failed:
        report += f"\n\n⚠ Failed: {', '.join(failed)}\n"

    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
