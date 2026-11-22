"""SQLite database for structured market data (the source of truth for exact numbers).

Every table keeps the date the information became public (price date, SEC `filed` date,
earnings date) so every query can be limited to what was known on a prediction date.
"""
import sqlite3
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    cik TEXT,
    name TEXT,
    sector TEXT,
    industry TEXT
);
CREATE TABLE IF NOT EXISTS prices_daily (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,            -- YYYY-MM-DD trading day
    open REAL, high REAL, low REAL,
    close REAL NOT NULL,           -- adjusted for splits and dividends (total return)
    volume INTEGER,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS financials (   -- long format: a new metric adds rows, not columns
    ticker TEXT NOT NULL,
    metric TEXT NOT NULL,          -- e.g. revenue, eps_diluted (see config.FUNDAMENTAL_TAGS)
    period_type TEXT NOT NULL,     -- Q (3-month), FY (12-month), I (instant, balance sheet)
    period_end TEXT NOT NULL,
    filed TEXT NOT NULL,           -- date the filing became public: the as-of key
    form TEXT,
    value REAL NOT NULL,
    unit TEXT,
    tag TEXT,
    PRIMARY KEY (ticker, metric, period_type, period_end, filed)
);
CREATE TABLE IF NOT EXISTS universe (     -- companies the screener scans (current S&P 500 members)
    ticker TEXT PRIMARY KEY,               -- Yahoo symbol (BRK-B, not BRK.B)
    name TEXT,
    sector TEXT,
    sub_industry TEXT,
    cik TEXT,
    date_added TEXT,                       -- date added to the index
    refreshed TEXT                         -- date this list was downloaded
);
CREATE TABLE IF NOT EXISTS fetch_log (     -- when per-company data was last fetched (avoids daily re-downloads)
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,                  -- financials | earnings
    fetched TEXT NOT NULL,
    PRIMARY KEY (ticker, source)
);
CREATE TABLE IF NOT EXISTS predictions (   -- long-term memory: every forecast, later scored
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    ticker TEXT NOT NULL,
    as_of TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    p_quant REAL NOT NULL,                 -- calibrated model probability
    evidence_adjustment REAL NOT NULL,     -- capped sum of research adjustments, before shrinkage
    shrink_weight REAL NOT NULL,           -- share of the adjustment applied (grows with resolved history)
    p_final REAL NOT NULL,
    needs_review INTEGER NOT NULL,         -- outside config.REVIEW_BAND
    features_json TEXT,
    adjustments_json TEXT,
    report_trace TEXT,                     -- research run the adjustments came from
    status TEXT NOT NULL DEFAULT 'open',   -- open | resolved
    outcome INTEGER,                       -- 1 if the stock beat the benchmark over the horizon
    excess_return REAL,
    resolved_on TEXT
);
CREATE TABLE IF NOT EXISTS rules (         -- self-correction rules proposed by the reflector
    rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    field TEXT NOT NULL,                   -- context field, e.g. sector, momentum_bucket
    value TEXT NOT NULL,
    delta REAL NOT NULL,                   -- probability points added for matching forecasts
    status TEXT NOT NULL,                  -- proposed | active | rejected | retired
    evidence_json TEXT,                    -- sample sizes and Brier before/after on held-out data
    decided TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS positions (     -- paper positions only; no real trading tool exists
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    prediction_id INTEGER,
    run_id INTEGER,
    opened TEXT NOT NULL,                  -- entry date (as-of date of the pick)
    entry_price REAL NOT NULL,
    bench_entry REAL NOT NULL,             -- benchmark close on the entry date
    horizon_end TEXT NOT NULL,
    thesis_json TEXT,                      -- thesis and evidence IDs at entry
    status TEXT NOT NULL DEFAULT 'open',   -- open | closed
    flags TEXT,                            -- latest monitor flags, comma-separated
    thesis_status TEXT,                    -- intact | weakened | broken | unchecked
    thesis_note TEXT,
    last_checked TEXT,
    closed TEXT, close_price REAL, close_reason TEXT, excess_return REAL,
    UNIQUE (ticker, opened)
);
CREATE TABLE IF NOT EXISTS daily_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of TEXT NOT NULL,
    status TEXT NOT NULL,                  -- running | done | failed
    started TEXT NOT NULL,
    finished TEXT,
    message TEXT,
    report_path TEXT
);
CREATE TABLE IF NOT EXISTS daily_picks (
    run_id INTEGER NOT NULL,
    rank INTEGER,                          -- NULL for picks sent to human review
    ticker TEXT NOT NULL,
    prediction_id INTEGER,
    p_final REAL,
    contested INTEGER,
    needs_review INTEGER,
    pick_json TEXT,                        -- thesis, risks, sources, debate summary
    PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS filing_status (  -- on-demand filing preparation for non-core companies
    ticker TEXT PRIMARY KEY,
    status TEXT NOT NULL,                  -- queued | running | ready | failed
    updated TEXT NOT NULL,
    message TEXT
);
CREATE TABLE IF NOT EXISTS screener_results (
    as_of TEXT NOT NULL,
    ticker TEXT NOT NULL,
    score REAL,                            -- 0-1, mean of the available signal percentiles
    momentum_pct REAL, value_pct REAL, surprise_pct REAL,   -- percentile ranks within the universe
    signals_json TEXT,                     -- raw signal values
    triggers TEXT,                         -- discovery triggers that fired, comma-separated
    shortlisted INTEGER NOT NULL,          -- 1 if passed to the research agents
    reason TEXT,
    PRIMARY KEY (as_of, ticker)
);
CREATE TABLE IF NOT EXISTS insider_filings (   -- every Form 4 fetched, so filings with no stock trades aren't refetched
    accession TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    filed TEXT NOT NULL,
    url TEXT,
    trades INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS insider_trades (    -- SEC Form 4, non-derivative transactions (common stock)
    accession TEXT NOT NULL,
    line INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    filed TEXT NOT NULL,           -- the as-of key: date the Form 4 became public
    owner TEXT,
    relationship TEXT,             -- e.g. "Director", "Chief Executive Officer", "10% owner"
    txn_date TEXT,
    code TEXT,                     -- P open-market purchase, S open-market sale, A grant, M exercise, F tax, G gift
    shares REAL,
    price REAL,
    acquired_disposed TEXT,        -- A or D
    planned INTEGER,               -- 1 if reported under a Rule 10b5-1 trading plan, NULL if not stated
    PRIMARY KEY (accession, line)
);
CREATE TABLE IF NOT EXISTS macro_series (      -- FRED observations (latest vintage)
    series_id TEXT NOT NULL,
    date TEXT NOT NULL,            -- observation date (a monthly value is published weeks later; see config)
    value REAL NOT NULL,
    PRIMARY KEY (series_id, date)
);
CREATE TABLE IF NOT EXISTS news_items (        -- headline + summary + link only (no full article text)
    news_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    published TEXT NOT NULL,       -- YYYY-MM-DD (UTC): the as-of key
    source TEXT NOT NULL,          -- publisher, from the trusted allowlist
    headline TEXT NOT NULL,
    summary TEXT,
    url TEXT NOT NULL,
    doc_id TEXT                    -- parsed document indexed in Chroma
);
CREATE TABLE IF NOT EXISTS earnings_events (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,            -- announcement date
    eps_estimate REAL,
    eps_reported REAL,             -- NULL for upcoming announcements
    surprise_pct REAL,
    PRIMARY KEY (ticker, date)
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path or config.MARKET_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after a table first shipped (CREATE TABLE IF NOT EXISTS does not add columns).
MIGRATIONS = [
    ("predictions", "source", "TEXT NOT NULL DEFAULT 'live'"),   # live | backtest
    ("predictions", "context_json", "TEXT"),                     # segment fields for the reflector
    ("predictions", "rule_adjustment", "REAL NOT NULL DEFAULT 0"),
    ("positions", "source", "TEXT NOT NULL DEFAULT 'system'"),  # system (opened by a picks run) | mine (user)
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()
