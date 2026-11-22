"""Step 13: company news from trusted publishers (Finnhub company-news API).

For each researchable company this fetches recent news (config.NEWS_LOOKBACK_DAYS), keeps only
items whose publisher is on config.NEWS_TRUSTED_SOURCES, and stores headline, summary and link
(never full article text) in market.db (news_items). Each kept item is also written to data/parsed/
as a one-section document so step 03 indexes it next to the filings, dated by its publish date.

Needs FINNHUB_API_KEY in .env (free key from finnhub.io). Without it the step does nothing.

Run from the project root:
    python scripts/13_news.py [--tickers AAPL,MSFT]
"""
import argparse
import html
import json
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.ondemand import researchable_tickers  # noqa: E402

URL = "https://finnhub.io/api/v1/company-news"


def trusted(source: str) -> bool:
    s = (source or "").lower()
    return any(t in s for t in config.NEWS_TRUSTED_SOURCES)


def clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def select_items(raw: list[dict], ticker: str, known: set[str]) -> tuple[list[dict], Counter]:
    """Trusted, new, de-duplicated items (newest first, capped), plus a count of untrusted sources skipped."""
    skipped, seen, out = Counter(), set(), []
    for it in sorted(raw, key=lambda r: -(r.get("datetime") or 0)):
        nid = f"finnhub-{it.get('id')}"
        headline = clean(it.get("headline", ""))
        if not trusted(it.get("source", "")):
            skipped[it.get("source") or "unknown"] += 1
            continue
        if nid in known or not headline or not it.get("url") or headline.lower() in seen:
            continue
        seen.add(headline.lower())
        published = datetime.fromtimestamp(it["datetime"], tz=timezone.utc).date().isoformat()
        out.append({"news_id": nid, "ticker": ticker, "published": published, "source": it["source"].strip(),
                    "headline": headline, "summary": clean(it.get("summary", "")), "url": it["url"]})
        if len(out) >= config.NEWS_MAX_PER_TICKER:
            break
    return out, skipped


def to_parsed_doc(item: dict) -> dict:
    """A news item as a one-section document in the same shape as a parsed filing."""
    doc_id = f"{item['ticker']}_news_{item['published']}_{item['news_id'].split('-')[-1]}"
    text = item["headline"] + ("\n" + item["summary"] if item["summary"] else "")
    return {"doc_id": doc_id, "ticker": item["ticker"], "form_type": "news", "filing_date": item["published"],
            "period_end": None, "accession": item["news_id"], "source_url": item["url"], "publisher": item["source"],
            "sections": [{"section": f"News: {item['source']}", "text": text, "chars": len(text)}]}


def fetch_day(ticker: str, day: str) -> tuple[list[dict], str | None]:
    """One day of company news, retrying rate limits (429) and dropped connections with a growing wait."""
    error = None
    for wait in (0, 10, 30, 60):
        time.sleep(wait)
        try:
            resp = requests.get(URL, params={"symbol": ticker, "from": day, "to": day,
                                             "token": config.FINNHUB_API_KEY}, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                error = f"HTTP {resp.status_code}"
                continue
            resp.raise_for_status()
            got = resp.json()
            return (got if isinstance(got, list) else []), None
        except requests.RequestException as exc:
            error = str(exc).replace(config.FINNHUB_API_KEY, "***")
    return [], error


def main() -> None:
    if not config.FINNHUB_API_KEY:
        print("FINNHUB_API_KEY not set in .env; news step skipped.")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers")
    ap.add_argument("--since", help="YYYY-MM-DD: backfill from this date even for companies already fetched")
    args = ap.parse_args()
    today = date.today()
    conn = connect()
    known = {r[0] for r in conn.execute("SELECT news_id FROM news_items")}
    config.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    all_skipped = Counter()
    for ticker in researchable_tickers(args.tickers):
        last = conn.execute("SELECT MAX(published) FROM news_items WHERE ticker = ?", (ticker,)).fetchone()[0]
        start = args.since or last or (today - timedelta(days=config.NEWS_LOOKBACK_DAYS)).isoformat()
        # Finnhub returns at most 250 items per call (a single busy day for a mega-cap), so ask one day at a time.
        raw, day, failed = [], date.fromisoformat(start), None
        while day <= today and not failed:
            got, failed = fetch_day(ticker, day.isoformat())
            raw += got
            day += timedelta(days=1)
            time.sleep(1.1)   # free plan: 60 calls per minute
        if failed:   # keep the days already fetched; the next run resumes from the newest stored item
            print(f"{ticker:6} stopped at {day - timedelta(days=1)}: {failed}")
        items, skipped = select_items(raw, ticker, known)
        all_skipped.update(skipped)
        for it in items:
            doc = to_parsed_doc(it)
            (config.PARSED_DIR / f"{doc['doc_id']}.json").write_text(json.dumps(doc, indent=1))
            conn.execute("INSERT OR REPLACE INTO news_items VALUES (?,?,?,?,?,?,?,?)",
                         (it["news_id"], ticker, it["published"], it["source"], it["headline"], it["summary"],
                          it["url"], doc["doc_id"]))
            known.add(it["news_id"])
        conn.commit()
        print(f"{ticker:6} {len(raw):>5} items since {start}, {len(items):>3} trusted and new kept")
    conn.close()
    if all_skipped:
        print("Skipped (not on the trusted list): " + ", ".join(f"{s} {n}" for s, n in all_skipped.most_common(8)))


if __name__ == "__main__":
    main()
