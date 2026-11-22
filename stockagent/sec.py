"""Shared SEC EDGAR access: identified, rate-limited GETs and the submissions list."""
import time

import requests

import config

_last_request = 0.0


def sec_get(url: str) -> requests.Response:
    """GET with SEC's required User-Agent and a polite rate limit."""
    global _last_request
    wait = config.SEC_MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers={"User-Agent": config.SEC_USER_AGENT}, timeout=30)
    _last_request = time.time()
    resp.raise_for_status()
    return resp


def recent_filings(cik: str) -> list[dict]:
    """Recent filings from the submissions API, one dict per filing, newest first."""
    data = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    recent = data["filings"]["recent"]
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument", "items"]
    return [dict(zip(keys, row)) for row in zip(*(recent[k] for k in keys))]


def archive_base(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
