"""Step 6: show the retrieval failure mode (wrong company, wrong period) and the filters fixing it.

Two cases, each run once without and once with metadata filters:
  A. Wrong company: researching AAPL with a query in cloud/AI vocabulary that Microsoft's filings
     match better. Unfiltered search returns MSFT passages; the ticker filter removes them.
  B. Look-ahead: a backtest dated 2026-05-15 asks for "the latest quarterly results". Unfiltered
     search can return filings published after that date; the as-of filter removes them.

Saves a markdown report to logs/failure_demo_<time>.md (evidence for Checkpoint 3).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.retriever import retrieve  # noqa: E402

CASES = [
    {"name": "A. Wrong company", "target": "AAPL", "as_of": None,
     "query": "growth in cloud services revenue and investment in AI infrastructure and data centers"},
    {"name": "B. Look-ahead (backtest dated 2026-05-15)", "target": "JPM", "as_of": "2026-05-15",
     "query": "net interest income in the most recent quarter compared with the prior year"},
]


def table(chunks, target, as_of) -> list[str]:
    rows = ["| # | Distance | Source | Problem |", "|---|---|---|---|"]
    for n, c in enumerate(chunks, 1):
        problem = []
        if c.ticker != target:
            problem.append("wrong company")
        if as_of and c.filing_date > as_of:
            problem.append("filed after as-of date")
        rows.append(f"| {n} | {c.distance:.3f} | {c.citation()} | {', '.join(problem) or 'ok'} |")
    return rows


def main() -> None:
    lines = ["# Retrieval failure-mode demo", f"Run {time.strftime('%Y-%m-%d %H:%M')}, top {config.RETRIEVE_FINAL_K} "
             f"chunks, embedding model {config.EMBED_MODEL}", ""]
    for case in CASES:
        lines += [f"## {case['name']}", f"Researching **{case['target']}**. Query: \"{case['query']}\"", ""]
        unfiltered = retrieve(case["query"], caller="06_failure_demo:unfiltered")
        filtered = retrieve(case["query"], ticker=case["target"], as_of=case["as_of"], caller="06_failure_demo:filtered")
        bad = sum(c.ticker != case["target"] or (case["as_of"] and c.filing_date > case["as_of"]) for c in unfiltered)
        lines += ["**Without filters:**", ""] + table(unfiltered, case["target"], case["as_of"])
        lines += ["", f"{bad} of {len(unfiltered)} retrieved passages are unusable for this question.", ""]
        lines += [f"**With filters** (ticker={case['target']}, as_of={case['as_of']}):", ""]
        lines += table(filtered, case["target"], case["as_of"]) + [""]
    path = config.LOG_DIR / f"failure_demo_{time.strftime('%Y%m%d_%H%M%S')}.md"
    config.LOG_DIR.mkdir(exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines).encode("ascii", "replace").decode())
    print(f"\nSaved {path.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
