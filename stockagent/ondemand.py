"""Prepare filings for companies outside the core list (download, parse, index), on request.

Status lives in the filing_status table: queued -> running -> ready | failed. A single lock file
makes sure only one pipeline run (on-demand or the nightly job) writes to the index at a time.
"""
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime

import config
from stockagent.db import connect
from stockagent.vectorstore import get_collection

LOCK_PATH = config.DATA_DIR / "pipeline.lock"
STALE_LOCK_SECONDS = 4 * 3600     # a lock older than this was left by a crashed run


def _owner_dead() -> bool:
    """True if the process that wrote the lock no longer exists (it crashed without releasing the lock)."""
    try:
        pid = int(LOCK_PATH.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False   # being written right now, or already gone
    if os.name != "nt":
        try:
            os.kill(pid, 0)   # signal 0 only checks existence on POSIX
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return k32.GetLastError() != 5             # 5 = access denied: it exists
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(handle, ctypes.byref(code))
    k32.CloseHandle(handle)
    return code.value != 259                       # 259 = STILL_ACTIVE


@contextmanager
def pipeline_lock(wait_seconds: int = 4 * 3600):
    """Exclusive lock across processes, using an atomically created file."""
    deadline = time.time() + wait_seconds
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {datetime.now().isoformat()}".encode())
            os.close(fd)
            break
        except FileExistsError:
            if _owner_dead() or time.time() - LOCK_PATH.stat().st_mtime > STALE_LOCK_SECONDS:
                LOCK_PATH.unlink(missing_ok=True)
                continue
            if time.time() > deadline:
                raise TimeoutError("another pipeline run holds the lock")
            time.sleep(5)
    try:
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def set_status(ticker: str, status: str, message: str = "") -> None:
    conn = connect()
    conn.execute("INSERT OR REPLACE INTO filing_status VALUES (?, ?, ?, ?)",
                 (ticker, status, datetime.now().isoformat(timespec="seconds"), message))
    conn.commit()
    conn.close()


def get_status(ticker: str) -> dict:
    if ticker in config.TICKERS:
        return {"ticker": ticker, "status": "ready", "message": "core company"}
    conn = connect()
    row = conn.execute("SELECT * FROM filing_status WHERE ticker = ?", (ticker,)).fetchone()
    conn.close()
    return dict(row) if row else {"ticker": ticker, "status": "not_prepared", "message": ""}


def researchable_tickers(arg: str | None = None) -> dict[str, str]:
    """Ticker -> CIK: the core companies plus those prepared on demand, or the comma-separated `arg`."""
    conn = connect()
    universe = dict(conn.execute("SELECT ticker, cik FROM universe").fetchall())
    if arg:
        wanted = [t.strip().upper() for t in arg.split(",") if t.strip()]
    else:
        wanted = list(config.TICKERS) + [r[0] for r in conn.execute(
            "SELECT ticker FROM filing_status WHERE status = 'ready'")]
    conn.close()
    return {t: config.TICKERS.get(t) or universe.get(t) for t in dict.fromkeys(wanted)
            if config.TICKERS.get(t) or universe.get(t)}


def is_researchable(ticker: str) -> bool:
    return get_status(ticker)["status"] == "ready"


def indexed_chunks(ticker: str) -> int:
    return len(get_collection().get(where={"ticker": ticker}, include=[])["ids"])


def prepare(tickers: list[str]) -> None:
    """Download, parse and index filings for these tickers (takes the pipeline lock)."""
    for t in tickers:
        set_status(t, "queued", "waiting for the pipeline lock")
    try:
        with pipeline_lock():
            prepare_locked(tickers)
    except TimeoutError as exc:
        for t in tickers:
            set_status(t, "failed", str(exc))


def prepare_locked(tickers: list[str]) -> None:
    """Same as prepare(), for a caller that already holds the pipeline lock (the nightly job)."""
    py, scripts = sys.executable, config.PROJECT_ROOT / "scripts"
    for t in tickers:
        set_status(t, "running", "downloading, parsing and indexing filings")
    try:
        joined = ",".join(tickers)
        for step in (["01_download_filings.py", "--tickers", joined], ["02_parse_sections.py"],
                     ["13_news.py", "--tickers", joined], ["03_ingest.py"],
                     ["11_insider_trades.py", "--tickers", joined]):
            result = subprocess.run([py, str(scripts / step[0]), *step[1:]], cwd=config.PROJECT_ROOT,
                                    capture_output=True, text=True, timeout=3 * 3600)
            if result.returncode != 0 and step[0] in ("13_news.py", "11_insider_trades.py"):
                continue   # news and insider trades are extras: filings alone make a company researchable
            if result.returncode != 0:
                raise RuntimeError(f"{step[0]} failed: {(result.stderr or result.stdout)[-300:]}")
    except Exception as exc:   # record the failure per ticker; never leave a ticker stuck in "running"
        for t in tickers:
            set_status(t, "failed", str(exc)[:500])
        return
    for t in tickers:
        n = indexed_chunks(t)
        set_status(t, "ready" if n else "failed", f"{n} chunks indexed" if n else "no usable filings found")


def nightly_candidates() -> list[str]:
    """Top shortlisted (by score) non-core companies from the latest screen that aren't prepared yet."""
    conn = connect()
    rows = conn.execute(
        """SELECT s.ticker FROM screener_results s LEFT JOIN filing_status f ON f.ticker = s.ticker
           WHERE s.as_of = (SELECT MAX(as_of) FROM screener_results) AND s.shortlisted = 1
             AND s.score IS NOT NULL AND (f.status IS NULL OR f.status = 'failed')
           ORDER BY s.score DESC""").fetchall()
    conn.close()
    return [r[0] for r in rows if r[0] not in config.TICKERS][:config.ON_DEMAND_NIGHTLY]
