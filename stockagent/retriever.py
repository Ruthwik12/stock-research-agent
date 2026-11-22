"""Retrieval over SEC filings and news: vector + keyword (BM25) candidates with ticker / as-of-date filters,
cross-encoder reranking, a recency preference, boilerplate removal, and a logged trace.

Every call appends one JSON line to logs/retrieval_trace.jsonl: the query, the filters, every
candidate with its distance, reranker score and final score, and whether it was kept
or why it was dropped. That file is the retrieval evidence for Checkpoint 3.
"""
import json
import math
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np

import config
from stockagent.embeddings import embed
from stockagent.vectorstore import get_collection


@dataclass
class Chunk:
    chunk_id: str
    text: str
    distance: float
    ticker: str
    form_type: str
    filing_date: str
    period_end: str
    section: str
    source_url: str

    def citation(self) -> str:
        return cite(asdict(self))


def cite(e: dict) -> str:
    """Human-readable source label for an evidence item (filing, news report, or computed summary)."""
    ticker = e.get("ticker", "")
    if e["form_type"] == "news":
        return f"{e['section'].removeprefix('News: ')} news report on {ticker}, published {e['filing_date']}"
    if e["form_type"] in ("Form 4 summary", "FRED macro"):
        origin = "SEC Form 4" if e["form_type"] == "Form 4 summary" else "FRED"
        return f"{e['section']} (computed from {origin} data available on {e['filing_date']})"
    period = f", period ended {e['period_end']}" if e.get("period_end") else ""
    return f"{ticker} {e['form_type']} filed {e['filing_date']}{period}, {e['section']}".strip()


def _where(ticker: str | None, as_of: str | None) -> dict | None:
    clauses = []
    if ticker:
        clauses.append({"ticker": ticker.upper()})
    if as_of:   # no document filed after the as-of date: blocks look-ahead leakage in backtests
        clauses.append({"filing_date_int": {"$lte": int(as_of.replace("-", ""))}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _near_duplicate(a: str, b: str) -> bool:
    """Boilerplate repeated across filings (risk factors copied from one 10-Q to the next) adds no evidence.
    Measured as containment: the share of the shorter passage's words found in the other. The same text cut
    at different chunk boundaries keeps a high containment where word-set overlap (Jaccard) falls to ~0.5."""
    wa, wb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    shorter = min(len(wa), len(wb))
    return shorter >= 50 and len(wa & wb) / shorter >= config.NEAR_DUPLICATE_CONTAINMENT   # news snippets: too short


def latest_doc_ids(collection, ticker: str, as_of: str | None) -> list[str]:
    """The newest periodic report (10-Q or 10-K) and the newest earnings release filed on or before as_of.
    Similarity alone has no notion of "latest", so their passages always join the candidate pool."""
    newest: dict[str, tuple[str, str]] = {}
    for m in collection.get(where=_where(ticker, as_of), include=["metadatas"])["metadatas"]:
        group = "periodic" if m["form_type"] in ("10-Q", "10-K") else m["form_type"]
        if group in ("periodic", "8-K-earnings") and m["filing_date"] > newest.get(group, ("", ""))[0]:
            newest[group] = (m["filing_date"], m["doc_id"])
    return [doc_id for _, doc_id in newest.values()]


_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")   # keeps "5.02" and "99.1" whole
_KEYWORD_INDEX: dict[tuple, dict] = {}


def _keyword_index(collection, ticker: str, as_of: str | None) -> dict:
    """Token counts per chunk for one company's passages filed on or before as_of. Cached per index size,
    so a nightly ingest (more chunks) rebuilds it."""
    key = (ticker, as_of, collection.count())
    if key not in _KEYWORD_INDEX:
        got = collection.get(where=_where(ticker, as_of), include=["documents"])
        docs = {cid: Counter(_TOKEN.findall(text.lower())) for cid, text in zip(got["ids"], got["documents"])}
        df = Counter(t for c in docs.values() for t in c)
        _KEYWORD_INDEX.clear()   # one company at a time is enough: a research run stays on one ticker
        _KEYWORD_INDEX[key] = {"docs": docs, "df": df,
                               "avg_len": sum(sum(c.values()) for c in docs.values()) / max(len(docs), 1)}
    return _KEYWORD_INDEX[key]


def keyword_top(collection, query: str, ticker: str, as_of: str | None, k: int) -> list[str]:
    """Chunk ids ranked by BM25 keyword score: exact terms ("Item 5.02", "EYLEA HD", "10b5-1") that an
    embedding can blur. Chunks matching no query term are never returned."""
    idx = _keyword_index(collection, ticker, as_of)
    n, k1, b = len(idx["docs"]), 1.2, 0.75
    terms = set(_TOKEN.findall(query.lower()))
    idf = {t: math.log(1 + (n - idx["df"][t] + 0.5) / (idx["df"][t] + 0.5)) for t in terms if idx["df"][t]}
    scores = {}
    for cid, counts in idx["docs"].items():
        length = sum(counts.values())
        s = sum(w * counts[t] * (k1 + 1) / (counts[t] + k1 * (1 - b + b * length / idx["avg_len"]))
                for t, w in idf.items() if counts[t])
        if s > 0:
            scores[cid] = s
    return sorted(scores, key=scores.get, reverse=True)[:k]


_CROSS_ENCODER = None


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    """Cross-encoder relevance (a logit: higher = more relevant). It reads query and passage together,
    so it judges relevance better than the embedding distance used to fetch the candidates."""
    global _CROSS_ENCODER
    if _CROSS_ENCODER is None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        _CROSS_ENCODER = TextCrossEncoder(config.RERANKER_MODEL, cache_dir=str(config.RERANKER_CACHE))
    return [float(s) for s in _CROSS_ENCODER.rerank(query, texts)]


def _age_years(filing_date: str, as_of: str | None) -> float:
    end = date.fromisoformat(as_of) if as_of else date.today()
    return max(0.0, (end - date.fromisoformat(filing_date)).days / 365.25)


def retrieve(query: str, ticker: str | None = None, as_of: str | None = None,
             fetch_k: int = config.RETRIEVE_FETCH_K, final_k: int = config.RETRIEVE_FINAL_K,
             max_distance: float = config.MAX_COSINE_DISTANCE, caller: str = "") -> list[Chunk]:
    """Top chunks for query, restricted to ticker and to filings dated on or before as_of (YYYY-MM-DD).

    Candidates: the fetch_k nearest chunks, plus (with a ticker) the nearest chunks of the newest 10-Q/10-K
    and earnings release and the top BM25 keyword matches. Order: cross-encoder score minus RECENCY_PENALTY_PER_YEAR x filing age, so
    an older filing must be clearly more relevant to outrank a newer one. Then the distance cutoff, news
    cap, near-duplicate check and final_k are applied in that order."""
    collection = get_collection()
    where = _where(ticker, as_of)
    qvec = embed([query])
    include = ["documents", "metadatas", "distances"]
    results = [collection.query(query_embeddings=qvec, n_results=fetch_k, where=where, include=include)]
    latest = latest_doc_ids(collection, ticker, as_of) if ticker and config.RETRIEVE_LATEST_K else []
    if latest:
        results.append(collection.query(query_embeddings=qvec, n_results=config.RETRIEVE_LATEST_K, include=include,
                                        where={"$and": [where, {"doc_id": {"$in": latest}}]}))
    pool: dict[str, tuple] = {}
    for res in results:
        for cid, text, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0],
                                         res["distances"][0]):
            pool[cid] = (text, meta, dist)
    keyword = [c for c in keyword_top(collection, query, ticker, as_of, config.RETRIEVE_KEYWORD_K)
               if c not in pool] if ticker and config.RETRIEVE_KEYWORD_K else []
    if keyword:   # keyword-only hits get their real vector distance, so the distance cutoff still applies
        got = collection.get(ids=keyword, include=["documents", "metadatas", "embeddings"])
        q = np.asarray(qvec[0])
        for cid, text, meta, vec in zip(got["ids"], got["documents"], got["metadatas"], got["embeddings"]):
            v = np.asarray(vec)
            pool[cid] = (text, meta, float(1 - q @ v / (np.linalg.norm(q) * np.linalg.norm(v))))
    ids = list(pool)
    ce = rerank_scores(query, [pool[c][0] for c in ids]) if config.RERANKER_MODEL and ids else [0.0] * len(ids)
    score = {c: s - config.RECENCY_PENALTY_PER_YEAR * _age_years(pool[c][1]["filing_date"], as_of)
             for c, s in zip(ids, ce)}
    rerank = dict(zip(ids, ce))

    kept: list[Chunk] = []
    trace_rows = []
    for cid in sorted(ids, key=lambda c: -score[c]):
        text, meta, dist = pool[cid]
        chunk = Chunk(chunk_id=cid, text=text, distance=round(dist, 4), ticker=meta["ticker"],
                      form_type=meta["form_type"], filing_date=meta["filing_date"],
                      period_end=meta.get("period_end", ""), section=meta["section"],
                      source_url=meta["source_url"])
        if dist > max_distance:
            status = f"dropped: distance > {max_distance}"
        elif chunk.form_type == "news" and \
                sum(k.form_type == "news" for k in kept) >= config.NEWS_MAX_PER_QUERY:
            status = "dropped: news cap reached"   # short news snippets must not crowd out filings
        elif len(kept) >= final_k:
            status = "dropped: beyond final_k"
        elif any(_near_duplicate(text, k.text) for k in kept):
            status = "dropped: near-duplicate of a kept chunk"
        else:
            kept.append(chunk)
            status = "kept"
        trace_rows.append({"chunk_id": cid, "distance": chunk.distance, "rerank": round(rerank[cid], 3),
                           "score": round(score[cid], 3), "latest_doc": meta.get("doc_id") in latest,
                           "keyword_only": cid in keyword, "status": status,
                           "citation": chunk.citation(), "preview": text[:160].replace("\n", " ")})

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.RETRIEVAL_TRACE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "caller": caller, "query": query,
                            "filters": {"ticker": ticker, "as_of": as_of}, "fetch_k": fetch_k,
                            "final_k": final_k, "max_distance": max_distance,
                            "candidates": trace_rows}) + "\n")
    return kept


def chunk_to_dict(chunk: Chunk) -> dict:
    return asdict(chunk)
