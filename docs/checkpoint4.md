# Capstone Checkpoint 4: Tree-of-Thought Reasoning for the Stock Research Agent

Nov 2, 2026 · Ruthwik

## 1. Decision: ToT in one stage only

ToT improves one part of my agent: building the investment thesis behind each probability forecast, where competing explanations (strong earnings, cautious guidance, rising debt) must be compared before committing. Elsewhere it would only add cost. Screening, retrieval and arithmetic have one correct path, so they stay linear, as does the Checkpoint 3 research loop.

## 2. Where ToT is used and why a linear chain fails

ToT runs in a new **thesis stage** between research and forecasting, once per shortlisted stock. It decides which drivers matter over 90 days and how far they move the probability of beating the S&P 500 from the 50% base rate. A linear chain fails here because of:

- **Premature commitment:** the first passage sets the story; "revenue beat" produces a bullish chain that never weighs the guidance cut.
- **High branching:** earnings, guidance, margins, debt, regulation and macro are all plausible drivers, each needing its own evidence.
- **Constraint complexity:** every thesis must use only the right company's filings, dated before the prediction date, with cited support. One early violation contaminates the whole chain.

## 3. ToT structure

| Element | Definition |
| --- | --- |
| Thought | One candidate thesis: driver, direction (bull or bear) and 90-day mechanism, e.g. "Bear: rising DRAM costs compress Apple's margin" |
| Node (state) | The thesis plus its cited evidence, verified claims, score and, at the last level, a probability adjustment |
| Branch | One path from the root (ticker, as-of date, research evidence, quantitative signals) to a leaf |
| Branching and depth | Level 1 generates 6 theses (3 bull, 3 bear); level 2 tests each kept thesis with 2 children (refined thesis, strongest counter-argument); level 3 quantifies the adjustment. Depth limit 3 |

**Termination:** at depth 3; earlier if all kept branches agree in direction with scores of 8+; or when the budget runs out (confidence lowered). If no branch survives the hard checks, the output is "insufficient evidence" at the base rate. The decision maker then sums the surviving leaves' weighted adjustments, caps them at ±15 points and shrinks toward 50% using calibration history. If the best bull and bear branches both score 7+, the pick is labelled "contested" and the adjustment halved.

## 4. Search strategy: beam search

I use **beam search, width 3**: expand the kept nodes, score the children, keep the best 3, always including at least one bull and one bear branch.

| Strategy | Compute (LLM calls per stock) | Latency | Complexity and fit |
| --- | --- | --- | --- |
| **Beam (width 3)** | About 12 | 3 parallel rounds | Needs a reliable scoring function; keeps both sides at a fixed cost |
| Pure BFS (no pruning) | About 30 | 3 rounds, 2.5x the work | Simple, but expands every branch |
| DFS | About 5 | Lowest | Simple, but commits to the first branch, the failure ToT targets |
| Monte Carlo | 50+ | Highest | Needs a reward signal that arrives only 90 days later |

Beam search gives most of pure BFS's coverage at under half its cost; the price is the scoring function in section 5.

## 5. Evaluation and pruning

**Hard checks (code, reused from the Checkpoint 3 verifier):** every claim must cite a retrieved passage from the target company, filed on or before the prediction date, and every number must appear in that passage. Any failure prunes the branch. Survivors get a 0-10 score:

| Criterion | Weight | Evaluated by |
| --- | --- | --- |
| Evidence support (share of claims supported) | 40% | Critic agent, plus code checks |
| Relevance within 90 days | 20% | Critic agent |
| Materiality | 20% | Critic agent with calculator and market-data tools |
| Consistency with quantitative signals | 10% | Tool calls |
| Novelty versus other kept branches | 10% | Word-overlap heuristic |

**Pruning:** branches scoring below 5 are discarded; the top 3 survive under the bull-and-bear rule. If a critic call fails, the branch keeps its code-based score and confidence is lowered. **Ties:** more distinct source filings wins, then newer evidence, then lower cost; otherwise a pairwise critic vote. **Budget:** at most 15 LLM calls and 3 minutes per stock; under a tight daily budget the shortlist shrinks, not the beam.

## 6. Mapping ToT roles to tools

| Role | Tool | Why |
| --- | --- | --- |
| Thought generator | **CrewAI** Bull Analyst and Bear Analyst agents, called from a LangGraph node | Role separation keeps each side's argument focused |
| Critic / evaluator | **LangChain** LCEL chain with structured output, plus the verifier and calculator as tools | Validated scores; code checks beside LLM judgment |
| Decision maker / controller | **LangChain's LangGraph**: expand, evaluate, prune and decide nodes with conditional edges | Explicit, testable loop and stop rules; CrewAI processes cannot express pruning as precisely |
| Memory / state manager | **MCP** servers (filings retrieval, market data, predictions memory) plus a LangGraph SQLite checkpointer | All roles read the same filtered data through MCP; every branch (parent, thought, evidence, score) is recorded for audit |

## 7. Updated architecture

[Diagram: Agent workflow with the ToT thesis stage highlighted. Screener → Researcher (RAG, linear) → Thesis stage: Tree of Thought (Generate → Evaluate → Prune, looping to depth 3 → Decide) → Forecaster → Ranker / report → Prediction log → Reflector; MCP servers (filings retrieval, market data, predictions memory) underneath.]

Only the thesis stage branches; the rest of the pipeline stays linear. The decision maker hands one adjusted probability to the forecaster, and every prediction and pruned branch is logged for the reflector.

## 8. Risk and mitigation

**Risk: weak evaluation signals prune the best branch.** The most important driver is often new and thinly documented. In my Checkpoint 3 run, Apple's new memory-cost risk appeared in only one filing, so an evidence-hungry score would prune it.

**Mitigation:**

- Score evidence as the *share* of claims supported, not the citation count.
- Prune outright only on verifiable failures; soft scores only rank.
- Always keep the best bull and best bear branch.
- Log pruned branches; the reflector checks how often they proved right and flags the weights for revision (the Checkpoint 2 self-correction loop).
