"""Step 11: SEC Form 4 insider trades for the researchable companies.

For each company (core + prepared on demand, or --tickers) this reads its recent Form 4 filings
(config.INSIDER_LOOKBACK_DAYS), downloads each filing's XML once, and stores the common-stock
transactions in market.db (insider_trades). Incremental: filings already fetched are skipped.

Run from the project root:
    python scripts/11_insider_trades.py [--tickers AAPL,MSFT]
"""
import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.ondemand import researchable_tickers  # noqa: E402
from stockagent.sec import archive_base, recent_filings, sec_get  # noqa: E402


def _text(node, path: str) -> str | None:
    found = node.find(path)
    if found is None:
        return None
    value = found.findtext("value") if found.find("value") is not None else found.text
    return value.strip() if value and value.strip() else None


def _num(node, path: str) -> float | None:
    v = _text(node, path)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def parse_form4(xml_text: str) -> list[dict]:
    """Non-derivative (common stock) transactions in a Form 4, with owner and relationship."""
    root = ET.fromstring(xml_text)
    owners, roles = [], []
    for ro in root.findall("reportingOwner"):
        owners.append(_text(ro, "reportingOwnerId/rptOwnerName") or "")
        rel = ro.find("reportingOwnerRelationship")
        if rel is not None:
            if (_text(rel, "isDirector") or "").lower() in ("1", "true"):
                roles.append("Director")
            if (_text(rel, "isOfficer") or "").lower() in ("1", "true"):
                roles.append(_text(rel, "officerTitle") or "Officer")
            if (_text(rel, "isTenPercentOwner") or "").lower() in ("1", "true"):
                roles.append("10% owner")
    plan = (_text(root, "aff10b5One") or "").lower()
    planned = 1 if plan in ("1", "true") else 0 if plan in ("0", "false") else None
    rows = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        rows.append({
            "owner": "; ".join(o for o in owners if o), "relationship": ", ".join(dict.fromkeys(roles)),
            "txn_date": _text(t, "transactionDate"), "code": _text(t, "transactionCoding/transactionCode"),
            "shares": _num(t, "transactionAmounts/transactionShares"),
            "price": _num(t, "transactionAmounts/transactionPricePerShare"),
            "acquired_disposed": _text(t, "transactionAmounts/transactionAcquiredDisposedCode"),
            "planned": planned,
        })
    return rows


def main() -> None:
    if "@" not in config.SEC_USER_AGENT:
        sys.exit("Set SEC_USER_AGENT in .env first (name and contact email).")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers")
    args = ap.parse_args()
    cutoff = (date.today() - timedelta(days=config.INSIDER_LOOKBACK_DAYS)).isoformat()
    conn = connect()
    known = {r[0] for r in conn.execute("SELECT accession FROM insider_filings")}
    for ticker, cik in researchable_tickers(args.tickers).items():
        todo = [f for f in recent_filings(cik)
                if f["form"] == "4" and f["filingDate"] >= cutoff and f["accessionNumber"] not in known]
        todo = todo[:config.INSIDER_MAX_FILINGS]
        added = failed = 0
        for f in todo:
            # primaryDocument points at the rendered view ("xslF345X05/form4.xml"); the raw XML sits beside it.
            url = f"{archive_base(cik, f['accessionNumber'])}/{f['primaryDocument'].split('/')[-1]}"
            try:
                rows = parse_form4(sec_get(url).text)
            except Exception as exc:   # one malformed filing must not stop the rest
                failed += 1
                print(f"   {ticker} {f['accessionNumber']}: skipped ({exc})")
                continue
            conn.executemany(
                """INSERT OR REPLACE INTO insider_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(f["accessionNumber"], n, ticker, f["filingDate"], r["owner"], r["relationship"], r["txn_date"],
                  r["code"], r["shares"], r["price"], r["acquired_disposed"], r["planned"])
                 for n, r in enumerate(rows)])
            conn.execute("INSERT OR REPLACE INTO insider_filings VALUES (?,?,?,?,?)",
                         (f["accessionNumber"], ticker, f["filingDate"], url, len(rows)))
            conn.commit()
            added += len(rows)
        print(f"{ticker:6} {len(todo):>3} new Form 4 filings, {added:>4} transactions stored"
              + (f", {failed} failed" if failed else ""))
    conn.close()


if __name__ == "__main__":
    main()
