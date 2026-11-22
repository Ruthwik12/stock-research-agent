"""Step 14: score retrieval on a fixed test set (eval/retrieval_cases.json).

Each case is a question for one company as of a date, plus a rule for which passages answer it: the
passage must come from one of the listed filings (or a filing family / section) and contain every
listed pattern (case-insensitive regex). Relevance is judged by that rule, never by the retriever.

Metrics over the cases (top 5 passages, as the agents see them):
  hit@5     share of cases with at least one answering passage in the top 5
  MRR       mean of 1 / rank of the first answering passage (0 if none)
  first     share of cases whose first passage answers ("latest" cases: the newest filing comes first)

Configurations compared: vector order only (before 2026-11-19), the current settings, and a small
grid over the recency penalty and the distance cutoff. Read-only: the index is never written.

Run from the project root (does not need the pipeline lock; avoid running during the nightly ingest):
    python scripts/14_eval_retrieval.py
"""
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import retriever  # noqa: E402
from stockagent.vectorstore import get_collection  # noqa: E402

CASES_PATH = config.PROJECT_ROOT / "eval" / "retrieval_cases.json"


def answers(case: dict, chunk_id: str, section: str, text: str) -> bool:
    rel, doc_id = case["relevant"], chunk_id.split("::")[0]
    if "docs" in rel and doc_id not in rel["docs"]:
        return False
    if "doc_prefix" in rel and not doc_id.startswith(rel["doc_prefix"]):
        return False
    if "section" in rel and section != rel["section"]:
        return False
    return all(re.search(p, text, re.IGNORECASE) for p in rel["text_has"])


def answerable(case: dict, collection) -> int:
    """How many indexed passages answer the case (0 = the case is broken, not the retriever)."""
    got = collection.get(where=retriever._where(case["ticker"], case["as_of"]), include=["documents", "metadatas"])
    return sum(answers(case, cid, m["section"], text)
               for cid, text, m in zip(got["ids"], got["documents"], got["metadatas"]))


def score(cases: list[dict], settings: dict) -> dict:
    saved = {k: getattr(config, k) for k in settings}
    for k, v in settings.items():
        setattr(config, k, v)
    try:
        ranks = []
        for c in cases:
            got = retriever.retrieve(c["query"], ticker=c["ticker"], as_of=c["as_of"],
                                     fetch_k=config.RETRIEVE_FETCH_K, max_distance=config.MAX_COSINE_DISTANCE,
                                     caller="14_eval_retrieval")
            hit = [i for i, ch in enumerate(got, 1) if answers(c, ch.chunk_id, ch.section, ch.text)]
            ranks.append(hit[0] if hit else None)
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
    n = len(cases)
    return {"hit@5": sum(r is not None for r in ranks) / n, "MRR": sum(1 / r for r in ranks if r) / n,
            "first": sum(r == 1 for r in ranks) / n, "ranks": ranks}


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    collection = get_collection()
    broken = [c["id"] for c in cases if not answerable(c, collection)]
    if broken:
        sys.exit(f"no indexed passage answers these cases (fix the case or the index): {broken}")

    # The same query embeddings and reranker scores recur across configurations: compute each once.
    embed, rerank = retriever.embed, retriever.rerank_scores
    embed_once = lru_cache(maxsize=None)(lambda q: tuple(map(tuple, embed([q]))))
    rerank_once = lru_cache(maxsize=None)(lambda q, texts: tuple(rerank(q, list(texts))))
    retriever.embed = lambda texts: [list(v) for v in embed_once(texts[0])]
    retriever.rerank_scores = lambda q, texts: list(rerank_once(q, tuple(texts)))

    configs = {
        "vector order only (before)": {"RERANKER_MODEL": None, "RECENCY_PENALTY_PER_YEAR": 0.0,
                                       "RETRIEVE_LATEST_K": 0, "RETRIEVE_FETCH_K": 10, "RETRIEVE_KEYWORD_K": 0,
                                       "NEAR_DUPLICATE_CONTAINMENT": 1.01},
        "reranker only": {"RECENCY_PENALTY_PER_YEAR": 0.0, "RETRIEVE_LATEST_K": 0, "RETRIEVE_KEYWORD_K": 0},
        "reranker + recency": {"RETRIEVE_KEYWORD_K": 0, "NEAR_DUPLICATE_CONTAINMENT": 1.01},
        "+ boilerplate removal": {"RETRIEVE_KEYWORD_K": 0},
        "+ keyword search (current)": {},
    }
    for penalty in (1.0, 4.0):
        configs[f"current, penalty {penalty}/year"] = {"RECENCY_PENALTY_PER_YEAR": penalty}
    for k in (5, 20):
        configs[f"current, {k} keyword candidates"] = {"RETRIEVE_KEYWORD_K": k}

    latest = [c for c in cases if c["kind"] == "latest"]
    print(f"{len(cases)} cases ({len(latest)} 'latest quarter' cases); current: penalty "
          f"{config.RECENCY_PENALTY_PER_YEAR}/year, cutoff {config.MAX_COSINE_DISTANCE}\n")
    print(f"{'configuration':30} {'hit@5':>6} {'MRR':>6} {'first':>6} | latest: {'hit@5':>6} {'first':>6}")
    results = {}
    for name, settings in configs.items():
        r, rl = score(cases, settings), score(latest, settings)
        results[name] = {"all": r, "latest": rl}
        print(f"{name:30} {r['hit@5']:6.2f} {r['MRR']:6.2f} {r['first']:6.2f} | latest: "
              f"{rl['hit@5']:6.2f} {rl['first']:6.2f}")
    print("\nrank of the first answering passage per case (- = not in top 5):")
    for name in ("vector order only (before)", "+ keyword search (current)"):
        print(f"  {name:28}", " ".join(f"{c['id']}={r or '-'}" for c, r in zip(cases, results[name]["all"]["ranks"])))
    out = config.LOG_DIR / "retrieval_eval.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"\nsaved {out.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
