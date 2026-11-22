"""Step 1: download raw SEC EDGAR filings for the Checkpoint 3 slice.

For each ticker in config.TICKERS this fetches the most recent 10-K, 10-Q,
earnings 8-Ks (Item 2.02, keeping exhibit 99.1, the press release) and material-event
8-Ks from the last year (config.EVENT_8K_ITEMS: body plus exhibit 99.1), saves the
raw HTML under data/raw/<TICKER>/, and writes data/raw/manifest.json with the
metadata every later step needs (ticker, form, filing date, period, source URL).

Run from the project root:
    python scripts/01_download_filings.py
"""
import argparse
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.sec import archive_base, recent_filings, sec_get  # noqa: E402

# "ex99_1", "exhibit991", and a bare "ex99" / "xexx99" (Best Buy types its release plain EX-99): no digit = 99.1.
EX99_PATTERN = re.compile(r"ex(?:hibit)?[-_.]?99(?:[-_.]?(\d))?(?!\d)")


def find_ex99_1(cik: str, accession: str) -> str | None:
    """Return the file name of exhibit 99.1 (earnings press release) inside an 8-K, if any."""
    index = sec_get(f"{archive_base(cik, accession)}/index.json").json()
    # Companies name exhibits differently: "msft-ex99_1.htm", "a8-kex991...htm", "...exhibit991narrative.htm".
    exhibits = {}
    for item in index["directory"]["item"]:
        name = item["name"]
        m = EX99_PATTERN.search(name.lower())
        if m and name.lower().endswith((".htm", ".html")):
            exhibits.setdefault(int(m.group(1) or 1), name)
    if not exhibits:
        # Some filers use names with no "ex99" in them (NVIDIA: "q2fy27pr.htm"). The filing's
        # index page lists each document's official type, e.g. "EX-99.1".
        page = sec_get(f"{archive_base(cik, accession)}/{accession}-index.htm").text
        for tr in BeautifulSoup(page, "lxml").select("table.tableFile tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            m = re.fullmatch(r"EX-99(?:\.(\d))?", cells[3]) if len(cells) > 3 else None
            if m and cells[2].lower().endswith((".htm", ".html")):
                exhibits.setdefault(int(m.group(1) or 1), cells[2])
    # Prefer 99.1 (press release) over 99.2+ (supplements, slides).
    return exhibits[min(exhibits)] if exhibits else None


def event_items(items: str | None) -> list[str]:
    """Material-event items of an 8-K (config.EVENT_8K_ITEMS), in the filer's order."""
    return [i.strip() for i in (items or "").split(",") if i.strip() in config.EVENT_8K_ITEMS]


def select_filings(filings: list[dict], wanted: dict[str, int], today: str | None = None) -> list[tuple[str, dict]]:
    """Pick the most recent filings of each kind, `wanted[kind]` of each (newest first).
    Event 8-Ks count only inside config.EVENT_8K_LOOKBACK_DAYS."""
    today = date.fromisoformat(today) if today else date.today()
    event_cutoff = (today - timedelta(days=config.EVENT_8K_LOOKBACK_DAYS)).isoformat()
    picked, counts = [], {k: 0 for k in wanted}
    for f in filings:
        if f["form"] in ("10-K", "10-Q"):
            kind = f["form"]
        elif f["form"] == "8-K" and "2.02" in (f["items"] or ""):
            kind = "8-K-earnings"
        elif f["form"] == "8-K" and event_items(f["items"]) and f["filingDate"] >= event_cutoff:
            kind = "8-K-event"
        else:
            continue
        if kind not in wanted:
            continue
        if counts[kind] < wanted[kind]:
            picked.append((kind, f))
            counts[kind] += 1
        if counts == wanted:
            break
    return picked


def resolve_tickers(arg: str | None) -> dict[str, str]:
    """Ticker -> CIK. Default: core companies plus any company already prepared on demand."""
    conn = connect()
    universe = dict(conn.execute("SELECT ticker, cik FROM universe").fetchall())
    if arg:
        wanted = [t.strip().upper() for t in arg.split(",") if t.strip()]
    else:
        wanted = list(config.TICKERS) + [r[0] for r in conn.execute(
            "SELECT ticker FROM filing_status WHERE status = 'ready'")]
    conn.close()
    out = {}
    for t in dict.fromkeys(wanted):
        cik = config.TICKERS.get(t) or universe.get(t)
        if not cik:
            sys.exit(f"{t}: unknown ticker (not a core company or S&P 500 member)")
        out[t] = cik
    return out


def main() -> None:
    if "@" not in config.SEC_USER_AGENT:
        sys.exit('Set SEC_USER_AGENT first, e.g. PowerShell:  $env:SEC_USER_AGENT = "Your Name you@example.com"')
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="comma-separated; default: core companies + those prepared on demand")
    args = ap.parse_args()

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    # Incremental: keep every document downloaded before, keyed by accession number.
    existing = json.loads(config.MANIFEST_PATH.read_text()) if config.MANIFEST_PATH.exists() else []
    known = {d["accession"]: d for d in existing}
    manifest, new_docs = list(existing), []

    for ticker, cik in resolve_tickers(args.tickers).items():
        wanted = config.FILINGS_PER_TICKER if ticker in config.TICKERS else config.FILINGS_ON_DEMAND
        print(f"\n== {ticker} (CIK {cik})")
        for kind, f in select_filings(recent_filings(cik), wanted):
            if f["accessionNumber"] in known:
                d = known[f["accessionNumber"]]
                print(f"   {kind:13} filed {d['filing_date']}  already have")
                continue
            base = archive_base(cik, f["accessionNumber"])
            extra = {}
            doc_name = find_ex99_1(cik, f["accessionNumber"]) if kind == "8-K-earnings" else f["primaryDocument"]
            if not doc_name:   # results stated in the 8-K itself (no press-release exhibit), e.g. Regeneron
                doc_name = f["primaryDocument"]
                extra["earnings_from_body"] = True

            url = f"{base}/{doc_name}"
            out_dir = config.RAW_DIR / ticker
            out_dir.mkdir(exist_ok=True)
            out_path = out_dir / f"{kind}_{f['filingDate']}_{f['accessionNumber']}.htm"
            if not out_path.exists():
                out_path.write_bytes(sec_get(url).content)
            if kind == "8-K-event":
                # The 8-K body states the event; a press release (exhibit 99.1), if any, carries the detail.
                extra["items"] = event_items(f["items"])
                ex_name = find_ex99_1(cik, f["accessionNumber"])
                if ex_name:
                    ex_path = out_path.with_name(out_path.stem + "_ex99.htm")
                    if not ex_path.exists():
                        ex_path.write_bytes(sec_get(f"{base}/{ex_name}").content)
                    extra["exhibit_path"] = str(ex_path.relative_to(config.PROJECT_ROOT))

            doc_id = f"{ticker}_{kind}_{f['filingDate']}_{f['accessionNumber'][-6:]}"
            new_docs.append({**extra,
                "doc_id": doc_id,
                "ticker": ticker,
                "cik": cik,
                "form_type": kind,
                "filing_date": f["filingDate"],
                # For an 8-K, reportDate is the event date, not a fiscal period end.
                "period_end": (f["reportDate"] or None) if kind in ("10-K", "10-Q") else None,
                **({"event_date": f["reportDate"] or None} if kind == "8-K-event" else {}),
                "accession": f["accessionNumber"],
                "source_url": url,
                "local_path": str(out_path.relative_to(config.PROJECT_ROOT)),
                "size_kb": round(out_path.stat().st_size / 1024, 1),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            print(f"   {kind:13} filed {f['filingDate']}  period {new_docs[-1]['period_end'] or '-':10}  "
                  f"{new_docs[-1]['size_kb']:>8} KB  {doc_name}  NEW")

    manifest += new_docs
    manifest.sort(key=lambda d: (d["ticker"], d["filing_date"]))
    config.MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"\n{len(new_docs)} new, {len(manifest)} total documents. "
          f"Manifest: {config.MANIFEST_PATH.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
