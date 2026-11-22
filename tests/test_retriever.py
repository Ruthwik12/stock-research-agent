"""Tests for retrieval ordering: reranking, recency preference and newest-filing candidates (no index, no models)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import retriever  # noqa: E402

META = {  # chunk id -> (doc_id, form, filing date, text)
    "old_q": ("JPM_10-Q_2025-11-04", "10-Q", "2025-11-04", "net interest income third quarter"),
    "new_q": ("JPM_10-Q_2026-05-01", "10-Q", "2026-05-01", "net interest income first quarter"),
    "risk": ("JPM_10-K_2026-02-13", "10-K", "2026-02-13", "risk factors"),
}


class FakeCollection:
    def __init__(self):
        self.queries = []

    def _row(self, cid):
        doc, form, filed, _ = META[cid]
        return {"ticker": "JPM", "doc_id": doc, "form_type": form, "filing_date": filed, "section": "MD&A",
                "source_url": "u"}

    def get(self, where, include):
        return {"metadatas": [self._row(c) for c in META]}

    def query(self, query_embeddings, n_results, where, include):
        self.queries.append(where)
        # the plain search misses the newest 10-Q; only the newest-filing search (filtered on doc_id) finds it
        ids = ["new_q"] if "doc_id" in str(where) else ["old_q", "risk"]
        return {"ids": [ids], "documents": [[META[c][3] for c in ids]], "metadatas": [[self._row(c) for c in ids]],
                "distances": [[0.3] * len(ids)]}


def test_newest_filing_joins_the_pool_and_recency_breaks_near_ties(monkeypatch, tmp_path):
    col = FakeCollection()
    monkeypatch.setattr(retriever, "get_collection", lambda: col)
    monkeypatch.setattr(retriever, "embed", lambda texts: [[0.0]])
    monkeypatch.setattr(retriever.config, "RETRIEVAL_TRACE_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(retriever.config, "RETRIEVE_KEYWORD_K", 0)
    scores = {"net interest income third quarter": 5.2, "net interest income first quarter": 5.0, "risk factors": -3}
    monkeypatch.setattr(retriever, "rerank_scores", lambda q, texts: [scores[t] for t in texts])

    got = retriever.retrieve("net interest income most recent quarter", ticker="JPM", as_of="2026-05-15")
    assert retriever.latest_doc_ids(col, "JPM", "2026-05-15") == ["JPM_10-Q_2026-05-01"]
    # old_q: 5.2 - 2.0 x 0.53 years = 4.14; new_q: 5.0 - 0.08 = 4.92 -> the newer 10-Q wins the near-tie
    assert [c.chunk_id for c in got] == ["new_q", "old_q", "risk"]


def test_clearly_more_relevant_older_passage_still_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(retriever, "get_collection", lambda: FakeCollection())
    monkeypatch.setattr(retriever, "embed", lambda texts: [[0.0]])
    monkeypatch.setattr(retriever.config, "RETRIEVAL_TRACE_PATH", tmp_path / "t.jsonl")
    monkeypatch.setattr(retriever.config, "RETRIEVE_KEYWORD_K", 0)
    scores = {"net interest income third quarter": 7.0, "net interest income first quarter": 2.0, "risk factors": -3}
    monkeypatch.setattr(retriever, "rerank_scores", lambda q, texts: [scores[t] for t in texts])
    got = retriever.retrieve("q", ticker="JPM", as_of="2026-05-15")
    assert got[0].chunk_id == "old_q"


def test_keyword_search_finds_exact_terms():
    texts = {"a": "Item 5.02 Departure of Directors or Certain Officers. The chief executive officer resigned.",
             "b": "Item 8.01 Other Events. The company issued senior notes due 2031.",
             "c": "Revenue grew on strong demand for data center products."}

    class Col:
        def count(self):
            return len(texts)

        def get(self, where, include):
            return {"ids": list(texts), "documents": list(texts.values())}

    assert retriever.keyword_top(Col(), "Item 5.02 executive change", "X", None, k=5) == ["a", "b"]  # b: "Item" only
    assert retriever.keyword_top(Col(), "senior notes issued", "X", None, k=5)[0] == "b"
    assert retriever.keyword_top(Col(), "dividend policy", "X", None, k=5) == []   # no shared term: nothing


def test_boilerplate_cut_at_other_boundaries_is_a_duplicate():
    para = " ".join(f"risk{i} competition{i} affects margin{i} and demand{i}" for i in range(30))
    later_copy = "The following updates our risk factors. " + para[200:] + " New sentence about AI rules."
    assert retriever._near_duplicate(para, later_copy)          # same text, different chunk boundaries
    assert not retriever._near_duplicate(para, "Revenue grew 12% on cloud demand. " * 20)
    assert not retriever._near_duplicate("Acme wins deal", "Acme wins deal today")   # short news: never
