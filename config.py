"""Shared settings for the stock research agent.

Decision support only, not financial advice. Paper trading only.
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines) so scheduled runs get settings without a shell profile."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip().strip('"')
            if value:   # an empty placeholder (e.g. ANTHROPIC_API_KEY=) must not mask other credentials
                os.environ.setdefault(key.strip(), value)


_load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------- paths
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # untouched EDGAR downloads (source of truth; everything else is rebuildable)
MANIFEST_PATH = RAW_DIR / "manifest.json"
PARSED_DIR = DATA_DIR / "parsed"      # clean text split into sections, one JSON per document
CHROMA_DIR = PROJECT_ROOT / "chroma_db"
LOG_DIR = PROJECT_ROOT / "logs"
RETRIEVAL_TRACE_PATH = LOG_DIR / "retrieval_trace.jsonl"
RUNS_DIR = LOG_DIR / "runs"           # one JSON trace per agent run
MARKET_DB_PATH = DATA_DIR / "market.db"   # structured data: prices, fundamentals, earnings (SQLite)

# ---------------------------------------------------------------- market data
BENCHMARK = "SPY"                 # S&P 500 index fund; "beat the market" means beat its total return
PRICE_HISTORY_START = "2015-01-01"

# ---------------------------------------------------------------- screener
UNIVERSE_HISTORY_START = "2015-01-01"   # price history for the ~500 universe members (forecast model training)
SCREENER_CANDIDATES = 50          # stage A: best price-signal names that get fundamentals and earnings loaded
SHORTLIST_SIZE = 10               # stage B: names passed to the research agents each day
SCREENER_MIN_HISTORY_DAYS = 120   # fewer trading days than this = new listing -> human review, not auto-scored
EARNINGS_FRESH_DAYS = 120         # an earnings surprise older than this is not a current signal
MAX_CREDIBLE_SURPRISE = 50.0      # |surprise| above this % usually mixes GAAP with adjusted EPS (one-off items); ignored

# ---------------------------------------------------------------- forecaster
FORECAST_TRAIN_START = "2015-07-15"   # first sample date with 6 months of universe price history behind it
FORECAST_FIRST_TEST_YEAR = 2018       # walk-forward evaluation: first out-of-sample year (2+ years of training before it)
MAX_EVIDENCE_ADJUSTMENT = 0.15        # research can move the probability at most +/-15 points (Checkpoint 4)
REVIEW_BAND = (0.35, 0.65)            # forecasts outside this band go to human review (Checkpoint 6)

# ---------------------------------------------------------------- bull / bear debate (Checkpoint 4 design)
DEBATE_THESES_PER_SIDE = 3            # theses each analyst writes
DEBATE_BEAM_WIDTH = 3                 # survivors after scoring (best bull and best bear always kept)
DEBATE_PRUNE_BELOW = 5.0              # critic score (0-10) below which a thesis is dropped
DEBATE_CONTESTED_AT = 7.0             # best bull and best bear both at/above this -> "contested"
DEBATE_SEND_BACK_ROUNDS = 2           # times the critic can send theses back to the researcher (Checkpoint 5)
DEBATE_SEND_BACK_PER_ROUND = 2        # searches per round

# ---------------------------------------------------------------- daily picks (ranker)
DAILY_PICKS_LIMIT = 5                 # stocks researched + debated + forecast per run (~12-15 LLM calls each)
DAILY_QUESTION = ("What are the main drivers of {ticker}'s latest results and outlook in its recent filings, "
                  "and what risks did it highlight?")
DAILY_DIR = LOG_DIR / "daily"         # one markdown report per run

# ---------------------------------------------------------------- sell monitor (paper positions only)
PAPER_MIN_PROBABILITY = 0.50          # ranked picks above this open a paper position
STOP_LOSS_EXCESS = -0.10              # flag when the stock trails the S&P 500 by 10+ points since entry
THESIS_UNSEEN_DAYS = 90               # thesis check also reads passages filed this long before entry the pick never saw
# SEC XBRL tags per metric, tried in order (companies report the same concept under different tags).
FUNDAMENTAL_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"],   # last one: banks (net revenue)
    "net_income": ["NetIncomeLoss"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity"],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
}

# ---------------------------------------------------------------- SEC EDGAR
# SEC asks every automated client to identify itself with a name and contact email. Set in .env.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "")
SEC_MIN_SECONDS_BETWEEN_REQUESTS = 0.2   # well under SEC's ~10 requests/second limit

# Two large-cap tech names (similar vocabulary, so "wrong company" retrieval is a realistic
# failure) plus one bank (very different vocabulary). CIKs zero-padded to 10 digits.
TICKERS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "JPM": "0000019617",
    # Rest of the "Magnificent 7" mega-caps (CIKs from SEC company_tickers.json)
    "NVDA": "0001045810",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "META": "0001326801",
    "TSLA": "0001318605",
}

# How many of the most recent filings of each kind to keep per company. Downloads accumulate:
# a newer filing is added, older ones already downloaded are kept (needed for as-of-date backtests).
FILINGS_PER_TICKER = {
    "10-K": 2,
    "10-Q": 3,
    "8-K-earnings": 4,   # 8-K with Item 2.02 (Results of Operations); we keep exhibit 99.1, the press release
    "8-K-event": 8,      # other material-event 8-Ks (EVENT_8K_ITEMS), body + exhibit 99.1, within the lookback
}
# Lighter profile for companies prepared on demand from the screener shortlist (indexing on CPU is slow).
FILINGS_ON_DEMAND = {"10-K": 1, "10-Q": 1, "8-K-earnings": 2, "8-K-event": 3}
EVENT_8K_LOOKBACK_DAYS = 365
# 8-K items that report a material event (must be filed within 4 business days). Item 2.02 is the
# earnings kind above; 5.07 (shareholder votes) and 9.01 (exhibits only) are left out.
EVENT_8K_ITEMS = {
    "1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy",
    "2.01": "Acquisition or disposal", "2.03": "New debt obligation", "2.04": "Debt acceleration",
    "2.05": "Restructuring or exit costs", "2.06": "Material impairment", "3.01": "Delisting notice",
    "4.01": "Auditor change", "4.02": "Prior financials unreliable", "5.01": "Change in control",
    "5.02": "Executive or director change", "7.01": "Regulation FD disclosure", "8.01": "Other event",
}
ON_DEMAND_NIGHTLY = 3     # top shortlisted (by score) companies prepared automatically each night

# ---------------------------------------------------------------- insider trades (SEC Form 4)
INSIDER_LOOKBACK_DAYS = 180       # Form 4 filings fetched per company (incremental after the first run)
INSIDER_MAX_FILINGS = 80          # per company per run (mega-caps file many Form 4s)
INSIDER_SUMMARY_DAYS = 90         # window summarised for the agents

# ---------------------------------------------------------------- macro data (FRED, no API key needed)
MACRO_HISTORY_START = "2023-01-01"
# series id -> (label, kind, publication lag in days). The lag keeps a monthly value out of any
# as-of date before it was actually published (CPI for October comes out mid-November).
FRED_SERIES = {
    "DGS10": ("10-year Treasury yield", "rate", 1),
    "DGS2": ("2-year Treasury yield", "rate", 1),
    "DFF": ("Effective fed funds rate", "rate", 1),
    "BAMLH0A0HYM2": ("High-yield credit spread", "rate", 1),
    "VIXCLS": ("VIX volatility index", "level", 1),
    "UNRATE": ("Unemployment rate", "rate", 40),
    "CPIAUCSL": ("CPI inflation, year over year", "yoy", 45),
}

# ---------------------------------------------------------------- news (Finnhub company news; key in .env)
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
NEWS_LOOKBACK_DAYS = 30           # first run (one API call per day per company); later runs start at the last item
NEWS_MAX_PER_TICKER = 60          # newest trusted items kept per company per run
NEWS_MAX_PER_QUERY = 2            # at most this many news items among a retrieval's kept results
# Only these publishers are kept (case-insensitive substring of Finnhub's "source"). Aggregators that
# republish others (Yahoo) and opinion or promotional sites are left out on purpose.
NEWS_TRUSTED_SOURCES = ["reuters", "bloomberg", "wall street journal", "wsj", "dow jones", "financial times",
                        "cnbc", "marketwatch", "barron", "associated press", "business wire", "businesswire",
                        "pr newswire", "prnewswire", "globenewswire", "nikkei"]

# ---------------------------------------------------------------- chunking / embeddings / retrieval
CHUNK_CHARS = 2400          # about 600 tokens (English filings run about 4 characters per token)
CHUNK_OVERLAP_CHARS = 300   # about 12.5% overlap
MIN_CHUNK_CHARS = 200       # drop fragments shorter than this (page headers, stray table cells)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = "bge-m3"      # via Ollama; 8,192-token input. Never mix embedding models in one collection.
CHROMA_COLLECTION = "filings_bge_m3"

RETRIEVE_FETCH_K = 30       # candidates pulled from the vector store, then reranked
RETRIEVE_LATEST_K = 8       # extra candidates from the newest 10-Q/10-K and earnings release (recency)
RETRIEVE_KEYWORD_K = 10     # extra candidates from BM25 keyword search (exact terms); 0 = off
RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-12-v2"   # local ONNX cross-encoder via fastembed; None = vector order
RERANKER_CACHE = DATA_DIR / "models" / "fastembed"
RECENCY_PENALTY_PER_YEAR = 2.0   # reranker-score points subtracted per year of filing age (tuned: scripts/14)
RETRIEVE_FINAL_K = 5        # passed to the agent after filtering/dedup
NEAR_DUPLICATE_CONTAINMENT = 0.8   # a passage mostly contained in a higher-ranked one is dropped (repeated boilerplate)
MAX_COSINE_DISTANCE = 0.55  # weaker matches are dropped (0.50-0.65 made no difference on the test set)

# ---------------------------------------------------------------- LLM for the agents
# "ollama" (local, free) or "anthropic" (needs ANTHROPIC_API_KEY or `ant auth login`).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")
OLLAMA_CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen3:14b")
OLLAMA_NUM_CTX = 16384      # Ollama's default context is too small for 5+ evidence chunks
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")

AGENT_MAX_RETRIEVAL_ROUNDS = 3   # step budget for the research loop
AGENT_MAX_REVISIONS = 1          # times the critic can send an answer back

DISCLAIMER = "Decision support only, not financial advice. Paper trading only."
