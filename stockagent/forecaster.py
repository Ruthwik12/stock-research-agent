"""Forecaster: probability that a stock beats the S&P 500 over the next 90 days.

    p_quant  = calibrated quantitative model (stockagent/forecast_model.py), base rate if no skill
    adjust   = evidence adjustments, small = 2 points, moderate = 4, total capped at +/-15, from either
               - the bull/bear debate (stockagent/debate_graph.py): each surviving thesis scoring 6+ (small)
                 or 8+ (moderate); halved when the debate is contested, or
               - without a debate, the LLM's proposals from the research report's verified claims
    p_final  = p_quant + shrink_weight * adjust + active reflector rules (human-approved)
    shrink_weight starts at 0.5 and grows toward 1 as predictions resolve (n / (n + 100) on top of
    the 0.5 floor), so unproven judgment counts for less until outcomes show it helps.

Every forecast is stored in the predictions table and scored once its 90 days have passed.
"""
import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

import config
from stockagent import llm, reflector
from stockagent.db import connect
from stockagent.features import FEATURES, HORIZON_DAYS, features_at, label_at, load_closes, universe_medians
from stockagent.forecast_model import ForecastModel

STRENGTH_POINTS = {"small": 0.02, "moderate": 0.04}


class Adjustment(BaseModel):
    claim_numbers: list[int] = Field(description="Numbers of the verified claims this adjustment rests on")
    direction: Literal["up", "down"]
    strength: Literal["small", "moderate"]
    reason: str


class Adjustments(BaseModel):
    adjustments: list[Adjustment] = Field(description="0-4 adjustments; none if the claims don't bear on the next 90 days")


def _evidence_adjustments(ticker: str, as_of: str, p_quant: float, claims: list[dict]) -> list[dict]:
    """LLM proposes adjustments; code keeps only those citing real, verified claims."""
    usable = [c for c in claims if c["status"] in ("supported", "partially_supported")]
    if not usable:
        return []
    listing = "\n".join(f"{i}. {c['text']}" for i, c in enumerate(usable, 1))
    out = llm.structured(
        "You are a careful equity forecaster. You adjust a base probability only for evidence that plausibly "
        "affects relative stock performance over the next 90 days. Most evidence is already priced in; "
        "prefer no adjustment over a weak one. Never give investment advice.",
        f"Stock: {ticker}. As of {as_of}. Current probability of beating the S&P 500 over {HORIZON_DAYS} days: "
        f"{p_quant:.1%} (from price signals).\n\nVerified claims (SEC filings, Form 4 and FRED summaries, "
        f"trusted news; filings outrank news):\n{listing}\n\n"
        "Propose at most 4 adjustments, each citing the claim numbers it rests on.", Adjustments)
    kept = []
    for a in out.adjustments:
        nums = [n for n in a.claim_numbers if 1 <= n <= len(usable)]
        if not nums:
            continue   # an adjustment must rest on a real verified claim
        size = STRENGTH_POINTS[a.strength] * (1 if a.direction == "up" else -1)
        if all(usable[n - 1]["status"] == "partially_supported" for n in nums):
            size /= 2  # only partially supported evidence counts half
        kept.append({"points": size, "direction": a.direction, "strength": a.strength, "reason": a.reason,
                     "claims": [usable[n - 1]["text"] for n in nums]})
    return kept


def _debate_adjustments(debate: dict) -> list[dict]:
    """Each surviving thesis moves the probability by its strength; a contested debate counts half."""
    kept = []
    for t in debate.get("survivors", []):
        strength = "moderate" if t["score"] >= 8 else "small" if t["score"] >= 6 else None
        if not strength:
            continue   # survived only as its side's best (protected), too weak to move the forecast
        size = STRENGTH_POINTS[strength] * (1 if t["side"] == "bull" else -1)
        if debate.get("contested"):
            size /= 2
        kept.append({"points": size, "direction": "up" if t["side"] == "bull" else "down", "strength": strength,
                     "reason": f"{t['side']} thesis {t['id']} (score {t['score']:.1f}): {t['text']}",
                     "claims": t["evidence_ids"]})
    return kept


def shrink_weight(conn) -> float:
    """Grows with the LIVE track record only: backtests contain no LLM judgment to validate."""
    n = conn.execute("SELECT COUNT(*) FROM predictions WHERE status = 'resolved' AND source = 'live'").fetchone()[0]
    return 0.5 + 0.5 * n / (n + 100)


def sector_of(ticker: str, conn) -> str | None:
    row = conn.execute("SELECT sector FROM universe WHERE ticker = ? UNION ALL "
                       "SELECT sector FROM companies WHERE ticker = ?", (ticker, ticker)).fetchone()
    return row[0] if row else None


def forecast(ticker: str, as_of: str, report: dict | None = None, save: bool = True, conn=None,
             debate: dict | None = None) -> dict:
    conn = conn or connect()
    closes = load_closes(conn, [ticker, config.BENCHMARK])
    feats = features_at(closes, ticker, as_of) if ticker in closes else None
    model = ForecastModel.load()
    if feats:   # the model ranks stocks against the median S&P 500 member on the same date
        p_quant = model.predict_one({k: feats[k] - m for k, m in zip(FEATURES, universe_medians(conn, as_of))})
    else:       # too little price history: plain base rate
        p_quant = model.meta.get("base_rate", 0.5)
    if debate:
        adjustments = _debate_adjustments(debate)
    else:
        adjustments = _evidence_adjustments(ticker, as_of, p_quant, report["claims"]) if report else []
    total = max(-config.MAX_EVIDENCE_ADJUSTMENT,
                min(config.MAX_EVIDENCE_ADJUSTMENT, sum(a["points"] for a in adjustments)))
    w = shrink_weight(conn)
    context = reflector.context_for(feats, sector_of(ticker, conn), debate, total)
    rule_adj, rules_applied = reflector.active_adjustment(context, conn)   # human-approved reflector rules
    p_final = min(0.99, max(0.01, p_quant + w * total + rule_adj))
    low, high = config.REVIEW_BAND
    result = {
        "ticker": ticker, "as_of": as_of, "horizon_days": HORIZON_DAYS,
        "p_quant": p_quant, "evidence_adjustment": total, "shrink_weight": w,
        "rule_adjustment": rule_adj, "rules_applied": rules_applied, "context": context, "p_final": p_final,
        "needs_review": not (low <= p_final <= high),
        "features": feats, "adjustments": adjustments,
        "model": {"holdout_brier": model.meta.get("holdout_brier"), "calibration_slope": model.calib[0],
                  "auc_by_date": model.meta.get("auc_by_date")},
        "report_trace": report.get("trace_file") if report else None,
        "debate": {"contested": debate.get("contested"), "leaning": debate.get("leaning"),
                   "trace_file": debate.get("trace_file")} if debate else None,
        "disclaimer": config.DISCLAIMER,
    }
    if save:
        cur = conn.execute(
            """INSERT INTO predictions (created, ticker, as_of, horizon_days, p_quant, evidence_adjustment,
               shrink_weight, p_final, needs_review, features_json, adjustments_json, report_trace,
               source, context_json, rule_adjustment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?)""",
            (datetime.now().isoformat(timespec="seconds"), ticker, as_of, HORIZON_DAYS, p_quant, total, w, p_final,
             int(result["needs_review"]), json.dumps(feats), json.dumps(adjustments), result["report_trace"],
             json.dumps(context), rule_adj))
        conn.commit()
        result["prediction_id"] = cur.lastrowid
    return result


def resolve_open(conn=None) -> int:
    """Score every open prediction whose horizon has passed. Returns how many were resolved."""
    conn = conn or connect()
    rows = conn.execute("SELECT prediction_id, ticker, as_of FROM predictions WHERE status = 'open'").fetchall()
    if not rows:
        return 0
    closes = load_closes(conn, sorted({r["ticker"] for r in rows} | {config.BENCHMARK}))
    done = 0
    for r in rows:
        lab = label_at(closes, r["ticker"], r["as_of"]) if r["ticker"] in closes else None
        if lab:
            conn.execute("UPDATE predictions SET status='resolved', outcome=?, excess_return=?, resolved_on=? "
                         "WHERE prediction_id=?", (lab[0], lab[1], date.today().isoformat(), r["prediction_id"]))
            done += 1
    conn.commit()
    return done
