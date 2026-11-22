"""Tests for ranking and pick summaries (no network, no LLM)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import ranker  # noqa: E402

STATE = {"evidence": [
    {"eid": "E1", "ticker": "AAA", "form_type": "10-Q", "filing_date": "2026-05-01", "section": "MD&A", "source_url": "u1"},
    {"eid": "E2", "ticker": "AAA", "form_type": "10-K", "filing_date": "2026-02-01", "section": "Risk Factors", "source_url": "u2"},
    {"eid": "E3", "ticker": "AAA", "form_type": "10-K", "filing_date": "2026-02-01", "section": "MD&A", "source_url": "u3"}],
    "final": {"missing_risks": ["r1", "r2", "r3", "r4"], "trace_file": "t.json"}}
DEBATE = {"leaning": "bull", "contested": False, "trace_file": "d.json", "survivors": [
    {"id": "BULL1", "side": "bull", "text": "Demand is broadening", "score": 7.5, "evidence_ids": ["E1"]},
    {"id": "BULL2", "side": "bull", "text": "Weaker bull point", "score": 6.0, "evidence_ids": ["E3"]},
    {"id": "BEAR1", "side": "bear", "text": "Debt is rising", "score": 6.2, "evidence_ids": ["E2"]}]}
FORECAST = {"as_of": "2026-06-01", "prediction_id": 7, "p_final": 0.52, "p_quant": 0.5,
            "evidence_adjustment": 0.04, "needs_review": False}


def test_summary_takes_thesis_from_leaning_side_and_risk_from_the_other():
    p = ranker.summarize("AAA", STATE, DEBATE, FORECAST)
    assert p["thesis"]["text"] == "Demand is broadening"
    assert p["main_risk"]["text"] == "Debt is rising"
    assert set(p["sources"]) == {"E1", "E2"}                     # only evidence the thesis and risk cite
    assert p["critic_risks"] == ["r1", "r2", "r3"] and p["research_trace"] == "t.json"


def test_rank_orders_by_probability_and_separates_review_and_failures():
    picks = [{"ticker": "LOW", "p_final": 0.48, "needs_review": False},
             {"ticker": "HIGH", "p_final": 0.55, "needs_review": False},
             {"ticker": "WILD", "p_final": 0.70, "needs_review": True},
             {"ticker": "BAD", "error": "boom"}]
    ranked = ranker.rank(picks)
    assert [(p["ticker"], p["rank"]) for p in ranked] == [("HIGH", 1), ("LOW", 2), ("WILD", None), ("BAD", None)]


def test_report_lists_review_and_failure_sections():
    p = ranker.summarize("AAA", STATE, DEBATE, FORECAST)
    md = ranker.render_markdown("2026-06-01", ranker.rank([p, {**p, "ticker": "WILD", "p_final": 0.7,
                                                                "needs_review": True}, {"ticker": "BAD", "error": "boom"}]))
    assert "## 1. AAA: 52.0%" in md and "## Human review: WILD" in md and "## BAD: failed" in md
    assert "Demand is broadening" in md and ranker.config.DISCLAIMER in md
