"""Step 5: same question answered without retrieval (model memory only) and with the RAG agents.

Example (from the project root):
    python scripts/05_compare.py AAPL "What did Apple report for Services revenue in its most recent quarter, and what risks did it highlight?"

Saves both answers side by side to logs/compare_<time>_<TICKER>.md (evidence for Checkpoint 3).
"""
import argparse
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import llm  # noqa: E402
from stockagent.research_graph import render_markdown, run_research  # noqa: E402


class BaselineAnswer(BaseModel):
    answer: str
    sources: list[str] = Field(description="Where the information comes from, if you can say")
    confidence_percent: int = Field(description="How confident you are that the answer is correct, 0-100")
    knowledge_cutoff_note: str = Field(description="Any caveat about how current your knowledge is")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("question")
    ap.add_argument("--as-of", default=time.strftime("%Y-%m-%d"))
    args = ap.parse_args()

    print(f"[1/2] Without retrieval ({llm.describe()}) ...")
    t0 = time.time()
    try:
        base = llm.structured(
            "You are an equity research assistant. Answer from your own knowledge.",
            f"Today is {args.as_of}. Question about {args.ticker}: {args.question}", BaselineAnswer)
        base_md = (f"{base.answer}\n\n- Sources given: {', '.join(base.sources) or 'none'}\n"
                   f"- Self-reported confidence: {base.confidence_percent}%\n"
                   f"- Caveat: {base.knowledge_cutoff_note}")
    except llm.LLMError as exc:
        base_md = f"(baseline call failed: {exc})"
    t_base = time.time() - t0

    print("[2/2] With retrieval (LangGraph researcher + critic) ...")
    t0 = time.time()
    final = run_research(args.question, args.ticker, args.as_of)
    t_rag = time.time() - t0

    out = "\n".join([
        f"# Retrieval comparison: {args.ticker}", f"Question: {args.question}", f"As of: {args.as_of} | LLM: {llm.describe()}", "",
        f"## Without retrieval ({t_base:.0f}s)", base_md, "",
        f"## With retrieval ({t_rag:.0f}s)", render_markdown(final), "",
        f"Full agent trace: {final['trace_file']}; retrieval log: {config.RETRIEVAL_TRACE_PATH.relative_to(config.PROJECT_ROOT)}",
    ])
    path = config.LOG_DIR / f"compare_{time.strftime('%Y%m%d_%H%M%S')}_{args.ticker.upper()}.md"
    path.write_text(out, encoding="utf-8")
    print(out.encode("ascii", "replace").decode())
    print(f"\nSaved {path.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
