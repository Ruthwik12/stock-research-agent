"""Run the LangGraph researcher + critic agents on one question.

Example (from the project root):
    python scripts/run_research.py AAPL "What new risks did Apple highlight in its latest quarter?"
    python scripts/run_research.py JPM "How did net interest income change?" --as-of 2026-07-31

Writes the full state trace (logs/runs/<time>_<TICKER>.json) and a markdown report (.md).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import llm  # noqa: E402
from stockagent.research_graph import render_markdown, run_research  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("question")
    ap.add_argument("--as-of", default=time.strftime("%Y-%m-%d"), help="YYYY-MM-DD (default today)")
    args = ap.parse_args()

    print(f"Running research agent with {llm.describe()} ...")
    t0 = time.time()
    final = run_research(args.question, args.ticker, args.as_of)
    print(render_markdown(final).encode("ascii", "replace").decode())
    print(f"\n({time.time() - t0:.0f}s; full trace: {final['trace_file']})")


if __name__ == "__main__":
    main()
