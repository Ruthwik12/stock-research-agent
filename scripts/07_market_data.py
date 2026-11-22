"""Step 7: load structured market data into data/market.db.

- prices_daily: adjusted daily prices from Yahoo Finance (yfinance) for config.TICKERS + the benchmark
- financials: SEC XBRL company facts (config.FUNDAMENTAL_TAGS), keeping each value's `filed` date
- earnings_events: EPS estimate vs reported, from Yahoo Finance
- companies: name, sector, industry

Incremental: prices continue from the last stored date; financials and earnings are upserted.

Run from the project root:
    python scripts/07_market_data.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.loaders import load_company, load_earnings, load_financials, load_prices  # noqa: E402


def main() -> None:
    if "@" not in config.SEC_USER_AGENT:
        sys.exit("Set SEC_USER_AGENT in .env first (a name and contact email).")
    conn = connect()
    failures = 0
    for ticker, cik in [*config.TICKERS.items(), (config.BENCHMARK, None)]:
        try:
            n_prices = load_prices(conn, ticker)
            n_fin = load_financials(conn, ticker, cik) if cik else 0
            n_earn = load_earnings(conn, ticker) if cik else 0
            load_company(conn, ticker, cik)
            conn.commit()
            print(f"{ticker:5} prices +{n_prices:<4} financial facts {n_fin:<5} earnings events {n_earn}")
        except Exception as exc:   # one bad ticker must not stop the others; report and continue
            conn.rollback()
            failures += 1
            print(f"{ticker:5} FAILED: {exc}")
    for table in ("companies", "prices_daily", "financials", "earnings_events"):
        print(f"{table:16} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>7} rows")
    conn.close()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
