# nashscan

Scan stocks for Tom Nash's "Price Down, Business Up" mispricing gap.

Pulls quarterly fundamentals and price data via Yahoo Finance (free, no API key),
then scores each stock on how strongly it fits the quadrant where business quality
is improving while the stock price lags.

## Setup

```bash
pip install yfinance
```

## Usage

```bash
# Scan a list of tickers
./nashscan PLTR,TSLA,MSFT,AMZN

# Top N results only
./nashscan PLTR,TSLA,MSFT,AMZN -n 5

# Full detail with per-ticker breakdown
./nashscan PLTR,TSLA --detail

# Write to file
./nashscan PLTR,TSLA,MSFT -o report.md
```

## Scoring Framework (100 points)

| Category | Max | What It Measures |
|----------|-----|------------------|
| Revenue Growth | 25 | YoY growth rate + acceleration trend |
| Margin Quality | 25 | Operating margin level + expansion |
| Free Cash Flow | 20 | Positive FCF + growth trajectory |
| Balance Sheet | 15 | Cash/debt ratio + cash % of market cap |
| Price Gap | 15 | Price decline/flatness + P/E compression |

Stocks up >30% in 12 months get score-capped — the price has already run too far
for a "Price Down" thesis.

## Interpretation

- **70+**: Textbook setup — strong fundamentals, beaten-down price
- **50-69**: Significant gap forming — worth deeper research
- **30-49**: Mixed signals
- **<30**: Weak fundamentals or already fully priced
- **⚠ Capped**: Score artificially limited — stock has already run up heavily

## Limitations

- Uses quarterly data from Yahoo Finance (may lag by 1 quarter)
- Financial sector stocks (banks) don't have standard operating margins
- FCF calculations are approximate for some companies
- Not financial advice — a screening tool, not a buy/sell signal
