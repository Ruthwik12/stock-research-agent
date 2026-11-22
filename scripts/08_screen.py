"""Step 8: run the screener over the S&P 500 and save the shortlist.

1. Refresh the universe (current S&P 500 members) from Wikipedia.
2. Update prices for every member in bulk (first run backfills from config.UNIVERSE_HISTORY_START).
3. Stage A: price signals and triggers for everyone.
4. Load fundamentals and earnings for the stage-A candidates (skipped if fetched in the last 7 days).
5. Stage B + score; save to screener_results and print the shortlist.

Run from the project root:
    python scripts/08_screen.py [--as-of YYYY-MM-DD]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import screener  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.loaders import (fetched_within, load_earnings, load_financials,  # noqa: E402
                                load_prices_bulk, load_universe)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="default: latest benchmark trading day in the database")
    args = ap.parse_args()
    conn = connect()

    print(f"universe: {load_universe(conn)} S&P 500 members"); conn.commit()
    members = {r["ticker"]: r["cik"] for r in conn.execute("SELECT ticker, cik FROM universe")}
    tickers = sorted(set(members) | set(config.TICKERS) | {config.BENCHMARK})
    t0, added = time.time(), 0
    for i in range(0, len(tickers), 100):                  # Yahoo handles ~100 symbols per request well
        added += load_prices_bulk(conn, tickers[i:i + 100], config.UNIVERSE_HISTORY_START); conn.commit()
    print(f"prices: +{added} rows in {time.time() - t0:.0f}s")

    as_of = args.as_of or conn.execute("SELECT MAX(date) FROM prices_daily WHERE ticker = ?",
                                       (config.BENCHMARK,)).fetchone()[0]
    names = [t for t in tickers if t != config.BENCHMARK]
    a = screener.stage_a(conn, as_of, names)
    cands = screener.candidates(a)
    print(f"stage A ({as_of}): {len(names)} scanned, {len(cands)} candidates")

    ciks = {**members, **config.TICKERS}
    for t in cands:
        try:
            if ciks.get(t) and not fetched_within(conn, t, "financials", 7):
                load_financials(conn, t, ciks[t]); time.sleep(config.SEC_MIN_SECONDS_BETWEEN_REQUESTS)
            if not fetched_within(conn, t, "earnings", 7):
                load_earnings(conn, t)
            conn.commit()
        except Exception as exc:   # one company's missing data must not stop the screen
            conn.rollback()
            print(f"  {t}: data load failed ({exc}); scored on what is available")

    rows = screener.score(as_of, a, screener.stage_b(conn, as_of, cands))
    screener.save(conn, rows)
    names_by = dict(conn.execute("SELECT ticker, name FROM universe").fetchall())
    print(f"\nShortlist for {as_of}:")
    for r in rows:
        if r["shortlisted"] or r["reason"].startswith("human review"):
            s = r["signals"]
            fmt = lambda v, f: "-" if v is None else format(v, f)   # noqa: E731
            print(f"  {r['ticker']:6} {names_by.get(r['ticker'], '')[:28]:28} score {fmt(r['score'], '.2f'):5} "
                  f"6m vs SPY {fmt(s['excess_return_6m'], '+.1%'):7} E/P {fmt(s['earnings_yield'], '.1%'):6} "
                  f"surprise {fmt(s['eps_surprise_pct'], '+.1f'):6}  {r['reason']}")
    conn.close()


if __name__ == "__main__":
    main()
