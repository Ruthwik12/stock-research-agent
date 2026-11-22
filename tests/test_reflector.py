"""Tests for the reflector on synthetic resolved predictions (no network, no LLM)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import reflector  # noqa: E402
from stockagent.db import connect  # noqa: E402


def _rows(sector, n, hit_old, hit_new):
    """n predictions per half at p=0.5, with the given hit rates in the older and newer half."""
    out = []
    for half, date_, hit in (("old", "2025-08-15", hit_old), ("new", "2026-05-15", hit_new)):
        for i in range(n):
            out.append({"as_of": date_, "p_final": 0.5, "outcome": int(i < hit * n),
                        "context": {"sector": sector}})
    return out


def test_rule_kept_only_if_it_holds_out_of_sample():
    rows = sorted(_rows("Steady", 150, 0.70, 0.70) + _rows("Flips", 150, 0.70, 0.30), key=lambda r: r["as_of"])
    proposals, rejected = reflector.propose_rules(rows)
    assert [(p["field"], p["value"]) for p in proposals] == [("sector", "Steady")]
    assert 0 < proposals[0]["delta"] <= reflector.RULE_MAX_DELTA          # shrunk and capped
    assert rejected == 2   # "Flips" and the overall segment (70% then 50%) both fail on the newer half


def test_overall_bias_rule():
    rows = sorted(_rows("A", 150, 0.40, 0.40) + _rows("B", 150, 0.40, 0.40), key=lambda r: r["as_of"])
    proposals, _ = reflector.propose_rules(rows)
    assert ("all", "all") in [(p["field"], p["value"]) for p in proposals]
    assert all(p["delta"] < 0 for p in proposals)                         # forecasts too high everywhere


def test_too_few_cases_propose_nothing():
    assert reflector.propose_rules(_rows("Tiny", 20, 0.9, 0.9)) == ([], 0)


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "r.db")
    rows = sorted(_rows("Steady", 150, 0.70, 0.70), key=lambda r: r["as_of"])
    conn.executemany(
        """INSERT INTO predictions (created, ticker, as_of, horizon_days, p_quant, evidence_adjustment, shrink_weight,
           p_final, needs_review, status, outcome, source, context_json) VALUES
           ('t', 'X', ?, 90, 0.5, 0, 0.5, 0.5, 0, 'resolved', ?, 'backtest', ?)""",
        [(r["as_of"], r["outcome"], json.dumps(r["context"])) for r in rows])
    conn.commit()
    return conn


def test_reflect_saves_proposal_once_and_human_activates_it(db):
    out = reflector.reflect(db)
    assert out["learned_from"] == "backtest" and out["backtest"]["n"] == 300
    reflector.reflect(db)                                                  # rerun refreshes, never duplicates
    rules = {(r["field"], r["value"]): r for r in reflector.list_rules(db)}
    assert set(rules) == {("all", "all"), ("sector", "Steady")}           # one sector here, so both describe it
    assert all(r["status"] == "proposed" for r in rules.values())
    assert reflector.active_adjustment({"sector": "Steady"}, db)[0] == 0   # not applied until approved
    steady = rules[("sector", "Steady")]
    reflector.decide_rule(steady["rule_id"], "active", db)
    delta, applied = reflector.active_adjustment({"sector": "Steady"}, db)
    assert delta == steady["delta"] and applied[0]["value"] == "Steady"
    assert reflector.active_adjustment({"sector": "Other"}, db)[0] == 0
    with pytest.raises(ValueError):
        reflector.decide_rule(steady["rule_id"], "active", db)             # already active
