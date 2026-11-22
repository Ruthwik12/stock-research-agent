# Stock Research Agent: Progress Log

CMU agentic AI capstone, Forecaster track. Decision support only, not financial advice. Paper trading only.

- **Full design and background:** [docs/Stock_Agent_Capstone_Handoff.md](docs/Stock_Agent_Capstone_Handoff.md) (agent roles, probability method, RAG design, Checkpoint 3 draft in Appendix B).
- **This file** is the running record: what is built, decisions made, real results, and what comes next. Update it after every step.
- **Rule:** only real outputs from scripts that were actually run go in here and in the checkpoint write-ups. No invented results.

---

## Current status (2026-10-25)

| Checkpoint | Status |
|---|---|
| 1. Problem, user, environment | Submitted, positive feedback |
| 2. Reasoning loop, memory, tools | Submitted, positive feedback (instructor suggested self-correction loop) |
| 3. Retrieval (RAG) | **Write-up drafted** (891 words, real results), ready to review and submit: [docs/checkpoint3.md](docs/checkpoint3.md) |

### Checkpoint 3 build plan

| # | Step | Script | Status |
|---|---|---|---|
| 1 | Download filings from SEC EDGAR (incremental) | `scripts/01_download_filings.py` | Done, 26 filings |
| 2 | Extract clean text, split by section | `scripts/02_parse_sections.py` | Done |
| 3 | Chunk, embed (bge-m3), store in Chroma | `scripts/03_ingest.py` | Done, 1,214 chunks |
| 4 | Retrieval with ticker and as-of filters, trace log | `stockagent/retriever.py`, `scripts/04_retrieve.py` | Done |
| - | LangGraph researcher + critic agents | `stockagent/research_graph.py`, `scripts/run_research.py` | Done, first real run with Claude |
| - | Daily auto-update (download, parse, index) | `scripts/update_daily.py`, Windows task | Scheduled daily 9:00 PM |
| 5 | With vs. without retrieval comparison | `scripts/05_compare.py` | Done, real results in log |
| 6 | Failure-mode demo (wrong company / look-ahead) | `scripts/06_failure_demo.py` | Done, real results in log |
| 7 | Checkpoint 3 write-up, 600-900 words | `docs/checkpoint3.md` + Claude Doc | Drafted, 891 words; review and submit |

---

## How to run

From PowerShell in `stock-research-agent`:

```powershell
.\.venv\Scripts\python scripts\01_download_filings.py      # new filings only
.\.venv\Scripts\python scripts\02_parse_sections.py        # new documents only (--force to redo all)
.\.venv\Scripts\python scripts\03_ingest.py                # new documents only (--force to rebuild)
.\.venv\Scripts\python scripts\04_retrieve.py "services revenue growth" --ticker AAPL --as-of 2026-10-25
.\.venv\Scripts\python scripts\run_research.py AAPL "What new risks did Apple highlight in its latest quarter?"
.\.venv\Scripts\python scripts\05_compare.py AAPL "<question>"
.\.venv\Scripts\python scripts\06_failure_demo.py
```

Needs Ollama running (it starts with Windows from the system tray) with `bge-m3` and `qwen3:14b` pulled.

### Settings and the Claude API key

All local settings live in **`stock-research-agent\.env`** (git-ignored, never share it):

```
SEC_USER_AGENT=Your Name your.email@example.com     <- replace the placeholder (SEC requires a real contact)
LLM_PROVIDER=ollama                                  <- change to anthropic once the key is in
ANTHROPIC_API_KEY=                                   <- paste the Claude API key here
```

With `LLM_PROVIDER=anthropic` the agents call Claude (`claude-opus-5-5` by default, override with `ANTHROPIC_MODEL`) through the official Anthropic SDK. With `ollama` they use local `qwen3:14b`. The code path is the same either way (`stockagent/llm.py`).

### Daily auto-update

Windows scheduled task **StockAgent-DailyFilingsUpdate** runs `scripts/update_daily.py` every day at 9:00 PM (or at the next chance if the laptop was off). It runs steps 1-3; each step only processes new filings. Output: `logs/update_daily.log`.

```powershell
Start-ScheduledTask -TaskName StockAgent-DailyFilingsUpdate                          # run now
Get-ScheduledTask -TaskName StockAgent-DailyFilingsUpdate | Get-ScheduledTaskInfo     # last/next run, result
Unregister-ScheduledTask -TaskName StockAgent-DailyFilingsUpdate -Confirm:$false      # remove
powershell -ExecutionPolicy Bypass -File scripts\schedule_daily_update.ps1            # re-register
```

## Project layout

```
stock-research-agent/
  config.py                   all settings (tickers, chunking, top-k, LLM provider, budgets)
  .env                        local secrets/settings (SEC contact, LLM provider, Claude key)
  PROGRESS.md                 this file
  docs/                       handoff document; checkpoint write-ups go here
  scripts/                    numbered pipeline steps + run_research, update_daily, scheduler
  stockagent/
    embeddings.py             bge-m3 via Ollama, with retries
    vectorstore.py            Chroma collection (cosine)
    retriever.py              filtered semantic search + retrieval trace log
    llm.py                    structured LLM calls: Ollama or Claude (Anthropic SDK)
    research_graph.py         LangGraph researcher + critic agents
  data/raw/                   raw EDGAR HTML + manifest.json (source of truth)
  data/parsed/                clean section text per document
  chroma_db/                  vector store (rebuildable from data/)
  logs/                       retrieval_trace.jsonl, runs/, compare_*.md, failure_demo_*.md, update_daily.log
```

---

## Agent design (LangGraph)

Yes, the agents use **LangGraph** (`langgraph` 1.2.17, `StateGraph`). Current graph:

```
START -> plan -> retrieve -> assess --(evidence missing, budget left)--> retrieve
                               |
                               v
                            answer -> verify --(>1/3 of claims rejected, 1 revision left)--> revise -> answer
                                         |
                                         v
                                     finalize -> END
```

- **plan:** LLM writes 1-3 search queries.
- **retrieve:** deterministic tool call; always filtered by ticker and as-of date.
- **assess:** LLM judges whether the evidence answers the question; asks for follow-up queries if not (max 3 retrieval rounds).
- **answer (researcher):** LLM answers as atomic claims, each citing evidence IDs (E1, E2, ...).
- **verify (critic):** code checks first, then a separate LLM critic judges each claim against only its cited text.
- **finalize:** report built from verified claims only, with sources, gaps, critic-flagged risks, removed claims, and the disclaimer.

**Anti-hallucination guardrails (enforced in code, not only in prompts):**
1. Every retrieval is filtered by ticker and as-of date (no other company, no future filings).
2. Structured outputs only (Pydantic schemas); malformed model output is retried, never parsed loosely.
3. Every claim must cite evidence IDs; claims with no valid citation are removed.
4. Cited chunks must match the ticker and be dated on or before the as-of date.
5. Every number in a claim must appear in the cited text (unit rescaling and 0.5% rounding allowed).
6. A separate critic call judges support claim by claim; unsupported claims are removed. If more than a third are rejected, the answer is sent back once with the critic's reasons.
7. The final answer is assembled from verified claims; there is no free-form summary that could slip in unverified text.
8. Evidence strength (strong / moderate / weak / insufficient) is computed from verification results, never self-reported by the model.
9. Fail closed: no evidence or a failed LLM call gives "insufficient evidence", not a guess. Step budgets (3 retrieval rounds, 1 revision, LangGraph recursion limit 30) bound every loop.
10. Retrieved filing text is treated as data; prompts tell the model to ignore instructions inside it.
11. Weak matches (cosine distance > 0.55) and near-duplicate chunks are dropped before the agent sees them.

Every run saves its full state trace (`logs/runs/*.json`) and a markdown report (`logs/runs/*.md`); every retrieval is logged to `logs/retrieval_trace.jsonl`.

---

## Decisions made

| Decision | Choice | Why |
|---|---|---|
| Tickers | AAPL, MSFT, JPM | Two tech names share vocabulary (cloud, services, AI), so "retrieved the wrong company" is a realistic failure to demonstrate; JPM is a bank with very different language. |
| Documents per ticker | up to 2 10-Ks, 3 10-Qs, 4 earnings 8-Ks (most recent); downloads accumulate | About a year of history per company for as-of-date (look-ahead) tests. |
| Earnings 8-K content | Exhibit 99.1 only (press release) | The 8-K body is a cover page; exhibit 99.2 at JPM is a table-heavy supplement better suited to structured data. |
| Sections kept | 10-K: Risk Factors, MD&A. 10-Q: MD&A, Risk Factors. 8-K: whole press release | Where guidance, results explanations and new risks live. |
| Chunking | Paragraph-aware, ~2,400 chars (~600 tokens), ~300 chars (~12.5%) overlap, never crossing sections | Keeps related statements together; overlap avoids cutting a key sentence. |
| Contextual header | Each chunk is embedded with "TICKER FORM filed DATE, section X" prepended | Puts company and period into the vector itself, a second defense against wrong-company matches. |
| Embedding model | bge-m3 via Ollama (local, free, 8,192-token input) | Recommended in the handoff; Ollama avoids a PyTorch install. |
| Vector store | Chroma, persistent, cosine distance | Local, simple, metadata filters. |
| Retrieval | fetch 10, drop distance > 0.55 and near-duplicates, keep best 5 per query | Less noise for the agent; still room for supporting and opposing evidence. |
| Agent framework | LangGraph 1.2.17 | Explicit state machine with conditional loops, budgets and inspectable state. |
| LLM | Local `qwen3:14b` via Ollama now; Claude via the Anthropic SDK once the key is added (`.env`) | Free to develop; one-line switch. |
| UI stack | React frontend + FastAPI backend (not Streamlit) | User's decision, 2026-10-25. |
| Confidence | Computed from verification results | Handoff Section 4: never trust the model's self-reported confidence. |

## Known issues / notes

- **JPM 10-K count:** only one JPM 10-K was downloaded. The EDGAR submissions API's "recent" list holds the last 1,000 filings and JPM files many prospectus supplements, so its February 2025 10-K is outside the list. Fix later by also reading the older "files" pages if needed.
- **JPM MD&A** has no "Item 7" heading in the body (the 10-K cross-references it), so the parser uses a fallback: INTRODUCTION/EXECUTIVE OVERVIEW through the last FORWARD-LOOKING STATEMENTS heading.
- **JPM 10-Q Risk Factors** and **AAPL January 10-Q Risk Factors** are short pointers to the 10-K and are skipped.
- **Microsoft** prints a bare "Item 1A" / "Item 7" running header on every page. The parser now requires a real section title after "Item N" so these aren't mistaken for section starts. (Before the fix MSFT sections were ~5,000 characters; after, 47,000-81,000.)
- **Local LLM speed:** qwen3:14b on this CPU (Ryzen 7 PRO 6850U, 28 GB RAM) is slow; a full agent run takes minutes. Claude will be much faster and stronger.
- **SEC contact:** `.env` still has a placeholder contact email; replace it with yours.
- **Pinning the Checkpoint 3 set:** downloads keep growing. Use an explicit `--as-of` date in runs for the write-up so results are reproducible.

---

## Log

### 2026-10-25 (session 1): Project set up, filings downloaded

- Created the project in `Desktop\CMU\stock-research-agent` with config, `.venv` (Python 3.12), and a copy of the handoff document.
- Wrote `scripts/01_download_filings.py` (EDGAR submissions API, 8-Ks with Item 2.02, exhibit 99.1 via `index.json`, 5 requests/second).
- Bug fixed: JPM names its press release `...exhibit991narrative.htm`, so exhibit detection now uses a regex for `ex`/`exhibit` + `99` + digit and picks the lowest number.

### 2026-10-25 (session 2): Full pipeline, LangGraph agents, daily automation

- **Downloader made incremental** (merges into the manifest by accession number) and widened to about a year of history. Real result: 26 filings (AAPL 9, MSFT 9, JPM 8). A second run reported "0 new, 26 total".
- **Parser** (`02_parse_sections.py`): HTML to text, hidden inline XBRL removed, table rows kept as `a | b | c` lines. Two fixes after the first run (MSFT running headers, JPM MD&A fallback). Real section sizes (characters):

| Doc | Sections |
|---|---|
| AAPL 10-K 2025-10-31 | Risk Factors 68,057; MD&A 18,124 |
| AAPL 10-Q 2026-07-31 | MD&A 21,731; Risk Factors 19,081 |
| MSFT 10-K 2026-07-29 | Risk Factors 81,192; MD&A 51,556 |
| MSFT 10-Q 2026-04-29 | MD&A 58,908; Risk Factors 70,912 |
| JPM 10-K 2026-02-13 | Risk Factors 112,139; MD&A 391,613 |
| JPM 10-Q 2026-08-06 | MD&A 270,177 |
| 8-K press releases | 10,205-25,527 each |

- **Ingestion** (`03_ingest.py`, bge-m3 on CPU via Ollama): **1,214 chunks** in 2,431 s (~40 min; about 0.5 chunk/s). Average chunk 1,750-2,300 characters. Biggest: JPM 10-K 248 chunks, JPM 10-Qs 114-131 each, MSFT 10-K/10-Q 62-68 each, AAPL 10-K 44-46. Daily updates only embed new filings, so they take a few minutes at most.
- **Retrieval check:** `"Services net sales growth"`, ticker AAPL, as-of 2026-10-25: top hit was AAPL 10-Q (filed 2026-05-01) MD&A at cosine distance 0.402, the passage saying Services grew on advertising, App Store and cloud services.
- **Failure-mode demo** (`06_failure_demo.py`, saved `logs/failure_demo_20261025_180437.md`), real results:
  - *Wrong company:* researching AAPL, query "growth in cloud services revenue and investment in AI infrastructure and data centers". **Unfiltered: 5 of 5 results were Microsoft filings** (distances 0.338-0.360). With the ticker filter: 5 of 5 Apple (0.413-0.510).
  - *Look-ahead:* JPM, backtest dated 2026-05-15, query "net interest income in the most recent quarter compared with the prior year". **Unfiltered: 3 of 5 unusable** (JPM 8-K filed 2026-07-14 and JPM 10-Q filed 2026-08-06 are after the as-of date; one MSFT 10-Q). With ticker + as-of filters: 5 of 5 valid JPM filings dated on or before 2026-05-15.
  - *New finding (recency):* even with filters, "most recent quarter" ranked the Nov 2025 JPM 10-Q above the May 2026 one. Semantic similarity has no notion of "latest". Planned fix: recency-aware reranking, or the agent querying the newest filing first (filter on filing date).
- **LangGraph agents:** graph compiles (nodes plan, retrieve, assess, answer, verify, revise, finalize). First run with local qwen3:14b was started and **stopped at the user's request** (too slow on CPU). No agent output recorded yet. Agents switched to Claude (`LLM_PROVIDER=anthropic` in `.env`), waiting for the API key.
- **Scheduler:** Windows task `StockAgent-DailyFilingsUpdate` registered; first run 2026-10-25 9:00 PM. Note: the daily job still needs Ollama running for bge-m3 embeddings (no chat model is used).

### 2026-10-25 (session 3): First real agent run (Claude), Checkpoint 3 written

- **Comparison run** (`05_compare.py`, Claude Opus 5.5, as-of 2026-10-25), question: "What drove Apple's Services net sales growth in its most recent reported quarter, and what risks did Apple highlight in that quarter's filing?" Saved `logs/compare_20261025_190630_AAPL.md`, trace `logs/runs/20261025_190630_AAPL.json`.
  - *Without retrieval* (20 s): said it could not answer for the latest quarter; gave fiscal 2025 figures from memory (Q3 FY25 Services "about $27.4B, up about 13%") and generic 2025 risks; self-reported confidence 25%.
  - *With retrieval* (48 s): fiscal Q3 2026 Services $30,739M vs $27,423M (+12%), "primarily due to higher net sales from advertising and cloud services"; new risk in the Q3 2026 10-Q: component supply constraints and rising costs (advanced semiconductors, NAND, DRAM), expected to intensify. Figures checked against the parsed filing. Evidence strength "moderate".
  - *Trace:* 2 retrieval rounds, 5 queries (the assess step found risk factors missing and asked for 2 follow-ups), 12 evidence chunks (all AAPL), 12 claims, 10 kept (1 "partially supported"), 2 removed.
- **Bug found and fixed:** both removed claims were false rejections by the number check. It read "31" from the filing date 2026-07-31 and "10" from "10-Q" as figures missing from the chunk text. Fix in `research_graph.py`: the check now includes each cited chunk's filing date and period, and ignores form names (10-K, 10-Q, 8-K). Re-checked: both claims pass; an invented figure ("19% to $33.1 billion") is still rejected.
- **Checkpoint 3 write-up drafted:** Claude Doc "Capstone Checkpoint 3: Adding a Retrieval Layer to the Stock Research Agent" (https://claude.ai/artifact/NGyigM1843fhKymCZffXqZ, export to PDF/DOCX from there) plus local copy `docs/checkpoint3.md`. 891 words (limit 600-900). All five rubric items are covered, with real results only.

### 2026-11-02 (session 4): Checkpoint 4 (Tree-of-Thought design) drafted

- Assignment: design-only checkpoint on ToT reasoning (decide if ToT fits; if so, placement, tree structure, search strategy, evaluation/pruning, role-to-tool mapping across CrewAI / LangChain / MCP, updated architecture diagram, one ToT failure mode + mitigation).
- **Design decided:** ToT only in a new **thesis stage** between the researcher and the forecaster; everything else stays linear. Thought = one candidate thesis (driver + bull/bear direction + 90-day mechanism). Depth 3 (generate 6 → test, 2 children each → quantify), beam search width 3 with a rule that the beam always keeps at least one bull and one bear branch. About 12 LLM calls per stock; cap 15 calls and 3 minutes. Hard code checks (citation, ticker, as-of date, numbers; reused verifier) prune; weighted 0-10 critic score ranks (evidence support 40%, horizon 20%, materiality 20%, consistency with quant signals 10%, novelty 10%); prune below 5. Decision maker sums surviving leaves' adjustments, caps at ±15 points, shrinks toward 50%, labels "contested" when best bull and best bear both score 7+.
- **Tools:** CrewAI = bull/bear thought generators; LangChain LCEL = critic; LangGraph = beam controller; MCP servers (filings retrieval, market data, predictions memory) + LangGraph SQLite checkpointer = state and branch log. None of the ToT parts are built yet.
- **Risk:** weak evaluation signals prune the best (new, thinly evidenced) branch; mitigation: share-based evidence score, prune only on verifiable failures, keep best bull + best bear, log pruned branches and have the reflector audit them.
- **Deliverables:** Claude Doc https://claude.ai/artifact/AE3Qx7bqTY1jw4VathTKEe (with drawn architecture diagram), `docs/checkpoint4.md`, `docs/Capstone_Checkpoint_4_Stock_Research_Agent.pdf` (6 pages). Suggested length is 750-1,100 words: first draft was 1,516, trimmed to **926** (312 in tables; ~1,015 if the diagram's labels count). Trim also added latency and complexity columns to the search-strategy table so all four strategies are compared on compute, latency and complexity. All seven rubric items re-checked as present.
- Roadmap impact: the ToT thesis stage becomes part of the agent build (between researcher and forecaster); see next step 5b.

### 2026-11-08 (session 5): Multi-agent architecture checkpoint drafted

- Design: nine agents (screener, researcher, bull analyst, bear analyst, critic, forecaster, ranker/reporter, sell monitor, reflector); math in deterministic tools, not agents. Hybrid graph coordination in one LangGraph controller: sequential spine, per-stock parallel fan-out, bull/bear/critic debate loop (the Checkpoint 4 ToT beam, max 2 send-backs to the researcher), scheduled sell-monitor and reflector branches. Communication through a shared typed state with evidence IDs; one-way handoffs, two-way critic validation, brainstorming only in the debate, long-term memory for lessons.
- Deliverables: Claude Doc https://claude.ai/artifact/1MUV56RPvocpk6d5UUgxSv, `docs/checkpoint_multi_agent.md`, `docs/Capstone_Checkpoint_5_Stock_Research_Agent.pdf` (3 pages). 565 words in the body (578 with title), within 400-600.

### 2026-11-15 (session 6): Checkpoint 6 (safety, evaluation, human oversight) drafted

- Risks: hallucination, wrong-company/future evidence, overconfidence, output taken as advice, bad or injected inputs, runaway cost. Guardrails by layer (input, retrieval, output, forecast, tools, runtime, presentation). Metrics with initial targets: groundedness 95%+, zero wrong/future passages, Brier below 0.25 and calibration gap within 5 points, fallback rate (investigate above 15%), escalation about 10% of picks, latency/cost within budget. Human-intervention triggers: any real-money trade, contested picks, probability outside 35-65%, missing data/parse failure/budget exhausted, suspected prompt injection, new reflector rules, calibration gap above 10 points over 30+ resolved predictions, sell-monitor flags.
- Deliverables: Claude Doc https://claude.ai/artifact/DpymF4MHSccdfFncWN1dDG, `docs/checkpoint6.md`, `docs/Capstone_Checkpoint_6_Stock_Research_Agent.pdf`. Suggested length 400-600 words: first draft 787, condensed to 538 in the body (555 with title), all five rubric items kept.

### 2026-11-15 (session 7): Implementation started: structured market database and tools

- **Universe:** added NVDA, AMZN, GOOGL, META, TSLA (CIKs from SEC company_tickers.json) to AAPL, MSFT, JPM. Benchmark SPY (never a pick).
- **Structured DB** (`stockagent/db.py`, SQLite `data/market.db`): companies, prices_daily (adjusted closes from Yahoo Finance via yfinance, since 2023-01-01), financials (SEC XBRL company facts, long format, every value keeps its `filed` date for as-of queries; period types Q / FY / I), earnings_events (EPS estimate vs reported). Chose SQLite over SQL Server: single user, nightly batch writes, zero setup, data rebuildable; switch to PostgreSQL/SQL Server if the FastAPI backend needs concurrent writers.
- **Loader** `scripts/07_market_data.py` (incremental). Real result: 9 companies, 8,739 price rows, 9,924 financial facts, 200 earnings events. Added to the nightly job (`update_daily.py`).
- **Tools** `stockagent/market.py`, all as-of: price_on, total_return, excess_return (vs SPY), momentum (182 calendar days), volatility, latest_fundamental (filed on or before as-of), pe_ratio, earnings_surprise, brier_score, calibration_table. `tests/test_market.py`: 6 tests pass (as-of logic including a restated filing, excess return, Brier, calibration buckets).
- **Bugs found and fixed:** (1) XBRL tags change over time (Apple's revenue stopped in 2018 under `Revenues`; now all listed tags are merged with priority); banks report `RevenuesNetOfInterestExpense` (added). (2) momentum default was 126 calendar days (~4 months), now 182. (3) NVIDIA names its press release `q2fy27pr.htm`; the downloader now falls back to the filing index page's official `EX-99.1` type.
- **Known gaps:** JPM long-term debt stops in 2014 (bank reporting); AMZN has no separate total liabilities tag. Tesla 10-Q Risk Factors are short pointers to the 10-K (skipped).
- **Filings:** 76 documents now (50 new for the five added companies, plus 5 picked up by the nightly task since Oct 25). Indexing finished: 1,911 new chunks in 8,846 s (~2.5 hours on CPU); the index now holds 3,398 chunks across all 8 companies.
- **Note on EPS surprise data:** Yahoo's "reported EPS" is GAAP diluted EPS (matches SEC exactly) while estimates are adjusted, so quarters with one-off items show huge "surprises" (GOOGL Q2 2026 +214%, Q3 -29.5%). The screener must not use the raw surprise blindly.

### 2026-11-15 (session 7, continued): First UI screen (React + FastAPI)

- **Backend** `api/main.py` (FastAPI): `GET /api/companies` (universe with as-of price, 6-month return, volatility, P/E, last EPS surprise), `POST /api/research` (runs the LangGraph research agent; validates ticker and date), `GET /api/health`.
- **Frontend** `web/` (React 19 + Vite + TypeScript, plain CSS): companies table (click to select) and a research panel (question, optional as-of date, cited report with evidence strength, claims with evidence IDs, gaps, critic risks, removed claims, sources linked to SEC, disclaimer). `npm run build` passes type-checking.
- **Run it:** `.venv\Scripts\python -m uvicorn api.main:app --port 8000` (from the project root) and `npm run dev` in `web/`, then open http://localhost:5173. In the Claude app, `.claude/launch.json` in the CMU folder starts both (`api`, `web`).
- **Verified in the browser:** companies table loads all 8 companies with real metrics. A research run from the UI (AAPL, "What drove Services revenue in its latest fiscal year, and what new risks?") returned a cited report (`logs/runs/20261115_170824_AAPL.md`, evidence "moderate") built on Apple's FY2026 10-K filed 2026-11-03, which the nightly job had downloaded on its own; the headline figure ($123,301M Services) matches the parsed filing. The run took several minutes because filing indexing was running at the same time and both share Ollama. Table now scrolls inside its own box on narrow screens.

### 2026-11-15 (session 7, continued): Screener agent

- **Universe:** current S&P 500 members from Wikipedia's list (503 symbols incl. share classes), refreshed every run into a `universe` table (ticker, name, GICS sector, CIK, date added). Prices for all members bulk-loaded from Yahoo since 2025-01-01 (first backfill: 234,996 rows in ~3 minutes; daily runs add ~500 rows in under 30 seconds).
- **Design (`stockagent/screener.py`, deterministic code, no LLM):** Stage A on all ~500 names, prices only: 6-month return vs SPY, plus discovery triggers (5-day move above 2.5 standard deviations; 5-day volume above 3x the prior 60-day average). Stage B on the best 50 by momentum + all triggered + the 8 core names: fundamentals and earnings loaded on demand (cached 7 days), then earnings yield (1/P/E) and the latest earnings surprise (ignored if older than 120 days or above 50%, since those mix GAAP with adjusted EPS); recent surprise of 10%+ within 10 days is a trigger. Score = mean of available percentile ranks (equal weights, untuned starting point, needs 2+ signals). Shortlist = top 10 by score + triggered names. Names with too little price history (new listings, spin-offs) always go to human review, even when triggered (Checkpoint 6 rule).
- **Runner** `scripts/08_screen.py`, results saved in `screener_results`; added to the nightly job after market data. Shared loaders moved to `stockagent/loaders.py`.
- **API/UI:** `GET /api/screener`; UI shows "Today's shortlist" above the companies table. Only the 8 core names are clickable for research (others have no indexed filings yet).
- **Real result (as of 2026-11-13):** 503 scanned, 64 candidates, 19 shortlisted (10 by score: CDW, DLTR, BBY, PSX, HPQ, WSM, NTAP, FDS, BAX, SWK; 9 by trigger, e.g. AMGN/HUM/CAH/ABNB/ANET earnings beats, NVDA 4.0x volume, AVGO +18.2% in 5 days), 3 for human review (FDXF FedEx Freight, HONA Honeywell Aerospace, VYLR Vylor: recent listings/spin-offs).
- **Bugs found and fixed:** a spin-off (FDXF) had enough trading days but no price 6 months back, which crashed the run; now it goes to human review. A triggered thin-history name (VYLR) was auto-shortlisted; review now wins over triggers. Tests: 10 pass (4 screener tests).
- **Known limitations:** universe is today's S&P 500 membership, so backtests have survivorship bias until membership history is added; weights and thresholds are untuned; in earnings season triggers can roughly double the shortlist (19 names today), which raises research cost; shortlisted names outside the 8 core companies can't be researched until their filings are downloaded and indexed on demand (next step).

### 2026-11-16 (session 8): On-demand filings for shortlisted companies

- **What:** any S&P 500 company can now be researched once its filings are prepared (downloaded, parsed, indexed). Non-core companies use a lighter profile (`config.FILINGS_ON_DEMAND`: latest 10-K, latest 10-Q, 2 earnings releases).
- **How it's triggered:** (1) "Prepare filings" button per shortlist row in the UI (`POST /api/filings/{ticker}`, runs in a background thread; `GET` returns queued / running / ready / failed); (2) the nightly job prepares the top `ON_DEMAND_NIGHTLY` = 3 shortlisted names by score after screening. Prepared companies are then included in later nightly filing updates.
- **Code:** `stockagent/ondemand.py` (status table `filing_status`, runner, nightly candidates), `01_download_filings.py --tickers` (CIK from the universe table; default = core + prepared), research endpoint now accepts any company whose filings are ready. A file lock (`data/pipeline.lock`, stale after 4 hours) ensures only one pipeline run writes to the index at a time; the nightly job takes the same lock. A status stuck in running/queued for over 4 hours (crashed server) can be retried. Tests: 12 pass (2 lock tests).

- **Verified end to end:** "Prepare filings" for CDW from the UI → ready in ~8 minutes (119 chunks); CDW then researchable from the UI.

### 2026-11-16 (session 8, continued): Forecaster

- **Output:** probability that a stock's total return beats SPY's over the next 90 days.
- **Quantitative model** (`stockagent/features.py`, `stockagent/forecast_model.py`, `scripts/09_train_forecaster.py`): logistic regression (numpy, L2) on 3 price features (6-month and 1-month return vs SPY, 3-month volatility); training and live forecasts share one feature function. Samples: every universe stock on the 15th of each month, 2025-07-15 .. 2026-08-15 (14 dates). Time split: 7 train dates, 90-day gap, 4 holdout dates; Platt calibration cross-fitted on the holdout.
- **Real result (honest):** holdout Brier: always 50% = 0.2500, train base rate = 0.2509, model uncalibrated = **0.2668 (worse)**, calibrated = 0.2486 only by flipping the sign. Momentum worked in the training period (coefficient +0.27) and reversed in the holdout period (calibration slope -1.07). Decision: never trust a negative calibration slope; it is set to 0, so the model falls back to the observed base rate (about 50.4%). Price signals show no stable skill in this data, matching the handoff's "markets are hard to beat". Caveats: only ~1.5 years of universe history, overlapping horizons, current-membership universe (survivorship bias).
- **Forecaster** (`stockagent/forecaster.py`): p_final = p_quant + shrink_weight × evidence adjustment. Adjustments are proposed by the LLM from the research report's verified claims; code keeps only those citing real verified claims (partially supported counts half); small = 2 points, moderate = 4; total capped at ±15 (`MAX_EVIDENCE_ADJUSTMENT`). shrink_weight = 0.5 + 0.5·n/(n+100), n = resolved predictions. Forecasts outside 35-65% are flagged for human review.
- **Long-term memory:** `predictions` table; `resolve_open()` scores predictions whose 90 days have passed (run nightly).
- **API/UI:** `POST /api/forecast` (optionally with a research trace); "Forecast probability from this research" button and a forecast card showing model, evidence adjustment, share applied, final probability, cited adjustments and the no-skill note.
- **Real first forecast (#1, CDW, 2026-11-16):** model 50.3%, evidence −2.0 points (margin pressure: gross margin −140 bp, Government −330 bp; AI-driven memory costs), applied at 50% → **49.3%**. Stored, open, resolves around 2027-02-14.
- **Tests:** 16 pass (4 forecaster tests: features/labels respect dates, model learns a real signal and rejects a reversed one, adjustments must cite real claims and are capped, predictions are stored and resolved).

### 2026-11-16 (session 8, continued): Bull and bear agents (Checkpoint 4 thesis stage)

- **Graph** (`stockagent/debate_graph.py`, LangGraph): bull and bear run in parallel from START (neither sees the other's drafts) → `check` (code: cited evidence IDs must exist, every number must appear in the cited text) → `score` (critic LLM: evidence support, 90-day relevance, materiality, 0-10; code adds consistency with 6-month momentum and novelty; weights 40/20/20/10/10) → `prune` (below 5 dropped, best 3 kept, best bull and best bear always kept) → `rebut` (each survivor gets the other side's strongest counter-argument) → `rescore` (critic re-rates with the rebuttal) → `decide` (contested if best bull and best bear both ≥ 7; otherwise leaning bull/bear). About 6 LLM calls; uses the evidence already gathered by the research run.
- **Forecaster integration:** with a debate, each surviving thesis scoring ≥ 8 adds/subtracts 4 points, ≥ 6 adds/subtracts 2 (bull up, bear down); contested halves each; same ±15 cap and shrink weight. UI button is now "Debate and forecast"; the forecast card shows bull vs bear side by side with scores before/after rebuttal, rebuttals, protected theses, and pruned/failed counts.
- **Real run (NVDA, 2026-11-16):** 3 bull + 3 bear theses; 2 failed checks; survivors BULL2 6.5 (broadening demand beyond hyperscalers), BULL3 6.3 (profitability and cash generation), BEAR2 6.3 (growth decelerating, margins slipping at the guide; was 7.1 before the bull's rebuttal); BEAR3 pruned (6.1). Leaning bull, not contested. **Forecast #2: 51.3%** (50.3% + 50% × 2.0 points).
- **Bug found and fixed:** both failed checks were false: the number check read "90" from "over the next 90 days" (the forecast horizon the analysts were asked about). The horizon phrase is now ignored before the check; invented figures are still caught.
- **Not yet built from the design:** the critic cannot send a thesis back to the researcher for new evidence (debate uses only the research run's evidence); no level-3 quantification per thesis (strength comes from the score mapping). Tests: 19 pass (3 debate tests: checks, scoring, contested rule, side protection, independence of drafts, forecaster mapping).

### 2026-11-17 (session 9): Ranker / reporter and sell monitor

- **Ranker** (`stockagent/ranker.py`): a daily run takes the latest shortlist's researchable names (best screener score first, up to `DAILY_PICKS_LIMIT` = 5), runs research (standard question `DAILY_QUESTION`) → bull/bear debate → forecast for each, ranks by probability; picks outside 35-65% go to a separate human-review section; failures are listed, never dropped silently. The report is assembled in code from verified outputs (thesis = best surviving thesis on the leaning side, main risk = best opposing thesis + critic-flagged risks, sources = cited filings): no new LLM text. Saved in `daily_runs` / `daily_picks` and `logs/daily/<date>_run<id>.md`. Started with the "Run today's picks" button (`POST /api/picks/run`), not nightly, because it spends LLM credit (~12-15 calls per stock).
- **Nightly job, unattended (2026-11-16 evening):** screened, then auto-prepared filings for REGN (309 chunks), BBY (90), DLTR (122).
- **Sell monitor** (`stockagent/sell_monitor.py`), paper positions only: each ranked pick above 50% opens a paper position at the as-of close (`positions` table, with the entry thesis). Checks per open position: stop-loss (trailing SPY by 10+ points since entry), horizon reached (90 days), new filing since entry; with the thesis check, the LLM judges the entry thesis against passages from filings after entry (intact / weakened / broken) and code rejects a verdict that cites no new passage. Flags only recommend review; closing is a user action ("Close (paper)" button with confirmation; `POST /api/positions/{id}/close`). Nightly job runs the free checks; the LLM thesis check runs from "Review open positions".
- **Tests:** 26 pass (3 ranker, 4 sell monitor).
- **First real daily run (run 1, 2026-11-17):** 5 picks, 0 failures, ~13 minutes: 1. REGN 51.3% (bull: Sanofi/Dupixent collaboration revenue +68%; risk: concentration, EYLEA erosion), 2. NVDA 51.3%, 3. CDW 50.8% (contested), 4. DLTR 50.8% (contested: guidance raise vs one-time tariff refunds), 5. BBY 50.3% (bull and bear cancelled out). Report: `logs/daily/2026-11-17_run1.md`.
- **Paper positions:** fixed the opening rule to require p_final above the model's base rate as well as 50% (BBY at 50.3% = base rate, no edge). 4 opened (REGN, NVDA, CDW, DLTR), horizons end 2027-02-15. First monitor run: no flags, no new filings since entry.

### 2026-11-17 (session 9, continued): Reflector (Checkpoint 2 self-correction loop)

- **Data to learn from now:** `scripts/10_backtest.py` replays the price model on every universe stock on the 15th of each past month (6,989 resolved predictions, 2025-07-15 .. 2026-08-15), stored with `source='backtest'`. Live predictions now store a `context` (sector, momentum/volatility bucket, debate lean, contested, evidence direction) and any applied rule adjustment (DB migration adds the columns to existing databases). The shrink weight for LLM adjustments counts only live resolved predictions.
- **Reflector** (`stockagent/reflector.py`, no LLM): calibration report (Brier vs always-50% and base rate, buckets), segment analysis, rule proposals with safeguards: 100+ cases in both time halves, learned on the older half and kept only if it lowers Brier on the newer half, shrunk and capped at ±5 points, human approval required (proposed → active/rejected; active → retired), active rules re-tested each reflection and flagged when they stop helping; pruned-thesis audit for resolved live debates. Approved rules are applied by the forecaster and shown in the forecast breakdown. Nightly job regenerates the backtest and saves proposals; the UI view is read-only except for Approve / Reject / Retire.
- **Real findings:** in the backtest only **45.3%** of S&P 500 stocks beat the index over 90 days (cap-weighted index led by a few giants), while the model says 50.3%: Brier 0.2503 vs 0.2500 (always 50%) vs 0.2478 (base rate). Biggest segment misses: Real Estate 35.7% actual, Communication Services 36.0%, Consumer Discretionary 36.5%, Utilities 38.5%, Energy 59.2%. **3 rules proposed** (each −5 points: Communication Services, Real Estate, Consumer Discretionary; held-out Brier improved, e.g. 0.25048 → 0.24890), **4 candidates rejected** out of sample. An overall "all forecasts" correction was not proposed: the gap was only about −3 points in the older half. All 3 approved by the user on 2026-11-17 and now active: future forecasts for those sectors get −5 points.
- **Caveats:** the model's calibration was fitted on part of the backtest period; current-membership universe (survivorship bias); backtests cover only the price layer (no research/debate).
- **Tests:** 30 pass (4 reflector tests: out-of-sample rejection of a flipping effect, minimum cases, overall-bias rule, proposal saved once and applied only after approval).

### 2026-11-17 (session 9, continued): Critic send-back to the researcher

- **What:** in the debate graph, after scoring, a thesis the critic rates as promising but thinly supported (evidence support ≤ 6 while relevance and materiality average ≥ 6) and for which it names the missing evidence (new `missing_evidence_query` field) is sent back: the researcher's retrieval runs that query with the same ticker and as-of filters, new passages join the evidence (new E-IDs) and are attached to the thesis, and the critic re-scores only those theses. Up to 2 queries per round and 2 rounds (`DEBATE_SEND_BACK_ROUNDS`, Checkpoint 5); a query is never repeated. New evidence can raise or lower a score; the score before send-back is kept for display. Debate results now carry their evidence so pick sources include send-back passages; the UI shows each send-back under its thesis and a count in the debate summary.
- **Tests:** 32 pass (send-back fetches with the right filters, attaches the passage and re-scores 7.2 → 8.4; weak-and-irrelevant theses are never sent back; the loop stops at 2 rounds even if the critic keeps asking).
- **Real check (DLTR debate re-run on today's research):** the critic named missing evidence for 3 theses (Q3 traffic trend; remaining buyback authorization; H2 buyback pace), but none met the thin-but-promising rule (evidence 7.0/6.0/7.0 with relevance+materiality averages 6.0/4.5/5.0), so no searches ran, as designed. The critic suggests queries readily; the evidence ≤ 6 threshold is what limits extra cost.

### 2026-11-17 (session 9, continued): Fix: novelty lost on partial re-scores

- **Bug:** novelty (10% of a thesis score) compares a thesis with same-side theses rated higher on evidence, but `_apply_scores` only saw the theses passed in. After a send-back (only the sent-back theses re-scored) and in `rescore` (only rebutted survivors), a near-duplicate had nothing to compare against and got novelty 10 instead of 0: up to +1 point, enough to cross the 7 "contested" or 8 "4-point" bars.
- **Fix** (`stockagent/debate_graph.py`): each scored thesis now stores the critic's raw ratings (`ratings`). `score` and `rescore` re-weight the whole pool, using new ratings for re-rated theses and stored ratings for the rest, so novelty always sees every same-side thesis. `score_before_send_back` and `first_score` behave as before.
- **Tests:** 33 pass. New `test_novelty_survives_partial_rescore`: a near-duplicate re-rated alone after a send-back scores 7.0 with novelty 0 (8.0 with the bug), and stays at novelty 0 after a rebuttal re-score.

### 2026-11-17 (session 9, continued): New sources: event 8-Ks, Form 4, FRED macro, trusted news

- **Event 8-Ks** (`01_download_filings.py`, `02_parse_sections.py`): material-event 8-Ks from the last 365 days (`config.EVENT_8K_ITEMS`: 1.01-1.03, 2.01, 2.03-2.06, 3.01, 4.01, 4.02, 5.01, 5.02, 7.01, 8.01; up to 8 per core company, 3 on demand). Body text (cover page and signatures cut) plus exhibit 99.1, one section named after the items ("Event on 2026-11-11: 5.02 Executive or director change"). First run: 69 event 8-Ks, 252 chunks (index now 4,719).
- **Form 4** (`scripts/11_insider_trades.py`, tables `insider_filings`, `insider_trades`): common-stock transactions per filing (owner, role, code, shares, price, Rule 10b5-1 flag), 180-day lookback, incremental. First run: 15 companies, ~1,200 transactions (MSFT and GOOGL hit the 80-filing cap; the rest come on later runs).
- **FRED** (`scripts/12_macro.py`, table `macro_series`): 10y and 2y Treasury, fed funds, high-yield spread, VIX, unemployment, CPI; keyless CSV endpoint. Monthly series carry a publication lag (CPI 45 days, unemployment 40) so an as-of date never sees a figure before it was published. FRED serves the latest vintage (small revision look-ahead in backtests). FRED stalls on custom User-Agent strings, so the script uses the default one.
- **News** (`scripts/13_news.py`, table `news_items`): Finnhub company news, kept only if the publisher is on `config.NEWS_TRUSTED_SOURCES` (Reuters, Bloomberg, WSJ/Dow Jones, FT, CNBC, MarketWatch, Barron's, AP, the press-release wires); headline + summary + link only, indexed in Chroma by publish date. **Needs `FINNHUB_API_KEY` in `.env`; until then the step is skipped.**
- **How agents use them:** the research graph adds a Form 4 summary and a FRED snapshot as citable evidence (`stockagent/context.py`, numbers checked like any filing); event 8-Ks and news come through retrieval with the same ticker and as-of filters. At most 2 news items per retrieval. News is labelled as a third-party report; "strong" evidence needs claims backed by filings or Form 4, not news or macro alone; in the debate a thesis citing only news/macro has evidence support capped at 6. Sell-monitor thesis checks now see event 8-Ks and news filed after entry.
- **Nightly job:** 01, 02, 13, 03, 07, 08, 10, 11, 12; 11-13 are optional (a failure is logged, the run continues). On-demand preparation also runs 13 and 11.
- **Real check (NVDA research, as of 2026-11-17):** 21 claims, 0 dropped. It found the 8-K filed 2026-11-12: Jen-Hsun Huang resigned as CEO on 2026-11-11, CFO Colette Kress interim CEO. Today's daily run (before this change) ranked NVDA second and opened a paper position without seeing it. Also cited: insider selling (25 sales by 3 insiders, ~$968 million, no purchases), the SB Energy / OpenAI residual value guaranties (capped at $105 billion), and the macro snapshot.
- **Sell-monitor fix (2026-11-18):** the thesis check judged only passages *filed after entry*, so it missed the NVDA CEO 8-K (filed 2026-11-12, entry 2026-11-17) that the pick never saw. It now reviews passages the pick did not use: filed after entry, or filed in the 90 days before entry (`THESIS_UNSEEN_DAYS`) and absent from the pick's research/debate traces. Unseen material-event 8-Ks are always included (up to 4), since a CEO exit reads nothing like a revenue thesis and similarity search would miss it. Passages are labelled "filed after entry" / "filed before entry, not seen by the pick". The paid check now runs whenever requested and makes no LLM call when nothing is unseen. Real run: **NVDA weakened** (CEO resignation; Q3 still beat: $115.8B vs the $108.0B guide), **CDW weakened** (growth from lower-margin hardware, operating margin 7.7% → 7.2%, $1.5B new notes), DLTR and REGN intact. No positions closed (human decision). 40 tests pass.
- **Tests:** 39 pass (`tests/test_sources.py`: event selection by item and lookback, Form 4 parsing, insider summary respects as-of, macro waits for publication, news trusted filter and de-duplication, news-only cap in the debate).

### 2026-11-18/19 (session 9, continued): Parser fixes, index rebuild, re-run picks

- **Parser fixes:** REGN earnings 8-K has no exhibit 99, so the 8-K body is used ("Earnings (8-K text)"). BBY exhibit named `bby-fy27q2xexx99.htm` (plain EX-99) is now matched. WSM 10-Q printed "PART I" only in the table of contents, so the 10-Q part is now inferred from the item title. Amazon event 8-Ks had a table of contents with its own items and signatures line; the longest item-to-signatures span is kept.
- **News fetch:** Finnhub returns at most 250 items per call, so news is fetched one day per call, with retries on rate limits and dropped connections; `--since` added.
- **Chroma corruption:** a manual `03_ingest` run outside the pipeline lock overlapped the nightly ingest and was killed mid-write; every query segfaulted. The broken index was kept as `chroma_db_broken_20261118` (deleted 2026-11-21) and rebuilt from `data/parsed` under the lock: 567 documents, 5,533 chunks, 7 hours on CPU (bge-m3, about 13 chunks/minute). Manual pipeline steps must hold `pipeline_lock`. The lock now clears itself when its owner process has died.
- **Re-run picks (as of 2026-11-18, run 2):** REGN 52.3%, HPQ 50.3%, DLTR 49.3%, CDW 49.3%, BBY 46.3%. NVDA left the screen shortlist. FRED and Form 4 evidence now shape REGN's main risk and DLTR's case. The quant model gave all five the same 50.3%, so only the debate separates them.
- **Fix: duplicate paper positions.** `open_from_run` stopped duplicates only within a run (`UNIQUE (ticker, opened)`), so run 2 opened a second REGN position. It now skips a stock with an open position; the duplicate was deleted. 44 tests pass.

### 2026-11-19 (session 9, continued): Forecast model retrained on 10 years

- **Why every stock got 50.3%:** universe prices started in 2025-01, so the model trained on 7 monthly dates and was checked on 4. The signal reversed on the holdout, calibration slope fell to 0 and every forecast collapsed to the base rate (by design).
- **Data:** full adjusted price history from 2015 re-downloaded for all 504 tickers (one adjustment basis; `PRICE_HISTORY_START` and `UNIVERSE_HISTORY_START` = 2015-01-01). SEC XBRL fundamentals fetched for all 503 universe companies (was 76).
- **What was tested (walk-forward, yearly refit, 2018-2026, ~44,600 out-of-sample forecasts):** current price features (6-month and 1-month excess return, 3-month volatility); plus 12-1 momentum; plus 52-week-high ratio; plus point-in-time fundamentals (EPS change vs a year ago over price, revenue growth, earnings yield, accruals). None beat the price features: ranking skill (AUC within a date) about 0.52-0.53 for every set; Brier equal to the base rate. Fundamentals were not adopted.
- **Model changes** (`features.py`, `forecast_model.py`, `09`, `10`, `forecaster.py`):
  - Samples include a stock only on dates it was in the S&P 500 (`date_added`). Removed members are still missing (survivorship bias).
  - Features are measured against that date's median S&P 500 stock (`relative_to_date`; live forecasts use `universe_medians`). The model's market-wide level showed no skill (correlation 0.14 with each date's actual base rate) and on 2026-11-18 sat far outside its historical range, so the model now only ranks.
  - Walk-forward evaluation (`walk_forward`, `config.FORECAST_FIRST_TEST_YEAR = 2018`); calibration fitted on the out-of-sample forecasts (a = 0.91), Brier cross-fitted by year.
  - Backtest rows (`10_backtest.py`) are now the walk-forward forecasts, 44,660 rows, 2018-01 to 2026-08 (was 5,492 in-sample rows).
- **Result:** Brier 0.2494 vs base rate 0.2493 (no probability skill overall); AUC 0.521; top fifth beat SPY 50.7% (mean excess +1.35%), bottom fifth 46.2% (-0.62%). Calibrated within 1-3 points from 40% to 55%; above 55% too few cases to trust. Skill varies by year (AUC 0.47 in 2018, 0.55 in 2021 and 2025). On 2026-11-18 forecasts range about 42-56% (REGN 43.7%, HPQ 49.2%, NVDA 52.1%).
- **Reflector rules: self-correction from new evidence (demo example).** On 2026-11-17 the reflector learned 3 sector rules (-5 points each) from 16 months of backtests (154-329 cases per sector), and they were approved. With the 8.5-year backtest, the nightly loop ran again:
  1. *Learn and test:* no new rule passed the out-of-sample test (2 candidates rejected).
  2. *Re-check active rules:* the re-test flagged Communication Services: "no longer improves recent Brier; consider retiring".
  3. *Look closer:* each rule on both halves of the data (mean forecast vs share that beat SPY):

     | Rule (-5 points) | 2018-01 to 2022-08 | 2022-08 to 2026-08 | Decision |
     |---|---|---|---|
     | Communication Services | 48.1% vs 45.7% (912) | 47.0% vs 45.6% (908) | Retired: -5 never improved Brier; real gap about -2 |
     | Real Estate | 47.5% vs 53.3% (1,449) | 46.2% vs 36.1% (1,453) | Kept: -5 improves recent Brier; gap flipped in the high-rate era, so re-check if rates fall |
     | Consumer Discretionary | 48.5% vs 49.7% (2,168) | 47.4% vs 44.6% (2,130) | Retired: oversized; -5 worsens Brier over the full period |

  4. *Human decision (Checkpoint 6):* the user retired rules 1 and 3 and kept rule 2 on 2026-11-19 through `reflector.decide_rule` (the UI's approval path). Each rule's `note` records the evidence. Effect: stocks in Communication Services and Consumer Discretionary (e.g. BBY) no longer lose 5 points; Real Estate still does.
- **Takeaway:** a rule learned on a short window can capture a passing regime. The system flags rules that stop helping, but keeps a person in the loop, because the same evidence can support different calls (Real Estate: wrong for 2018-2022, right since).
- **Tests:** 47 pass (walk-forward never trains on outcomes not yet known; features centered on the date median; live forecast centered on the universe median).

### 2026-11-19 (session 9, continued): Reranker and recency-aware retrieval

- **Problem:** retrieval only filtered vector-distance order (it never reordered), and similarity has no notion of "latest": for JPM as of 2026-05-15, "net interest income in the most recent quarter" ranked the Nov 2025 10-Q above the May 2026 one.
- **Recency** (`retriever.latest_doc_ids`): with a ticker, the newest 10-Q/10-K and the newest earnings release filed on or before the as-of date are looked up from metadata, and their 8 nearest passages (`RETRIEVE_LATEST_K`) always join the candidate pool. Ordering subtracts `RECENCY_PENALTY_PER_YEAR` = 1.0 reranker point per year of filing age, so an older passage must be clearly more relevant to outrank a newer one.
- **Reranker** (`retriever.rerank_scores`): local ONNX cross-encoder `Xenova/ms-marco-MiniLM-L-12-v2` via `fastembed` (0.12 GB, cached in `data/models/fastembed`) re-scores about 30-38 candidates (`RETRIEVE_FETCH_K` 10 -> 30). The distance cutoff, news cap, near-duplicate check and final 5 now apply in reranked order. `bge-reranker-v2-m3` (int8) was tried: 30-70 s per search on this CPU, too slow for daily runs.
- **Check on real queries:** JPM as of 2026-05-15 now returns the Feb 2026 10-K and May 2026 10-Q first (the Nov 2025 10-Q fifth). AAPL "services revenue growth drivers latest quarter" returns the Q4 earnings release (2026-11-02) first. NVDA "CEO resignation" returns the 5.02 8-K first, then the CNBC reports. Cost: about 6-10 s per search instead of about 2 s (about +1 minute per stock in a daily run).
- **Trace:** each candidate in `logs/retrieval_trace.jsonl` now records its reranker score, final score and whether it came from the newest filing.
- **Not done (then):** repeated boilerplate across filings could fill two slots (fixed 2026-11-20).
- **Tests:** 49 pass (`tests/test_retriever.py`: the newest 10-Q joins the pool and wins a near-tie; a clearly more relevant older passage still wins).

### 2026-11-19 (session 9, continued): Retrieval test set

- **Test set** (`eval/retrieval_cases.json`): 18 questions over 11 companies and several as-of dates: 13 "latest quarter" questions (JPM, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, REGN, BBY), 2 material events (NVDA CEO exit, AMZN notes), 3 topics (DLTR tariff refunds, MSFT AI risk, AAPL supply risk). Each names the passages that answer it by rule (which filings or section, plus text that must appear), so relevance never depends on the retriever. The script stops if a case has no answering passage in the index.
- **Scoring** (`scripts/14_eval_retrieval.py`, read-only, about 7 minutes): hit@5, MRR and "first passage answers", for all cases and for the latest-quarter cases; results in `logs/retrieval_eval.json`.

  | Configuration | hit@5 | MRR | first | latest: hit@5 | latest: first |
  |---|---|---|---|---|---|
  | Vector order only (before 2026-11-19) | 0.89 | 0.59 | 0.44 | 0.85 | 0.31 |
  | Reranker, no recency | 0.78 | 0.71 | 0.67 | 0.69 | 0.54 |
  | Reranker + recency, penalty 1.0/year | 0.89 | 0.75 | 0.67 | 0.85 | 0.54 |
  | **Reranker + recency, penalty 2.0/year (now)** | **0.94** | **0.80** | **0.72** | **0.92** | **0.62** |
  | Penalty 4.0/year | 0.94 | 0.81 | 0.72 | 0.92 | 0.62 |

- **Findings:** the reranker alone puts the right passage first more often (0.44 -> 0.67) but loses latest-quarter hits (0.85 -> 0.69), because it cannot tell which quarter is newest; recency restores them. `RECENCY_PENALTY_PER_YEAR` set to 2.0 (4.0 adds nothing). The distance cutoff (0.50, 0.55, 0.60, 0.65) changed no result; kept at 0.55.
- **Remaining misses:** AAPL services as of 2026-11-18 (the newest filing is the annual 10-K; the reranker prefers 10-Q passages that say "quarter"); MSFT Azure ranks 5th behind an 8-K that recast segment figures.
- **Caveat:** 18 cases, and the penalty was tuned on the same cases; one case moves a metric by about 0.06. Add cases when a retrieval failure shows up in a real run.

### 2026-11-20 (session 9, continued): Boilerplate removal, keyword search, UI note

- **Boilerplate removal** (`retriever._near_duplicate`): the duplicate check now uses containment (share of the shorter passage's words found in a higher-ranked kept passage, `NEAR_DUPLICATE_CONTAINMENT` = 0.8, passages of 50+ distinct words only) instead of word-set overlap at 0.8. The same paragraph cut at different chunk boundaries in two filings scored only 0.48-0.78 on overlap but 0.82-1.00 on containment; related but different passages stayed at 0.75 or below. On the 18 test questions: **12 of 90 top-5 slots (13%, in 7 questions) repeated a passage already shown; now 0**, so those slots hold other evidence.
- **Keyword search** (`retriever.keyword_top`): BM25 over the company's passages filed on or before the as-of date (index cached per company and index size; tokens keep "5.02" and "99.1" whole). Its top 10 matches (`RETRIEVE_KEYWORD_K`) join the candidate pool; keyword-only hits get their real vector distance, so the 0.55 cutoff still applies. For exact terms an embedding can blur ("Item 5.02", "EYLEA HD", note names).
- **Test set result** (`scripts/14_eval_retrieval.py`): both are neutral on the metrics (hit@5 0.94, MRR 0.80, first 0.72, unchanged); 5 or 20 keyword candidates also change nothing. The metrics score only the first answering passage, and no question depends on an exact term alone, so neither change could move them; the boilerplate gain is the count above. Add exact-term questions if keyword search is to be judged.
- **UI note:** under Today's picks, "Model" is explained as the price-signal model that only ranks stocks and centers near the ~48% base rate; the forecast card shows the model's ranking skill (AUC from the model file) instead of the old "no skill" message.
- **Nightly job:** about 30-45 minutes on a normal night (moved from 8:00 PM to 11:55 PM on 2026-11-20 so the app can be used in the evening; `schedule_daily_update.ps1` matches); last night took about 1 h 45 min because it indexed 3 newly shortlisted companies (about 800 chunks). It writes to Chroma in `03_ingest` and the on-demand step, both under the pipeline lock.
- **Tests:** 51 pass (keyword search finds exact terms and returns nothing without a shared term; boilerplate cut at other boundaries is a duplicate, short news is never one).
- **Add my holding** (UI form under Paper positions, `POST /api/positions`, `sell_monitor.open_manual`): records a stock the user bought themselves (ticker, date, price paid or that day's close, optional reason). New `positions.source` column: `system` (opened by a picks run) or `mine`. A holding of mine gets the same vs-S&P tracking, flags and Close button; its reason is re-checked against new filings by Review open positions. It has no prediction, so it never enters the track record, and it doesn't block the system from opening its own position in the same stock. S&P 500 stocks only (needs price data).
- **Shortlist / Companies row click** now scrolls to the research box and clears another company's report (before, the only change was the heading at the bottom of the page).
- **Tests:** 53 pass (holding tracked at the user's price, kept apart from system positions, bad input rejected).
- **UI redesign** (`web/src/App.tsx`, `index.css`): the single long page is now five tabs (Today's picks, Positions, Shortlist, Research, Reflector) under a dark top bar; the URL hash keeps the tab on refresh, and the Positions tab shows the open count and how many are flagged. Positions has summary tiles (open positions, average vs S&P since entry, how many beat the S&P, how many need attention). Flags, thesis status, bull/bear lean and rule status are colored badges. Research has a company dropdown (core companies plus shortlist stocks whose filings are ready); clicking a shortlist or company row opens it there. **Wrong-company warning:** if a question names another known company (ticker or name, e.g. "amazon" while TGT is selected) and not the selected one, the app asks before searching, with "Switch to AMZN and ask" or "Ask about TGT anyway".
- **Traces tab** (2026-11-21; `web/src/Traces.tsx`, `GET /api/traces`, `GET /api/traces/{name}`, read-only): lists every saved research run (newest first, with evidence strength, debate lean and forecast, and whether it came from a picks run or the Research tab). Opening one replays it as a timeline labelled by agent: planned searches; each search's kept and dropped passages with the reason in plain words; the researcher's sufficiency judgments; draft claims; the critic's verdict and reason per claim; bull and bear theses; code checks; critic scores with their parts and reasons; send-backs with their searches; pruning; rebuttals; re-scores; the lean; and the forecast arithmetic (price model + debate adjustment × weight + approved rules). Searches are matched from `logs/retrieval_trace.jsonl` by ticker, query and time window. Each pick card and research report has a "How this … was made (trace)" button. No agent code changed; 53 tests pass.

### Next steps

Done (kept for the record):
1. ~~Review and submit Checkpoints 3 and 4~~ (all checkpoints submitted 2026-11-20).
2. ~~Recency-aware retrieval~~ (2026-11-19); ~~cross-encoder reranker~~ (2026-11-19); ~~keyword (BM25) candidates and boilerplate removal~~ (2026-11-20).
3. ~~Replace the placeholder SEC contact in `.env`~~ (done).
4. ~~Retrieval test set~~ (2026-11-19: `eval/retrieval_cases.json`, `scripts/14_eval_retrieval.py`).
5. ~~Build all eight agents from Checkpoints 1-2~~ (all built by 2026-11-17):

| Agent | Status |
|---|---|
| Screener | Built (`screener.py`, 2026-11-15; on-demand filings 2026-11-16) |
| Researcher | Built (`research_graph.py`: plan, retrieve, assess, answer, verify, revise, finalize) |
| Forecaster (probability of beating the S&P 500 over 90 days) | Built (`forecast_model.py`, `forecaster.py`, 2026-11-16) |
| Bull and bear agents ("contested" rule) | Built (`debate_graph.py`, 2026-11-16) |
| Critic | Built: claim and thesis checks in code and by LLM, scoring, rebuttal re-scores, send-back to the researcher (2026-11-17) |
| Ranker / reporter | Built (`ranker.py`, daily ranked picks and human-review section, 2026-11-17) |
| Sell monitor | Built (`sell_monitor.py`, paper positions, flags, thesis check, 2026-11-17) |
| Reflector + self-correction rules | Built (`reflector.py`, out-of-sample rule tests, human approval, 2026-11-17) |

6. ~~UI: React frontend + FastAPI backend~~ (built; tabs: Today's picks, Positions, Shortlist, Research, Traces, Reflector).

Open:
- Paper-position bar: `PAPER_MIN_PROBABILITY = 0.50` rarely clears with the calibrated forecaster (run 3 opened none). Options: base rate (~47%), keep 50%, or top pick only. Awaiting the user's decision.
- Reflector page: plain-language wording for new users (intro, Brier verdict, "underrates/overrates by X pts").
- Survivorship bias: add S&P 500 membership history so removed members enter the backtest.
- LLM cost/token tracking per call and per picks run.
- Deferred by the user: auto-close positions at 90 days.

### 2026-11-21: Final capstone deliverables and public repository

- README rewritten for newcomers (problem, architecture diagrams, agents, design decisions, safety, evaluation, setup, data build, review without running); `docs/USER_MANUAL.md` (every screen, a typical day, glossary, troubleshooting); `.env.example`; `examples/` (3 daily reports, BBY research + debate traces, the AMZN fail-closed run, retrieval metrics, comparison and failure demo).
- `docs/final/`: final report filled into the 7.1 template (`Final_Capstone_Report.docx` and `.pdf`, about 1,350 words), `Presentation_Script.md` (10 slides, about 9:40, plus a 90-second elevator pitch), `Video_Submission.md`. Slide deck: https://claude.ai/artifact/AJQzQ8upJQwsyMyMPEqqsm (downloads as .pptx or PDF).
- Repository: https://github.com/Ruthwik12/stock-research-agent (data, index, logs and `.env` are not committed).
