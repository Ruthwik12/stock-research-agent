"""Step 4: query the filings index from the command line and see what comes back.

Every call is also appended to logs/retrieval_trace.jsonl (query, filters, candidates, scores).

Examples (from the project root):
    python scripts/04_retrieve.py "services revenue growth" --ticker AAPL --as-of 2026-10-25
    python scripts/04_retrieve.py "cloud revenue growth"            # no filters: shows the failure mode
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.retriever import retrieve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--ticker")
    ap.add_argument("--as-of", help="YYYY-MM-DD; only filings dated on or before this")
    ap.add_argument("--k", type=int, default=config.RETRIEVE_FINAL_K)
    args = ap.parse_args()

    chunks = retrieve(args.query, ticker=args.ticker, as_of=args.as_of, final_k=args.k, caller="04_retrieve")
    print(f"Query: {args.query!r}  filters: ticker={args.ticker} as_of={args.as_of}  -> {len(chunks)} chunks\n")
    for n, c in enumerate(chunks, 1):
        print(f"[{n}] distance={c.distance:.3f}  {c.citation()}\n    {c.chunk_id}")
        print("    " + c.text[:300].replace("\n", " ") + " ...\n")
    print(f"Trace appended to {config.RETRIEVAL_TRACE_PATH.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
