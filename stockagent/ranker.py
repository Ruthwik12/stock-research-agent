"""Ranker / reporter: the daily list of picks.

For the best researchable names on the latest shortlist: research -> bull/bear debate -> forecast,
then rank by probability of beating the S&P 500 over 90 days. Picks outside config.REVIEW_BAND go to
a separate human-review section (Checkpoint 6). The report is assembled in code from the debate's
verified theses and the research sources: no new LLM text, so no new room for invented claims.
"""
import json
from datetime import date, datetime
from pathlib import Path

import config
from stockagent import debate_graph, forecaster, ondemand, sell_monitor
from stockagent.db import connect
from stockagent.features import excess_6m
from stockagent.research_graph import run_research
from stockagent.retriever import cite


def candidates(conn, limit: int = config.DAILY_PICKS_LIMIT) -> list[str]:
    """Latest shortlist, best screener score first (triggered names without a score last), researchable only."""
    rows = conn.execute(
        """SELECT ticker FROM screener_results
           WHERE as_of = (SELECT MAX(as_of) FROM screener_results) AND shortlisted = 1
           ORDER BY score IS NULL, score DESC""").fetchall()
    return [r[0] for r in rows if ondemand.is_researchable(r[0])][:limit]


def summarize(ticker: str, research_state: dict, debate: dict, fc: dict) -> dict:
    """One pick: probability, thesis, risks and sources, all taken from verified outputs."""
    survivors = debate.get("survivors", [])
    best = lambda side: max((t for t in survivors if t["side"] == side), key=lambda t: t["score"], default=None)  # noqa: E731
    leaning = debate.get("leaning") or ("bull" if fc["evidence_adjustment"] > 0 else
                                        "bear" if fc["evidence_adjustment"] < 0 else None)
    thesis = best(leaning) if leaning else max(survivors, key=lambda t: t["score"], default=None)
    counter = best("bear" if (thesis or {}).get("side") == "bull" else "bull")
    # The debate's evidence includes passages added by the critic's send-backs.
    evidence = {e["eid"]: e for e in debate.get("evidence") or research_state.get("evidence", [])}
    used = {i for t in (thesis, counter) if t for i in t["evidence_ids"]}
    return {
        "ticker": ticker, "as_of": fc["as_of"], "prediction_id": fc.get("prediction_id"),
        "p_final": fc["p_final"], "p_quant": fc["p_quant"], "evidence_adjustment": fc["evidence_adjustment"],
        "needs_review": fc["needs_review"], "contested": bool(debate.get("contested")), "leaning": leaning,
        "thesis": {k: thesis[k] for k in ("side", "text", "score", "evidence_ids")} if thesis else None,
        "main_risk": {k: counter[k] for k in ("side", "text", "score", "evidence_ids")} if counter else None,
        "critic_risks": research_state.get("final", {}).get("missing_risks", [])[:3],
        "sources": {i: {"citation": cite(evidence[i]), "url": evidence[i]["source_url"]}
                    for i in sorted(used) if i in evidence},
        "research_trace": research_state.get("final", {}).get("trace_file"),
        "debate_trace": debate.get("trace_file"),
    }


def rank(picks: list[dict]) -> list[dict]:
    """Ranked picks by probability (rank 1 = highest); human-review picks unranked, listed after."""
    ok = sorted((p for p in picks if not p.get("error") and not p["needs_review"]), key=lambda p: -p["p_final"])
    review = [p for p in picks if not p.get("error") and p["needs_review"]]
    failed = [p for p in picks if p.get("error")]
    return ([{**p, "rank": i} for i, p in enumerate(ok, 1)] + [{**p, "rank": None} for p in review]
            + [{**p, "rank": None} for p in failed])


def render_markdown(as_of: str, picks: list[dict]) -> str:
    pct = lambda v: f"{v * 100:.1f}%"   # noqa: E731
    lines = [f"# Daily picks, {as_of}", "",
             f"Probability that each stock beats the S&P 500 over the next 90 days. {config.DISCLAIMER}", ""]
    for p in picks:
        if p.get("error"):
            lines += [f"## {p['ticker']}: failed", "", p["error"], ""]
            continue
        head = f"## {p['rank']}. {p['ticker']}: {pct(p['p_final'])}" if p["rank"] else \
               f"## Human review: {p['ticker']}: {pct(p['p_final'])} (outside {pct(config.REVIEW_BAND[0])}-{pct(config.REVIEW_BAND[1])})"
        flags = [f"leaning {p['leaning']}" if p["leaning"] else "no lean"] + (["contested"] if p["contested"] else [])
        lines += [head, "", f"Model {pct(p['p_quant'])}, debate adjustment {p['evidence_adjustment'] * 100:+.1f} pts; "
                  + ", ".join(flags) + ".", ""]
        if p["thesis"]:
            t = p["thesis"]
            lines.append(f"- **Thesis ({t['side']}, score {t['score']:.1f}):** {t['text']} [{', '.join(t['evidence_ids'])}]")
        if p["main_risk"]:
            r = p["main_risk"]
            lines.append(f"- **Main risk ({r['side']}, score {r['score']:.1f}):** {r['text']} [{', '.join(r['evidence_ids'])}]")
        for r in p["critic_risks"]:
            lines.append(f"- Critic-flagged risk: {r}")
        lines += [f"- [{i}] {s['citation']} ({s['url']})" for i, s in p["sources"].items()] + [""]
    return "\n".join(lines)


def _set_run(conn, run_id: int, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE daily_runs SET {cols} WHERE run_id = ?", (*fields.values(), run_id))
    conn.commit()


def start_run(as_of: str | None = None) -> int:
    conn = connect()
    as_of = as_of or date.today().isoformat()
    cur = conn.execute("INSERT INTO daily_runs (as_of, status, started, message) VALUES (?, 'running', ?, ?)",
                       (as_of, datetime.now().isoformat(timespec="seconds"), "starting"))
    conn.commit()
    conn.close()
    return cur.lastrowid


def daily_run(run_id: int) -> None:
    """Research, debate and forecast each candidate; rank; save picks and a markdown report."""
    conn = connect()
    as_of = conn.execute("SELECT as_of FROM daily_runs WHERE run_id = ?", (run_id,)).fetchone()[0]
    tickers = candidates(conn)
    picks = []
    for n, t in enumerate(tickers, 1):
        _set_run(conn, run_id, message=f"{t} ({n}/{len(tickers)}): research, debate, forecast")
        try:
            final = run_research(config.DAILY_QUESTION.format(ticker=t), t, as_of)
            trace = config.PROJECT_ROOT / final["trace_file"]
            debate = debate_graph.run_debate(trace, excess_6m(conn, t, as_of))
            fc = forecaster.forecast(t, as_of, final, debate=debate, conn=conn)
            state = json.loads(trace.read_text())
            state["final"]["trace_file"] = final["trace_file"]   # the saved trace predates its own path
            picks.append(summarize(t, state, debate, fc))
        except Exception as exc:   # one stock failing must not lose the others' picks
            picks.append({"ticker": t, "error": f"{type(exc).__name__}: {exc}"[:500]})
    ranked = rank(picks)
    conn.executemany("INSERT OR REPLACE INTO daily_picks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (run_id, p["rank"], p["ticker"], p.get("prediction_id"), p.get("p_final"), int(p.get("contested", False)),
         int(p.get("needs_review", False)), json.dumps(p)) for p in ranked])
    config.DAILY_DIR.mkdir(parents=True, exist_ok=True)
    report = config.DAILY_DIR / f"{as_of}_run{run_id}.md"
    report.write_text(render_markdown(as_of, ranked), encoding="utf-8")
    failed = sum(bool(p.get("error")) for p in ranked)
    opened = sell_monitor.open_from_run(run_id, conn)
    _set_run(conn, run_id, status="done", finished=datetime.now().isoformat(timespec="seconds"),
             message=(f"{len(ranked) - failed} picks, {failed} failed, {opened} paper positions opened"
                      if tickers else "no researchable shortlisted names"),
             report_path=str(Path(report).relative_to(config.PROJECT_ROOT)))
    conn.close()


def latest(conn=None) -> dict:
    conn = conn or connect()
    run = conn.execute("SELECT * FROM daily_runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if not run:
        return {"run": None, "picks": []}
    picks = [json.loads(r["pick_json"]) for r in conn.execute(
        "SELECT pick_json FROM daily_picks WHERE run_id = ? ORDER BY rank IS NULL, rank", (run["run_id"],))]
    return {"run": dict(run), "picks": picks}
