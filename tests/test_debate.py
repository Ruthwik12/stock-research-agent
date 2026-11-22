"""Tests for the bull/bear debate graph with a scripted fake LLM (no network)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import debate_graph, forecaster  # noqa: E402

EVIDENCE = [
    {"eid": "E1", "chunk_id": "c1", "ticker": "AAA", "form_type": "10-Q", "filing_date": "2026-05-01",
     "period_end": "2026-03-31", "section": "MD&A", "text": "Revenue grew 20% to $5,000 million on strong demand."},
    {"eid": "E2", "chunk_id": "c2", "ticker": "AAA", "form_type": "10-Q", "filing_date": "2026-05-01",
     "period_end": "2026-03-31", "section": "Risk Factors", "text": "Debt rose sharply and refinancing costs may increase."},
]


def fake_llm(scores, prompts):
    """Scripted analysts and critic. `scores` maps thesis ID -> (evidence, relevance, materiality[, query]),
    or to a list of such tuples used in turn on successive scorings (to script a re-score)."""
    calls: dict[str, int] = {}

    def pick(i):
        s = scores[i]
        if isinstance(s, list):
            n = calls[i] = calls.get(i, -1) + 1
            s = s[min(n, len(s) - 1)]
        return s

    def structured(system, user, schema):
        prompts.append((system, user))
        if schema is debate_graph.Theses:
            if "BULL analyst" in system:
                return schema(theses=[
                    {"text": "Revenue grew 20% on strong demand", "evidence_ids": ["E1"]},          # valid
                    {"text": "New product launch", "evidence_ids": ["E9"]},                       # unknown ID
                    {"text": "Revenue grew 35% this quarter", "evidence_ids": ["E1"]}])          # wrong number
            return schema(theses=[{"text": "Rising debt raises refinancing costs over the next 90 days",
                                   "evidence_ids": ["E2"]},   # the horizon phrase must not fail the number check
                                  {"text": "Demand may cool after a strong quarter", "evidence_ids": ["E1"]}])
        if schema is debate_graph.Scores:
            ids = re.findall(r"Thesis (\w+) \(", user)
            picked = {i: pick(i) for i in ids}
            return schema(scores=[{"thesis_id": i, "evidence_support": s[0], "relevance_90d": s[1],
                                   "materiality": s[2], "reason": "scripted",
                                   "missing_evidence_query": s[3] if len(s) > 3 else ""}
                                  for i, s in picked.items()])
        if schema is debate_graph.Rebuttals:
            ids = re.findall(r"^(\w+): ", user, flags=re.M)
            return schema(rebuttals=[{"thesis_id": i, "counter_argument": "scripted rebuttal", "evidence_ids": ["E1"]}
                                     for i in ids])
        raise AssertionError(schema)
    return structured


def run(monkeypatch, scores, momentum=0.1):
    prompts = []
    monkeypatch.setattr(debate_graph.llm, "structured", fake_llm(scores, prompts))
    state = debate_graph.build_graph().invoke({"ticker": "AAA", "as_of": "2026-06-01", "evidence": EVIDENCE,
                                               "momentum": momentum, "theses": [], "events": []})
    return state["result"], prompts


def test_checks_scoring_and_contested(monkeypatch):
    res, prompts = run(monkeypatch, {"BULL1": (9, 8, 8), "BEAR1": (8, 8, 7), "BEAR2": (2, 2, 2)})
    failed = {t["id"]: t["problems"] for t in res["checked"] if not t["passed"]}
    assert set(failed) == {"BULL2", "BULL3"}                       # unknown ID and an invented number
    assert {t["id"] for t in res["survivors"]} == {"BULL1", "BEAR1"}
    assert res["best"]["bull"]["score"] == 8.8                     # 0.4*9 + 0.2*8 + 0.2*8 + 0.1*10 + 0.1*10
    assert res["contested"] and res["leaning"] is None
    # Independence: the bear analyst's prompt never contains the bull's drafts, and vice versa.
    # (These phrases appear only in each side's drafts, never in the shared evidence.)
    generation = [(s, u) for s, u in prompts if "Opposing" not in u and "analyst" in s]
    assert all("New product launch" not in u for s, u in generation if "BEAR analyst" in s)
    assert all("Rising debt raises" not in u for s, u in generation if "BULL analyst" in s)


def test_best_of_each_side_is_protected(monkeypatch):
    res, _ = run(monkeypatch, {"BULL1": (9, 9, 9), "BEAR1": (3, 3, 3), "BEAR2": (2, 2, 2)})
    bear = [t for t in res["survivors"] if t["side"] == "bear"]
    assert [t["id"] for t in bear] == ["BEAR1"] and bear[0]["protected"]   # kept although below 5
    assert not res["contested"] and res["leaning"] == "bull"


def _fake_retrieve(monkeypatch, queries):
    from stockagent.retriever import Chunk

    def retrieve(query, ticker=None, as_of=None, **kw):
        queries.append((query, ticker, as_of))
        n = len(queries)
        return [Chunk(chunk_id=f"new{n}", text=f"Gross margin guidance passage {n}.", distance=0.3, ticker=ticker,
                      form_type="8-K-earnings", filing_date="2026-05-20", period_end="", section="Earnings Release",
                      source_url="u")]
    monkeypatch.setattr(debate_graph, "retrieve", retrieve)


def test_send_back_fetches_evidence_and_rescores(monkeypatch):
    queries = []
    _fake_retrieve(monkeypatch, queries)
    res, _ = run(monkeypatch, {"BULL1": [(5, 8, 8, "gross margin guidance"), (8, 8, 8)],
                               "BEAR1": (7, 7, 7), "BEAR2": (2, 2, 2, "irrelevant and weak")})
    assert queries == [("gross margin guidance", "AAA", "2026-06-01")]     # same ticker + as-of filters; BEAR2 not sent
    bull = next(t for t in res["survivors"] if t["id"] == "BULL1")
    assert "E3" in bull["evidence_ids"] and bull["send_backs"][0]["added"] == ["E3"]
    # 0.4*5 + 0.2*8 + 0.2*8 + 1 + 1 = 7.2 before; with the new evidence the critic gives 8 -> 8.4
    assert bull["score_before_send_back"] == 7.2 and bull["first_score"] == 8.4
    assert res["send_back_log"] == [{"thesis": "BULL1", "query": "gross margin guidance", "added": ["E3"]}]


def test_send_back_stops_after_max_rounds(monkeypatch):
    queries = []
    _fake_retrieve(monkeypatch, queries)
    keeps_asking = [(5, 8, 8, f"query {n}") for n in range(10)]               # a new query every time
    res, _ = run(monkeypatch, {"BULL1": keeps_asking, "BEAR1": (7, 7, 7), "BEAR2": (6, 6, 6)})
    assert len(queries) == debate_graph.config.DEBATE_SEND_BACK_ROUNDS == 2


def _ratings(**by_id):
    return {i: {"thesis_id": i, "evidence_support": e, "relevance_90d": 8, "materiality": 8, "reason": "r",
                "missing_evidence_query": ""} for i, e in by_id.items()}


def test_novelty_survives_partial_rescore(monkeypatch):
    """A near-duplicate of a better-evidenced thesis keeps novelty 0 when re-scored alone (send-back, rebuttal)."""
    checked = [{"id": "BULL1", "side": "bull", "text": "Revenue grew 20% on strong demand", "evidence_ids": ["E1"],
                "passed": True},
               {"id": "BULL2", "side": "bull", "text": "Revenue grew 20% on strong demand this quarter",
                "evidence_ids": ["E1"], "passed": True}]
    state = {"ticker": "AAA", "as_of": "2026-06-01", "evidence": EVIDENCE, "momentum": 0.1,
             "result": {"checked": checked}}
    monkeypatch.setattr(debate_graph, "_critic", lambda s, t, r=None: _ratings(BULL1=9, BULL2=5))
    first = {t["id"]: t for t in debate_graph.score(state)["result"]["scored"]}
    assert first["BULL2"]["parts"]["novelty"] == 0 and first["BULL2"]["score"] == 6.2   # 2 + 1.6 + 1.6 + 1 + 0

    # Send-back: only BULL2 is re-rated (evidence 7, still below BULL1's 9), so it is still the duplicate.
    monkeypatch.setattr(debate_graph, "_critic", lambda s, t, r=None: _ratings(BULL2=7))
    state["result"] = {**state["result"], "scored": list(first.values()), "rescore_ids": ["BULL2"]}
    after = {t["id"]: t for t in debate_graph.score(state)["result"]["scored"]}
    assert after["BULL2"]["parts"]["novelty"] == 0 and after["BULL2"]["score"] == 7.0   # was 8.0 with the bug
    assert after["BULL2"]["score_before_send_back"] == 6.2 and after["BULL1"]["score"] == 8.8

    # Rebuttal round: only BULL2 is rebutted and re-rated; novelty must still see BULL1.
    monkeypatch.setattr(debate_graph, "_critic", lambda s, t, r=None: _ratings(BULL2=6))
    state["result"] = {**state["result"], "scored": list(after.values()), "survivors": list(after.values())}
    state["rebuttals"] = [{"thesis_id": "BULL2", "counter_argument": "x", "evidence_ids": ["E2"], "by": "bear"}]
    final = {t["id"]: t for t in debate_graph.rescore(state)["result"]["survivors"]}
    assert final["BULL2"]["parts"]["novelty"] == 0 and final["BULL2"]["score"] == 6.6
    assert final["BULL2"]["first_score"] == 7.0 and "first_score" not in final["BULL1"]


def test_forecaster_uses_debate_and_halves_contested():
    debate = {"contested": True, "survivors": [
        {"id": "BULL1", "side": "bull", "score": 8.8, "text": "x", "evidence_ids": ["E1"]},
        {"id": "BEAR1", "side": "bear", "score": 7.7, "text": "y", "evidence_ids": ["E2"]},
        {"id": "BEAR2", "side": "bear", "score": 4.0, "text": "z", "evidence_ids": ["E2"], "protected": True}]}
    adj = forecaster._debate_adjustments(debate)
    assert [a["points"] for a in adj] == [0.02, -0.01]           # moderate and small, both halved; 4.0 ignored
