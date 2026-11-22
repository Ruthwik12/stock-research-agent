"""Reflector: learns from resolved predictions (Checkpoint 2 self-correction loop).

1. Calibration report: Brier vs "always 50%" and the base rate, calibration buckets, per source.
2. Segment analysis: for each context field (sector, momentum/volatility bucket, debate lean,
   contested, ...) compare the mean forecast with the actual hit rate.
3. Rule proposals, with the Checkpoint 2 safeguards:
   - a segment needs RULE_MIN_CASES resolved predictions in BOTH halves of time;
   - the correction is learned on the older half and kept only if it lowers the Brier score on the
     newer half, which it never saw (out-of-sample);
   - corrections are shrunk toward 0 and capped at RULE_MAX_DELTA;
   - proposals start inactive; a human approves them (Checkpoint 6);
   - active rules are re-tested on each reflection and flagged for retirement when they stop helping.
4. Pruned-thesis audit: for resolved live picks with a debate, how often a pruned thesis's side won.
No LLM: every number here is computed, so the reflector cannot invent lessons.
"""
import json
from datetime import datetime

import config
from stockagent.db import connect
from stockagent.market import brier_score, calibration_table

RULE_MIN_CASES = 100
RULE_MIN_GAP = 0.05          # |hit rate - mean forecast| needed in the older half to consider a rule
RULE_MAX_DELTA = 0.05        # a rule moves a forecast at most 5 points
SEGMENT_FIELDS = ["sector", "momentum_bucket", "volatility_bucket", "leaning", "contested", "evidence_direction"]


def context_for(features: dict | None, sector: str | None, debate: dict | None, adjustment: float) -> dict:
    """Segment fields stored with every prediction (shared by live forecasts and backtests)."""
    mom = (features or {}).get("excess_6m")
    vol = (features or {}).get("volatility_3m")
    return {
        "sector": sector or "unknown",
        "momentum_bucket": None if mom is None else "low" if mom < -0.10 else "high" if mom > 0.10 else "mid",
        "volatility_bucket": None if vol is None else "low" if vol < 0.25 else "high" if vol > 0.40 else "mid",
        "leaning": (debate or {}).get("leaning"),
        "contested": None if debate is None else str(bool(debate.get("contested"))),
        "evidence_direction": None if debate is None and adjustment == 0 else
        "up" if adjustment > 0 else "down" if adjustment < 0 else "none",
    }


def _resolved(conn, source: str | None = None) -> list[dict]:
    q = "SELECT * FROM predictions WHERE status = 'resolved'" + (" AND source = ?" if source else "")
    rows = conn.execute(q + " ORDER BY as_of", (source,) if source else ()).fetchall()
    return [{**dict(r), "context": json.loads(r["context_json"] or "{}")} for r in rows]


def _report(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    p, y = [r["p_final"] for r in rows], [r["outcome"] for r in rows]
    base = sum(y) / len(y)
    return {"n": len(rows), "base_rate": base, "brier": brier_score(p, y),
            "brier_always_50": brier_score([0.5] * len(y), y), "brier_base_rate": brier_score([base] * len(y), y),
            "calibration": calibration_table(p, y, bucket=0.05)}


def _matches(context: dict, field: str, value: str) -> bool:
    """'all' is a segment containing every forecast (an overall bias correction)."""
    return field == "all" or str(context.get(field)) == value


def _segments(rows: list[dict]) -> list[dict]:
    out = []
    for field in ["all", *SEGMENT_FIELDS]:
        groups: dict[str, list[dict]] = {}
        for r in rows:
            v = "all" if field == "all" else r["context"].get(field)
            if v is not None:
                groups.setdefault(str(v), []).append(r)
        for value, g in groups.items():
            mean_p = sum(r["p_final"] for r in g) / len(g)
            hit = sum(r["outcome"] for r in g) / len(g)
            out.append({"field": field, "value": value, "n": len(g), "mean_forecast": mean_p, "hit_rate": hit,
                        "gap": hit - mean_p})
    return sorted(out, key=lambda s: -abs(s["gap"]))


def _brier_with(rows, field, value, delta) -> float:
    p = [min(0.99, max(0.01, r["p_final"] + (delta if _matches(r["context"], field, value) else 0))) for r in rows]
    return brier_score(p, [r["outcome"] for r in rows])


def propose_rules(rows: list[dict]) -> tuple[list[dict], int]:
    """Learn corrections on the older half, keep those that improve Brier on the newer half."""
    if len(rows) < 2 * RULE_MIN_CASES:
        return [], 0
    cut = rows[len(rows) // 2]["as_of"]
    old = [r for r in rows if r["as_of"] < cut]
    new = [r for r in rows if r["as_of"] >= cut]
    proposals, rejected = [], 0
    new_segments = {(s["field"], s["value"]): s for s in _segments(new)}
    for s in _segments(old):
        n_new = new_segments.get((s["field"], s["value"]), {}).get("n", 0)
        if s["n"] < RULE_MIN_CASES or n_new < RULE_MIN_CASES or abs(s["gap"]) < RULE_MIN_GAP:
            continue
        delta = max(-RULE_MAX_DELTA, min(RULE_MAX_DELTA, s["gap"] * s["n"] / (s["n"] + 100)))   # shrunk
        before, after = _brier_with(new, s["field"], s["value"], 0.0), _brier_with(new, s["field"], s["value"], delta)
        if after < before:
            proposals.append({"field": s["field"], "value": s["value"], "delta": round(delta, 4), "evidence": {
                "learned_on": f"{old[0]['as_of']}..{old[-1]['as_of']}", "n_learn": s["n"],
                "gap_learn": round(s["gap"], 4), "tested_on": f"{new[0]['as_of']}..{new[-1]['as_of']}",
                "n_test": n_new, "brier_test_before": round(before, 5), "brier_test_after": round(after, 5)}})
        else:
            rejected += 1
    return proposals, rejected


def _pruned_audit(conn, rows: list[dict]) -> dict | None:
    """How often a pruned thesis's side matched the outcome (resolved live predictions with a debate)."""
    right = total = 0
    for r in rows:
        trace = r.get("report_trace")
        if not trace:
            continue
        path = config.PROJECT_ROOT / trace.replace(".json", "_debate.json")
        if not path.exists():
            continue
        winner = "bull" if r["outcome"] == 1 else "bear"
        for t in json.loads(path.read_text())["result"].get("pruned", []):
            total += 1
            right += t["side"] == winner
    return {"pruned_theses": total, "side_won": right, "rate": right / total} if total else None


def reflect(conn=None, save: bool = True) -> dict:
    """Full reflection: reports, segments, rule proposals, active-rule re-tests.
    save=True stores proposals and re-test notes (nightly job); save=False only reports (UI view)."""
    conn = conn or connect()
    now = datetime.now().isoformat(timespec="seconds")
    live, backtest = _resolved(conn, "live"), _resolved(conn, "backtest")
    learn_from = live if len(live) >= 2 * RULE_MIN_CASES else backtest   # live history wins once there is enough
    proposals, rejected = propose_rules(learn_from)
    existing = {(r["field"], r["value"]): r for r in conn.execute(
        "SELECT * FROM rules WHERE status IN ('proposed', 'active')").fetchall()}
    for p in proposals if save else []:
        key = (p["field"], p["value"])
        if key in existing:   # refresh the evidence of a rule already pending or active; never duplicate
            conn.execute("UPDATE rules SET evidence_json = ? WHERE rule_id = ?",
                         (json.dumps(p["evidence"]), existing[key]["rule_id"]))
        else:
            conn.execute("INSERT INTO rules (created, field, value, delta, status, evidence_json) "
                         "VALUES (?, ?, ?, ?, 'proposed', ?)", (now, p["field"], p["value"], p["delta"],
                                                                 json.dumps(p["evidence"])))
    # Re-test active rules on the newer half of current data; flag (not remove) the ones that stopped helping.
    recent = learn_from[len(learn_from) // 2:]
    for r in conn.execute("SELECT * FROM rules WHERE status = 'active'").fetchall() if save else []:
        if recent and _brier_with(recent, r["field"], r["value"], r["delta"]) >= \
                _brier_with(recent, r["field"], r["value"], 0.0):
            conn.execute("UPDATE rules SET note = ? WHERE rule_id = ?",
                         (f"{now}: no longer improves recent Brier; consider retiring", r["rule_id"]))
    conn.commit()
    return {"generated": now, "live": _report(live), "backtest": _report(backtest),
            "learned_from": "live" if learn_from is live else "backtest",
            "segments": [s for s in _segments(learn_from) if s["n"] >= RULE_MIN_CASES][:12],
            "proposals_new_or_refreshed": len(proposals), "candidates_rejected_out_of_sample": rejected,
            "pruned_audit": _pruned_audit(conn, live), "rules": list_rules(conn)}


def list_rules(conn=None) -> list[dict]:
    conn = conn or connect()
    return [{**dict(r), "evidence": json.loads(r["evidence_json"] or "{}")}
            for r in conn.execute("SELECT * FROM rules ORDER BY status, rule_id DESC")]


def decide_rule(rule_id: int, status: str, conn=None) -> None:
    """Human decision on a rule: proposed -> active | rejected, active -> retired."""
    allowed = {"proposed": {"active", "rejected"}, "active": {"retired"}}
    conn = conn or connect()
    row = conn.execute("SELECT status FROM rules WHERE rule_id = ?", (rule_id,)).fetchone()
    if not row or status not in allowed.get(row["status"], set()):
        raise ValueError(f"cannot move rule {rule_id} from {row['status'] if row else 'missing'} to {status}")
    conn.execute("UPDATE rules SET status = ?, decided = ? WHERE rule_id = ?",
                 (status, datetime.now().isoformat(timespec="seconds"), rule_id))
    conn.commit()


def active_adjustment(context: dict, conn) -> tuple[float, list[dict]]:
    """Sum of active rules matching this forecast's context, and which ones applied."""
    applied = [dict(r) for r in conn.execute("SELECT rule_id, field, value, delta FROM rules WHERE status = 'active'")
               if _matches(context, r["field"], r["value"])]
    return sum(r["delta"] for r in applied), applied
