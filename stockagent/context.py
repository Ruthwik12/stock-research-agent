"""Structured evidence for the agents: insider trades (SEC Form 4) and a macro snapshot (FRED).

Each function returns one evidence item in the same shape as a retrieved filing chunk, so the
research graph can cite it (E-IDs) and the verifier can check every number against its text.
Only data public on or before the as-of date is used. None means "no data", never "nothing happened".
"""
import sqlite3
from datetime import date, timedelta

import config

FORM4_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&owner=include"


def _item(chunk_id: str, ticker: str, form_type: str, available: str, section: str, text: str, url: str) -> dict:
    return {"chunk_id": chunk_id, "text": text, "distance": 0.0, "ticker": ticker, "form_type": form_type,
            "filing_date": available, "period_end": "", "section": section, "source_url": url}


def _cik(conn: sqlite3.Connection, ticker: str) -> str:
    row = conn.execute("SELECT cik FROM universe WHERE ticker = ?", (ticker,)).fetchone()
    return config.TICKERS.get(ticker) or (row[0] if row else "")


def insider_evidence(conn: sqlite3.Connection, ticker: str, as_of: str) -> dict | None:
    days = config.INSIDER_SUMMARY_DAYS
    start = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    available = conn.execute("SELECT MAX(filed) FROM insider_filings WHERE ticker = ? AND filed <= ?",
                             (ticker, as_of)).fetchone()[0]
    if not available:
        return None   # never fetched (or nothing public yet): unknown, not "no trades"
    rows = [dict(r) for r in conn.execute(
        """SELECT owner, relationship, txn_date, code, shares, price, planned FROM insider_trades
           WHERE ticker = ? AND filed <= ? AND txn_date >= ? AND txn_date <= ? AND code IN ('P', 'S')""",
        (ticker, as_of, start, as_of))]

    def describe(code: str, label: str) -> list[str]:
        trades = [r for r in rows if r["code"] == code and r["shares"]]
        if not trades:
            return [f"Open-market {label} (code {code}): none."]
        shares = sum(r["shares"] for r in trades)
        value = sum(r["shares"] * (r["price"] or 0) for r in trades)
        people = len({r["owner"] for r in trades})
        planned = sum(1 for r in trades if r["planned"] == 1)
        line = (f"Open-market {label} (code {code}): {len(trades)} transactions by {people} insiders, "
                f"{shares:,.0f} shares, about ${value:,.0f} in total")
        if code == "S":
            line += f"; {planned} of the {len(trades)} sales were under a Rule 10b5-1 trading plan"
        top = max(trades, key=lambda r: r["shares"] * (r["price"] or 0))
        role = f" ({top['relationship']})" if top["relationship"] else ""
        price = f" at ${top['price']:,.2f}" if top["price"] else ""
        return [line + ".", f"Largest: {top['owner']}{role}, {top['shares']:,.0f} shares on {top['txn_date']}{price}."]

    text = "\n".join([f"SEC Form 4 insider transactions in {ticker} common stock, {start} to {as_of} "
                      f"(filings public by {as_of}):"] + describe("P", "purchases") + describe("S", "sales") +
                     ["Grants, option exercises, tax withholding and gifts are excluded (not open-market decisions)."])
    return _item(f"form4:{ticker}:{as_of}", ticker, "Form 4 summary", available,
                 f"Insider trades, last {days} days", text, FORM4_URL.format(cik=int(_cik(conn, ticker) or 0)))


def _latest(conn: sqlite3.Connection, sid: str, on_or_before: str) -> tuple[str, float] | None:
    row = conn.execute("SELECT date, value FROM macro_series WHERE series_id = ? AND date <= ? "
                       "ORDER BY date DESC LIMIT 1", (sid, on_or_before)).fetchone()
    return (row[0], row[1]) if row else None


def macro_evidence(conn: sqlite3.Connection, ticker: str, as_of: str) -> dict | None:
    lines, newest = [], ""
    for sid, (label, kind, lag) in config.FRED_SERIES.items():
        # Only observations already published on the as-of date (monthly data comes out weeks later).
        cutoff = (date.fromisoformat(as_of) - timedelta(days=lag)).isoformat()
        now = _latest(conn, sid, cutoff)
        if not now:
            continue
        d, v = now
        newest = max(newest, d)
        if kind == "yoy":
            # Newest month whose year-earlier month exists (BLS skipped some months, e.g. October 2025).
            recent = conn.execute("SELECT date, value FROM macro_series WHERE series_id = ? AND date <= ? "
                                  "ORDER BY date DESC LIMIT 3", (sid, cutoff)).fetchall()
            for d, v in recent:
                ago = conn.execute("SELECT value FROM macro_series WHERE series_id = ? AND date = ?",
                                   (sid, f"{int(d[:4]) - 1}{d[4:]}")).fetchone()
                if ago:
                    lines.append(f"{label}: {(v / ago[0] - 1) * 100:.1f}% for {d[:7]} "
                                 f"(index {v:,.3f} vs {ago[0]:,.3f} a year earlier).")
                    break
            continue
        unit = "%" if kind == "rate" else ""
        if lag > 1:   # monthly series: name the month it describes
            lines.append(f"{label}: {v:.1f}{unit} for {d[:7]}.")
            continue
        past = _latest(conn, sid, (date.fromisoformat(d) - timedelta(days=91)).isoformat())
        before = f"; about 3 months earlier ({past[0]}) {past[1]:.2f}{unit}" if past else ""
        lines.append(f"{label}: {v:.2f}{unit} on {d}{before}.")
    if not lines:
        return None
    text = "\n".join([f"US macro snapshot from FRED (Federal Reserve Bank of St. Louis), data public by {as_of}:"]
                     + lines)
    return _item(f"fred:{as_of}", ticker, "FRED macro", newest, "Macro snapshot", text,
                 "https://fred.stlouisfed.org/")


def context_evidence(conn: sqlite3.Connection, ticker: str, as_of: str) -> list[dict]:
    return [e for e in (insider_evidence(conn, ticker, as_of), macro_evidence(conn, ticker, as_of)) if e]
