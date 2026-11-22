"""Loaders that fill data/market.db from Yahoo Finance, SEC and Wikipedia. Shared by
scripts/07_market_data.py (core companies) and scripts/08_screen.py (the whole universe)."""
import io
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

import config

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def yahoo_symbol(ticker: str) -> str:
    """Yahoo writes share classes with a dash (BRK-B); the S&P list uses a dot (BRK.B)."""
    return ticker.replace(".", "-")


def load_universe(conn) -> int:
    """Replace the universe with the current S&P 500 members (ticker, name, sector, CIK)."""
    html = requests.get(SP500_URL, headers={"User-Agent": "CMU-capstone-research-agent/0.1"}, timeout=30).text
    df = pd.read_html(io.StringIO(html))[0]
    rows = [(yahoo_symbol(r["Symbol"]), r["Security"], r["GICS Sector"], r["GICS Sub-Industry"],
             str(int(r["CIK"])).zfill(10), str(r["Date added"]), date.today().isoformat())
            for _, r in df.iterrows()]
    if len(rows) < 400:   # a broken page must not wipe the universe
        raise ValueError(f"S&P 500 list looks wrong: only {len(rows)} rows")
    conn.execute("DELETE FROM universe")
    conn.executemany("INSERT INTO universe VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def _price_rows(ticker: str, hist: pd.DataFrame) -> list[tuple]:
    return [(ticker, idx.date().isoformat(), r["Open"], r["High"], r["Low"], r["Close"], int(r["Volume"]))
            for idx, r in hist.iterrows() if r["Close"] == r["Close"]]   # skip NaN closes


def load_prices(conn, ticker: str) -> int:
    last = conn.execute("SELECT MAX(date) FROM prices_daily WHERE ticker = ?", (ticker,)).fetchone()[0]
    start = (date.fromisoformat(last) + timedelta(days=1)).isoformat() if last else config.PRICE_HISTORY_START
    if start > date.today().isoformat():
        return 0
    rows = _price_rows(ticker, yf.Ticker(ticker).history(start=start, auto_adjust=True))
    conn.executemany("INSERT OR REPLACE INTO prices_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_prices_bulk(conn, tickers: list[str], start_floor: str) -> int:
    """One Yahoo request for many tickers, starting after the oldest 'last stored date' among them."""
    lasts = dict(conn.execute("SELECT ticker, MAX(date) FROM prices_daily GROUP BY ticker").fetchall())
    starts = [(date.fromisoformat(lasts[t]) + timedelta(days=1)).isoformat() if t in lasts else start_floor
              for t in tickers]
    start = min(starts)
    if start > date.today().isoformat():
        return 0
    data = yf.download(tickers, start=start, auto_adjust=True, progress=False, group_by="ticker", threads=True)
    rows = []
    for t in tickers:
        if t in data.columns.get_level_values(0):
            rows += _price_rows(t, data[t].dropna(how="all"))
    conn.executemany("INSERT OR REPLACE INTO prices_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def period_type(fact: dict) -> str | None:
    """Q for ~3-month, FY for ~12-month, I for balance-sheet instants; None for year-to-date spans."""
    if "start" not in fact:
        return "I"
    days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
    if 80 <= days <= 100:
        return "Q"
    if 350 <= days <= 380:
        return "FY"
    return None


def load_financials(conn, ticker: str, cik: str) -> int:
    resp = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                        headers={"User-Agent": config.SEC_USER_AGENT}, timeout=60)
    resp.raise_for_status()
    gaap = resp.json()["facts"].get("us-gaap", {})
    rows = []
    for metric, tags in config.FUNDAMENTAL_TAGS.items():
        # Companies switch tags over the years (Apple moved from Revenues to
        # RevenueFromContractWithCustomer...), so read every listed tag. Lowest priority first:
        # INSERT OR REPLACE then lets a preferred tag overwrite the same period.
        for tag in reversed(tags):
            if tag not in gaap:
                continue
            for unit, facts in gaap[tag]["units"].items():
                for f in facts:
                    ptype = period_type(f)
                    if ptype and f.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                        rows.append((ticker, metric, ptype, f["end"], f["filed"], f["form"], f["val"], unit, tag))
    conn.executemany("INSERT OR REPLACE INTO financials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    _mark_fetched(conn, ticker, "financials")
    return len(rows)


def load_earnings(conn, ticker: str) -> int:
    df = yf.Ticker(ticker).get_earnings_dates(limit=16)
    _mark_fetched(conn, ticker, "earnings")
    if df is None or df.empty:
        return 0
    nan_to_none = lambda v: None if v != v else float(v)   # noqa: E731
    rows = [(ticker, idx.date().isoformat(), nan_to_none(r.get("EPS Estimate")),
             nan_to_none(r.get("Reported EPS")), nan_to_none(r.get("Surprise(%)")))
            for idx, r in df.iterrows()]
    conn.executemany("INSERT OR REPLACE INTO earnings_events VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def load_company(conn, ticker: str, cik: str | None) -> None:
    try:
        info = yf.Ticker(ticker).info
    except Exception:   # company metadata is optional; never block the price load on it
        info = {}
    conn.execute("INSERT OR REPLACE INTO companies VALUES (?, ?, ?, ?, ?)",
                 (ticker, cik, info.get("longName"), info.get("sector"), info.get("industry")))


def _mark_fetched(conn, ticker: str, source: str) -> None:
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?, ?, ?)", (ticker, source, date.today().isoformat()))


def fetched_within(conn, ticker: str, source: str, days: int) -> bool:
    row = conn.execute("SELECT fetched FROM fetch_log WHERE ticker = ? AND source = ?", (ticker, source)).fetchone()
    return bool(row) and (date.today() - date.fromisoformat(row[0])).days < days
