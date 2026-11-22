"""FastAPI backend for the React UI.

    GET  /api/companies  -> every core company with key as-of metrics
    GET  /api/screener   -> latest screener shortlist (and names flagged for human review)
    GET  /api/filings/X  -> whether company X's filings are ready for research
    POST /api/filings/X  -> start preparing X's filings in the background
    POST /api/forecast   -> probability of beating the S&P 500 over 90 days; with a research trace, the
                            bull/bear debate runs first and its surviving theses adjust the forecast
    GET  /api/picks      -> latest daily run and its ranked picks
    POST /api/picks/run  -> start a daily run (research, debate, forecast, rank) in the background
    GET  /api/positions  -> paper positions with sell-monitor flags
    POST /api/positions  -> add a holding the user bought themselves (source 'mine')
    POST /api/positions/review     -> run the sell monitor with the thesis check (uses LLM credit)
    POST /api/positions/{id}/close -> close a paper position (human decision)
    GET  /api/reflector  -> calibration report, segments and correction rules
    POST /api/rules/{id} -> approve, reject or retire a correction rule (human decision)
    POST /api/research   -> run the LangGraph research agent on one question; returns the cited report
    GET  /api/traces     -> saved research runs, newest first, with the debate and forecast they led to
    GET  /api/traces/X   -> one run's full record: research and debate traces, its searches, its forecast

Run from the project root:
    .venv\\Scripts\\python -m uvicorn api.main:app --port 8000
"""
import json
import re
import sys
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import (debate_graph, forecaster, llm, market, ondemand, ranker, reflector,  # noqa: E402
                        sell_monitor)
from stockagent.db import connect  # noqa: E402
from stockagent.features import excess_6m  # noqa: E402
from stockagent.research_graph import run_research  # noqa: E402

app = FastAPI(title="Stock Research Agent")


def _safe(fn, *args):
    """Run a market tool; missing data becomes None instead of an error for the whole table."""
    try:
        return fn(*args)
    except market.DataUnavailable:
        return None


@app.get("/api/companies")
def companies(as_of: str | None = None) -> list[dict]:
    as_of = as_of or date.today().isoformat()
    conn = connect()
    names = {r["ticker"]: dict(r) for r in conn.execute("SELECT * FROM companies")}
    rows = []
    for ticker in config.TICKERS:
        price = _safe(market.price_on, ticker, as_of, conn)
        surprise = _safe(market.earnings_surprise, ticker, as_of, conn)
        rows.append({
            "ticker": ticker,
            "name": names.get(ticker, {}).get("name"),
            "sector": names.get(ticker, {}).get("sector"),
            "price": price[1] if price else None,
            "price_date": price[0] if price else None,
            "momentum_6m": _safe(market.momentum, ticker, as_of, 182, conn),
            "volatility": _safe(market.volatility, ticker, as_of, 90, conn),
            "pe_ratio": _safe(market.pe_ratio, ticker, as_of, conn),
            "last_surprise_pct": surprise["surprise_pct"] if surprise else None,
            "last_earnings_date": surprise["date"] if surprise else None,
        })
    conn.close()
    return rows


@app.get("/api/screener")
def screener_results(as_of: str | None = None) -> dict:
    """Shortlist and human-review names from the latest screen (or the screen for as_of)."""
    conn = connect()
    as_of = as_of or conn.execute("SELECT MAX(as_of) FROM screener_results").fetchone()[0]
    rows = [dict(r) for r in conn.execute(
        """SELECT s.ticker, u.name, u.sector, s.score, s.signals_json, s.triggers, s.shortlisted, s.reason
           FROM screener_results s LEFT JOIN universe u ON u.ticker = s.ticker
           WHERE s.as_of = ? AND (s.shortlisted = 1 OR s.reason LIKE 'human review%')
           ORDER BY s.shortlisted DESC, s.score DESC""", (as_of,))] if as_of else []
    scanned = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
    conn.close()
    for r in rows:
        r["signals"] = json.loads(r.pop("signals_json"))
        r["triggers"] = [t for t in (r["triggers"] or "").split(",") if t]
        r["shortlisted"] = bool(r["shortlisted"])
        r["filings"] = ondemand.get_status(r["ticker"])
    return {"as_of": as_of, "universe_size": scanned, "rows": rows}


def _in_universe(ticker: str) -> bool:
    conn = connect()
    found = conn.execute("SELECT 1 FROM universe WHERE ticker = ?", (ticker,)).fetchone()
    conn.close()
    return ticker in config.TICKERS or bool(found)


@app.get("/api/filings/{ticker}")
def filings_status(ticker: str) -> dict:
    return ondemand.get_status(ticker.upper())


@app.post("/api/filings/{ticker}")
def prepare_filings(ticker: str) -> dict:
    """Start downloading, parsing and indexing a company's filings in the background."""
    ticker = ticker.upper()
    if not _in_universe(ticker):
        raise HTTPException(400, f"{ticker} is not in the universe")
    status = ondemand.get_status(ticker)
    in_progress = status["status"] in ("queued", "running")
    stale = in_progress and (datetime.now() - datetime.fromisoformat(status["updated"])).total_seconds() \
        > ondemand.STALE_LOCK_SECONDS   # left behind by a crashed server; allow a retry
    if status["status"] == "ready" or (in_progress and not stale):
        return status
    ondemand.set_status(ticker, "queued", "waiting for the pipeline lock")
    threading.Thread(target=ondemand.prepare, args=([ticker],), daemon=True).start()
    return ondemand.get_status(ticker)


class ResearchRequest(BaseModel):
    ticker: str
    question: str = Field(min_length=5, max_length=500)
    as_of: str | None = None


@app.post("/api/research")
def research(req: ResearchRequest) -> dict:
    ticker = req.ticker.upper()
    if not ondemand.is_researchable(ticker):
        raise HTTPException(400, f"{ticker} has no indexed filings yet; prepare its filings first")
    as_of = req.as_of or date.today().isoformat()
    try:
        date.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    return run_research(req.question, ticker, as_of)


class ForecastRequest(BaseModel):
    ticker: str
    as_of: str | None = None
    trace_file: str | None = None   # research run whose verified claims may adjust the forecast


@app.post("/api/forecast")
def forecast(req: ForecastRequest) -> dict:
    ticker = req.ticker.upper()
    if not _in_universe(ticker):
        raise HTTPException(400, f"{ticker} is not in the universe")
    as_of = req.as_of or date.today().isoformat()
    report, debate = None, None
    if req.trace_file:
        path = (config.PROJECT_ROOT / req.trace_file).resolve()
        if path.parent != config.RUNS_DIR.resolve() or not path.exists():   # only our own run traces
            raise HTTPException(400, "unknown research trace")
        report = {**json.loads(path.read_text())["final"], "trace_file": req.trace_file}
        if report["ticker"] != ticker:
            raise HTTPException(400, "research trace is for a different company")
        conn = connect()
        momentum = excess_6m(conn, ticker, report["as_of"])
        conn.close()
        debate = debate_graph.run_debate(path, momentum)
    try:
        result = forecaster.forecast(ticker, as_of, report, debate=debate)
    except llm.LLMError as exc:
        raise HTTPException(502, f"evidence adjustment failed: {exc}")
    if debate:
        result["debate"] = {k: debate.get(k) for k in ("contested", "leaning", "survivors", "pruned", "checked",
                                                       "send_back_log", "trace_file")}
    return result


@app.get("/api/picks")
def picks() -> dict:
    """Latest daily run (status) and its ranked picks."""
    return ranker.latest()


@app.post("/api/picks/run")
def run_picks(as_of: str | None = None) -> dict:
    """Start today's research -> debate -> forecast -> rank run in the background (uses LLM credit)."""
    run = ranker.latest()["run"]
    if run and run["status"] == "running" and \
            (datetime.now() - datetime.fromisoformat(run["started"])).total_seconds() < 3 * 3600:
        return {"run_id": run["run_id"], "status": "running"}   # older "running" = crashed server; start anew
    run_id = ranker.start_run(as_of)
    threading.Thread(target=ranker.daily_run, args=(run_id,), daemon=True).start()
    return {"run_id": run_id, "status": "running"}


@app.get("/api/positions")
def positions() -> list[dict]:
    return sell_monitor.list_positions()


class Holding(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    opened: date
    price: float | None = None
    note: str = Field(default="", max_length=1000)


@app.post("/api/positions")
def add_holding(h: Holding) -> dict:
    """Record a stock the user bought themselves; tracked like a paper position, kept out of the track record."""
    try:
        return {"position_id": sell_monitor.open_manual(h.ticker, h.opened.isoformat(), h.price, h.note)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/positions/review")
def review_positions() -> list[dict]:
    """Run the sell monitor now, including the LLM thesis check against new filings."""
    return sell_monitor.check_positions(thesis_check=True)


@app.post("/api/positions/{position_id}/close")
def close_position(position_id: int) -> dict:
    """Close a paper position: a human decision, never automatic."""
    try:
        return sell_monitor.close_position(position_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/reflector")
def reflection() -> dict:
    """Calibration report, segments and rules (read-only; the nightly job saves new proposals)."""
    return reflector.reflect(save=False)


class RuleDecision(BaseModel):
    status: str   # active | rejected (for proposed rules), retired (for active rules)


@app.post("/api/rules/{rule_id}")
def decide_rule(rule_id: int, d: RuleDecision) -> dict:
    """Human decision on a reflector rule (Checkpoint 6: rules change behavior only after approval)."""
    try:
        reflector.decide_rule(rule_id, d.status)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"rule_id": rule_id, "status": d.status}


TRACE_NAME = re.compile(r"^\d{8}_\d{6}_[A-Z0-9.\-]+\.json$")   # research traces; debate traces end in _debate.json


def _trace_path(name: str) -> Path:
    if not TRACE_NAME.match(name):
        raise HTTPException(400, "unknown trace")
    path = config.RUNS_DIR / name
    if not path.exists():
        raise HTTPException(404, "trace not found")
    return path


def _name(trace_file: str | None) -> str | None:
    return trace_file.replace("\\", "/").rsplit("/", 1)[-1] if trace_file else None


@app.get("/api/traces")
def traces(limit: int = 100) -> list[dict]:
    """Saved research runs, newest first (read-only)."""
    conn = connect()
    p_final = {_name(r["report_trace"]): r["p_final"] for r in conn.execute(
        "SELECT report_trace, p_final FROM predictions WHERE report_trace IS NOT NULL ORDER BY prediction_id")}
    picks_run = {}
    for r in conn.execute("SELECT run_id, pick_json FROM daily_picks ORDER BY run_id"):
        picks_run[_name(json.loads(r["pick_json"]).get("research_trace"))] = r["run_id"]
    conn.close()
    out = []
    for path in sorted(config.RUNS_DIR.glob("*.json"), key=lambda p: p.name, reverse=True):
        if not TRACE_NAME.match(path.name):
            continue
        final = json.loads(path.read_text(encoding="utf-8")).get("final", {})
        debate_path = path.with_name(path.stem + "_debate.json")
        result = json.loads(debate_path.read_text(encoding="utf-8")).get("result", {}) if debate_path.exists() else None
        out.append({"name": path.name, "saved": datetime.strptime(path.name[:15], "%Y%m%d_%H%M%S").isoformat(),
                    "ticker": final.get("ticker"), "question": final.get("question"), "as_of": final.get("as_of"),
                    "strength": final.get("evidence_strength"), "kept": len(final.get("claims", [])),
                    "dropped": len(final.get("dropped_claims", [])),
                    "debate": {"leaning": result.get("leaning"), "contested": result.get("contested")} if result else None,
                    "p_final": p_final.get(path.name), "picks_run": picks_run.get(path.name)})
        if len(out) >= limit:
            break
    return out


def _clock(day: datetime, hms: str, after: bool) -> datetime:
    """Event times are HH:MM:SS; place them on the day of the saved trace (research before it, debate after)."""
    t = datetime.combine(day.date(), time.fromisoformat(hms))
    if after and t < day:
        return t + timedelta(days=1)
    if not after and t > day:
        return t - timedelta(days=1)
    return t


@app.get("/api/traces/{name}")
def trace(name: str) -> dict:
    """One run: research trace, debate trace, every search it ran (kept and dropped passages), its forecast."""
    path = _trace_path(name)
    research = json.loads(path.read_text(encoding="utf-8"))
    debate_path = path.with_name(path.stem + "_debate.json")
    debate = json.loads(debate_path.read_text(encoding="utf-8")) if debate_path.exists() else None
    saved = datetime.strptime(name[:15], "%Y%m%d_%H%M%S")
    events = research.get("events", [])
    start = _clock(saved, events[0]["ts"], after=False) if events else saved - timedelta(minutes=15)
    end = datetime.fromtimestamp(debate_path.stat().st_mtime) if debate else saved
    queries = set(research.get("queries_run", []))
    if debate:
        queries |= {q["query"] for q in debate.get("result", {}).get("send_back_log") or []}
    searches = []
    ticker = research["ticker"]
    with open(config.RETRIEVAL_TRACE_PATH, encoding="utf-8") as f:
        for line in f:
            if f'"{ticker}"' not in line:
                continue
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
            if entry["filters"].get("ticker") == ticker and entry["query"] in queries and \
                    entry["caller"] in ("research_graph", "debate_send_back") and \
                    start - timedelta(seconds=5) <= ts <= end + timedelta(seconds=5):
                searches.append(entry)
    conn = connect()
    row = conn.execute("SELECT * FROM predictions WHERE report_trace LIKE ? ORDER BY prediction_id DESC LIMIT 1",
                       (f"%{name}",)).fetchone()
    conn.close()
    prediction = None
    if row:
        prediction = {k: row[k] for k in ("prediction_id", "created", "p_quant", "evidence_adjustment",
                                          "shrink_weight", "rule_adjustment", "p_final", "needs_review")}
        prediction["adjustments"] = json.loads(row["adjustments_json"] or "[]")
    debate_events = [{**e, "at": _clock(saved, e["ts"], after=True).isoformat()} for e in (debate or {}).get("events", [])]
    return {"name": name,
            "research": {**research, "events": [{**e, "at": _clock(saved, e["ts"], after=False).isoformat()}
                                               for e in events]},
            "debate": {**debate, "events": debate_events} if debate else None,
            "searches": searches, "prediction": prediction}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm": llm.describe(), "tickers": list(config.TICKERS)}
