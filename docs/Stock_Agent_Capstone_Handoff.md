# Capstone Handoff: Stock Research Agent (CMU Agentic AI Course)

*Prepared October 25, 2026, from a long planning conversation. Paste this whole file into a new Claude chat (or upload it) and say: "Read this fully, then help me build the next step."*

---

## 0. Instructions for the next Claude

- You are continuing a project that was planned in an earlier conversation. Everything you need is below. Do not re-propose project ideas; the idea is decided (Section 2).
- The user, **Ruthwik**, is a working software developer (MS in Computer Science, background in BI/ETL, now a software developer). He is comfortable with code and databases (he knows SQL Server and SSMS), so explain agent and RAG concepts, not programming basics.
- **Never invent results.** Checkpoint 3 needs real evidence (traces, outputs) from code he actually ran. If the sandbox you're in has no internet, say so and give him scripts to run on his own machine.
- Build **incrementally**: one small script at a time, which he runs and checks before the next one.
- The project is **decision support, not financial advice**. Paper trading only. Keep a disclaimer on all outputs.
- Facts marked *(verify)* came from general knowledge or dated web snapshots (October 2026). Free-tier limits, model rankings, and tool features change, so check current documentation before depending on them.

---

## 1. Course context and status

- **Course:** Carnegie Mellon University agentic AI executive course. Three project tracks: Personal Assistant, Forecaster, Research Assistant. **Chosen track: Forecaster.**
- The capstone is built up through module checkpoints and ends with a recorded presentation. Checkpoints must be written submissions (text, PDF, or DOCX), and usually 600-900 words.
- Only publicly available, synthetic, or anonymized data may be used.

| Checkpoint | Topic | Status |
|---|---|---|
| 1 (Assignment 1) | Problem, user, why an agent, environment, actions, feedback | Submitted. Positive feedback. Suggestions: show how probabilities are validated against past outcomes, and how bull/bear conflicts are handled (both addressed in Checkpoint 2). |
| 2 (Assignment 2) | Reasoning loop, memory, tools, improvement over prompt-only | Submitted. Positive feedback. Instructor suggested a self-correction loop (Section 6). |
| 3 (Assignment 3) | Retrieval (RAG): decision, integration, demonstration, design choices, failure mode | **Not started.** Needs a working retrieval pipeline plus a 600-900 word written update. Draft in Appendix B. |

**Checkpoint 3 rubric (must all be present or the submission is marked Incomplete):**
1. A clear architectural decision on whether retrieval is required, with justification.
2. Evidence that a semantic retrieval mechanism is integrated using an **external data source**.
3. Demonstration that retrieval meaningfully influences or grounds the output (output comparison, system traces, or retrieval evidence).
4. Key retrieval design decisions: data source selection, document segmentation/chunking, and **number of retrieved results**.
5. At least one retrieval-related failure mode and how the design mitigates it.

---

## 2. The project in one paragraph

A **stock research and decision-support agent** for active investors (any experience level). Each day it screens a stock universe (for example, the S&P 500), researches a shortlist using filings, news, and data, and outputs a ranked list of buy candidates and sell reviews. Each result has a **calibrated probability** that the stock outperforms the S&P 500 over a fixed horizon (for example, 90 days), plus a thesis, target, time horizon, and main risks. It stores every recommendation, checks outcomes later, and learns from its mistakes. Success is measured by **forecast accuracy and calibration versus simple baselines** (S&P 500 index fund, "always 50%"), **not** by claimed profit.

**Why an agent, not a single prompt:** stale model knowledge, no live data access, unreliable arithmetic, no ability to screen and research many stocks in a multi-step process, and no accountability or learning over time.

**Honest expectations:** markets are hard to beat. A realistic good result is "modest edge (for example, 52-58% hit rate on best calls), well-calibrated confidence, explainable decisions." Realistic probabilities mostly sit in the 45-65% range.

---

## 3. Agent design

### Roles (multi-agent workflow)
1. **Screener:** scans the universe using fundamentals, price trends, earnings surprises, and news; outputs a shortlist.
2. **Researcher:** studies each shortlisted stock using filings, transcripts, and news via **RAG**.
3. **Forecaster:** estimates the probability of outperformance, combining quantitative signals with the research.
4. **Bull and bear agents:** each argues the strongest case for and against.
5. **Critic:** checks reasoning for weak evidence, missing risks, overconfidence; can send work back for more research.
6. **Ranker/reporter:** produces the ranked output (thesis, target, horizon, risks, citations).
7. **Sell monitor:** reviews open picks daily against thesis, target, and stop-loss.
8. **Reflector:** after outcomes resolve, scores predictions and records lessons.

### Reasoning loop (ReAct-style)
Thought, Action, Observation, repeated. Example: flag a stock after earnings; retrieve the filing; observe "revenue beat, guidance cautious"; reason that guidance matters over a 90-day horizon; retrieve similar past cases from memory. The loop ends when the critic judges the evidence sufficient or a step budget runs out (then report lower confidence).

### Handling conflicting bull/bear evidence
- If they disagree because a **fact is missing**, loop back for targeted research.
- If the evidence **truly conflicts** (for example, strong earnings growth but rising debt and risks), pull the probability toward the base rate, label the pick **"contested,"** and show both arguments.
- If the evidence points one way, a stronger probability is allowed.

### Memory
- **Short-term:** working state for one run (shortlist, retrieved evidence, calculations, bull/bear arguments, critic notes).
- **Long-term:** every prediction (date, ticker, probability, thesis, evidence, later the outcome and a lesson). Used for calibration checks, retrieving similar past cases and lessons, and sell monitoring (thesis and stop-loss persist across days).

### Tools
- Market data API (prices, fundamentals), news and economic data search (including Fed data), **document RAG** over filings and transcripts, calculator/code execution (momentum, valuation ratios, excess return, Brier score, calibration), and a database (memory read/write).

---

## 4. How the probability is produced

The percentage must **not** be the LLM saying "I'm 80% sure" (models are overconfident). Definition: **"the probability that this stock beats the S&P 500 over the next N days"** (fixed N, fixed total-return definition).

1. **Base rate:** start near 50%.
2. **Quantitative signals** (computed by tools): momentum, earnings surprise, valuation, analyst revisions, news sentiment. A simple model (even logistic regression on historical data) gives a first estimate (for example, 60%).
3. **Agent adjustments** from evidence the numbers miss (guidance cut, new risk), as structured judgments with direction and strength; the critic challenges them.
4. **Calibration** against past results: if "70%" calls win only 58% of the time, report about 58%. With too little history (under about 100 resolved predictions), **shrink toward the base rate**.

Example: 50% base, 60% model, 57% after agent adjustment, 55% after calibration; the user sees 55% with reasons.

**Evaluation metrics:** Brier score (baseline "always 50%" = 0.25), calibration curve (bucket by confidence, plot actual hit rate), hit rate by confidence, and a paper portfolio versus an S&P 500 index fund (return, max drawdown, Sharpe).

**Evaluation pitfalls to avoid:** changing definitions after the fact; too few predictions (want several hundred); **look-ahead bias** (every input, including the RAG index, must be limited to what was known at the prediction date); calibrating and testing on the same period; hiding weak results.

---

## 5. Retrieval (RAG) design for Checkpoint 3 and beyond

### Decision: retrieval IS required
Filings and transcripts are new (after the model's cutoff), large (a 10-K is hundreds of pages), and must be citable. RAG grounds the researcher agent in real passages.

### Where RAG is used
1. **Document RAG (main):** researcher agent queries an index of SEC filings (and optionally news) for evidence.
2. **Memory RAG (later):** retrieve similar past predictions and lessons.

**Retrieval is done by the agents, not by the end user.** The user sees the result with citations. If a chatbot is added later, the agent decides when to retrieve for follow-up questions.

### What is NOT RAG (use structured tools instead)
Prices, ratios, rankings, and "which company has the highest earnings?" need **all** the data, not top-k similar chunks. Route these to a **structured database (text-to-SQL)**. A chatbot would need a router: SQL for exact, aggregate, and comparison questions; document RAG for explanations ("why did earnings jump?"); both when needed.

### Starter design choices (adjust after testing)
| Choice | Starting point |
|---|---|
| Data source | SEC EDGAR: 8-K earnings releases (short, easy), plus 10-K / 10-Q risk-factor and MD&A sections |
| Scope for Checkpoint 3 | 3-5 companies, 2-3 documents each |
| Chunking | Section-aware split, about 500-800 tokens, about 10-15% overlap (check embedding model's input limit) |
| Metadata per chunk | ticker, form type, filing date, section, source URL |
| Retrieval | Fetch top about 10 candidates, filter/rerank, pass best 4-5 to the agent |
| Filters | Always filter by ticker and **as-of date (no documents after the prediction date)** |
| Required in answers | Every claim cites a retrieved chunk; critic checks the citation supports the claim |

### Retrieval failure mode (for the write-up)
**Retrieving irrelevant or outdated passages** (wrong company or period) and presenting them confidently; also look-ahead leakage in backtests. Mitigations: metadata filters (ticker, as-of date), mandatory citations plus critic verification, a sufficiency check (re-query or lower confidence), and routing aggregate questions to SQL.

### Embedding model and vector database (free options) *(verify)*
**Embedding models:**
- **BGE-M3** (recommended start): free, MIT license, 8,192-token input; slower on CPU.
- **nomic-embed-text:** very easy to run locally (for example via Ollama); slightly lower benchmark quality.
- **Qwen3-Embedding** (0.6B to 8B): the 8B leads open-source rankings but needs real GPU memory; smaller variants are laptop-friendly.
- **all-MiniLM-L6-v2:** tiny and fast, but only **512-token input**, shorter than the 500-800 token chunks above (use smaller chunks if chosen).
- **Gemini Embedding (API):** free tier via the Gemini API; documents leave your machine.
- **Rule:** never mix embedding models in one index (vectors are not comparable; switching means re-embedding). Don't chase the leaderboard; test on your own questions.

**Vector databases:**
- **Chroma** (recommended start): local, simple, supports metadata filters.
- **pgvector (PostgreSQL):** good if structured tables are in PostgreSQL too (one database, standard tools like DBeaver and pgAdmin).
- **Qdrant:** free local/Docker, strong filtering, built-in web dashboard.
- **FAISS:** fast library but no built-in metadata filtering; skip.

### Viewing and debugging Chroma (the user wants an SSMS-like tool)
- Local Chroma (`PersistentClient(path=...)`) stores data in a folder with a `chroma.sqlite3` file. **Close your script, copy the file, and open the copy** in **DBeaver** (closest to SSMS) or **DB Browser for SQLite**. Treat it as read-only. Table names vary by version; typically `collections`, `embeddings`, and `embedding_metadata` (key/value rows, so a pivot query is needed to see one row per chunk; the document text is usually under a key like `chroma:document`). Vectors are **not** visible as readable rows.
- Community Chroma viewers (verify maintenance before use; keep on localhost): **ChromaDB UI** (needs a running Chroma server, v2 API; browse, filter, semantic search), **chroma_viewer** (local folder, simple), **chromadb-admin**, **ChromaUI**, and **Chroma Explorer** (macOS native app).
- To use a server-based UI: `chroma run --path ./chroma_db`, then connect to `http://localhost:8000` (tenant `default_tenant`, database `default_database`). Don't open the same folder with `PersistentClient` while the server is running; use `HttpClient` instead. Possible CORS errors if a hosted UI talks to localhost.
- **Debugging philosophy:** treat the vector store as **rebuildable**; keep source documents and re-run ingestion. Log every retrieval (query, filters, returned chunks with metadata and scores). Keep a small fixed test set of 10-20 questions with known right passages. Consider LangSmith or Langfuse for tracing *(verify)*. If a real admin console matters, pgvector with DBeaver/pgAdmin is a legitimate reason to choose it over Chroma.

---

## 6. Planned extension: self-correction loop (instructor suggestion)

1. At the end of each 90-day cycle, compute calibration error (across all confidence buckets, not just 70%).
2. If error is too high (for example, 15%), the agent proposes a specific correction rule (for example, "lower confidence when tech stocks have rising debt").
3. Save the rule to long-term memory so the critic can use it next run.

**Safeguards to design in:** require a minimum number of cases before proposing a rule (avoid overfitting noise); test each rule on past predictions it was not derived from; mark new rules tentative and keep them only if later calibration improves; date each rule, record its evidence, and expire/retire stale ones; keep a human-readable list of active rules. Build the core pipeline first; backtesting can supply history faster than waiting for live 90-day cycles.

---

## 7. Data stores

### Structured database (source of truth for exact data and memory)
Roughly 5-7 tables. Use a "long" format for financials so new metrics add rows, not columns.

```sql
-- sketch; adapt names and types
companies(ticker, name, sector, industry, market_cap)
prices_daily(ticker, date, open, high, low, close, volume)
financials(ticker, period, metric, value)            -- long format
earnings_events(ticker, date, eps_reported, eps_expected, surprise_pct)
documents(doc_id, ticker, doc_type, filing_date, source_url)
predictions(prediction_id, ticker, prediction_date, horizon_days,
            probability, thesis, evidence_refs, outcome, resolved_date, lesson)
news_items(ticker, date, headline, sentiment_score)   -- optional
```

- **Predictions** live in the structured DB (calibration is a `GROUP BY`; open-pick monitoring is a date/value filter). Optionally embed the text fields (thesis, lesson) in the vector index with the `prediction_id`, for "retrieve similar past cases." Start with the structured table alone.
- **Text-to-SQL safety:** read-only connection, approved tables only, show the user the query and figures, and predefine views for common calculations.
- Structured financial data can often come from SEC structured (XBRL) data or market data APIs *(verify what is available)*.

### Free data sources *(verify current limits on each provider's page)*
- **Prices:** yfinance (unofficial; good for history), Alpha Vantage, Finnhub (free WebSocket reported), Twelve Data, Financial Modeling Prep, Polygon, Alpaca (data plus paper trading). Most free tiers are delayed about 15-20 minutes; fine for daily recommendations.
- **Filings:** SEC EDGAR (free, no key, send a descriptive User-Agent, about 10 requests per second limit).
- **Macro:** FRED (free API key).
- **News:** Finnhub and Alpha Vantage news endpoints, NewsAPI (restricted free plan), GDELT, RSS feeds.
- **Cautions:** rate limits (screen first, fetch details for the shortlist only, cache everything); free data may lack adjusted prices and delisted stocks (survivorship bias); check terms before showing data publicly.

---

## 8. MCP notes (course Lab 1.2 concepts)

- **Host:** the app the user talks to; embeds the LLM and runs clients (for this project, the agent app).
- **Client:** lives in the host; holds a 1:1 connection to a single server (two servers means two clients; isolation).
- **Server:** exposes tools, resources, and prompts over MCP; messages are JSON-RPC 2.0. MCP is the protocol, like USB; each server is a separate program that speaks it.
- The **LLM chooses a tool, not a server**: the host merges all servers' tool lists into one menu; the host routes the chosen tool call to the right client. Tool names and descriptions matter for correct selection.
- Tool definitions and implementation live on the **server**; client sessions, the merged tool list, routing table, and the LLM loop live in the **host**.
- Hosts like Claude Desktop use a JSON config listing servers (one client per entry); a custom host creates clients in code with the MCP SDK. Using MCP is optional for this project; one or several servers (for example, market data, news, filings) are both valid. *(verify SDK syntax in the official docs)*

---

## 9. Build plan

### Minimum slice for Checkpoint 3 (do this first)
1. **Choose scope:** 3 tickers; per company, a recent 8-K earnings release plus one 10-K or 10-Q.
2. **Download** from SEC EDGAR; extract clean text; split by section.
3. **Ingestion script:** chunk, embed (BGE-M3 or nomic-embed-text), store in Chroma with metadata (ticker, form type, filing date, section, URL). Verify stored chunks in DBeaver or a viewer.
4. **Retrieval script:** query to top-k chunks with filters (ticker, as-of date); log query, filters, chunks, metadata, and scores to a file (the **retrieval trace**).
5. **With/without comparison:** same question (for example, "What changed in this company's risk outlook in its latest quarter?") answered once without retrieval and once with retrieved chunks and citations. Save both outputs.
6. **Failure-mode demo:** show a query that retrieves a wrong period or company, then the metadata/date filter fixing it.
7. **Fill in Appendix B** with real numbers and results; confirm 600-900 words; export to PDF or DOCX.

You do **not** need the screener, bull/bear agents, critic, calibration, SQL database, or UI for Checkpoint 3. The same ingestion and retrieval code becomes the researcher agent's tool later.

### Later roadmap
1. Structured DB and data ingestion (prices, financials, earnings events).
2. Forecaster with quantitative signals and base-rate shrinkage.
3. Bull, bear, and critic agents; conflict handling rules.
4. Prediction logging, outcome resolution, calibration dashboard.
5. Backtest harness over historical dates with strict as-of filtering; ablations (no RAG vs RAG; basic vs agentic RAG).
6. Self-correction rule loop (Section 6).
7. Optional chatbot with SQL/RAG router; paper-trading mode; simple Streamlit dashboard.
8. Recorded presentation (course requirement).

**Suggested stack:** Python; LangGraph or an Anthropic/OpenAI agent SDK for orchestration; Chroma (or pgvector); SQLite or PostgreSQL for structured data; Streamlit for UI; optional tracing with LangSmith or Langfuse. *(verify versions)*

---

## 10. Open items and decisions still to make

- Which 3-5 tickers for the Checkpoint 3 slice.
- Embedding model (BGE-M3 versus nomic-embed-text, depending on laptop speed) and final chunk size, overlap, and top-k after testing.
- Whether the vector store stays Chroma or moves to pgvector (one database, SSMS-style tooling).
- Operating system (affects which Chroma desktop viewer is available).
- Which LLM and API will power the agents (cost and rate limits).
- Whether to ask the instructor in office hours: what level of working demonstration is expected for Checkpoint 3, whether the S&P 500 universe is narrow enough, and whether 100 resolved predictions is a realistic calibration threshold.

---

## Appendix A: Checkpoint 2 text (submitted)

**Capstone Checkpoint 2: Refining the Stock Research Agent**

**1. Refined agent concept.** My agent is a stock research and decision-support agent for active investors. Each day it screens a stock universe such as the S&P 500, researches a shortlist, and produces ranked buy candidates and sell reviews. Each result includes a probability that the stock outperforms the S&P 500 over a fixed horizon (for example, 90 days), plus a thesis, a target, and the main risks. It is decision support, not financial advice, it uses only publicly available data, and it trades only on paper. In this refinement, every capability I add is justified by a specific limitation of a prompt-only model.

**2. Reasoning loop.** The agent uses a ReAct-style loop: it reasons about what it needs, takes an action, observes the result, and reasons again until it has enough evidence. For example, the screener flags a stock after an earnings release. Thought: I need the actual results compared with expectations. Action: retrieve the earnings filing and transcript. Observation: revenue beat expectations, but forward guidance was cautious. Thought: guidance matters most over a 90-day horizon, so check how similar guidance changes affected returns before. Action: retrieve similar past cases from memory. Each observation changes the next step. The loop ends when the critic judges the evidence sufficient or a step budget runs out, in which case the agent reports lower confidence. Handling conflicting evidence: if bull and bear disagree because a fact is missing, the loop returns for targeted evidence; if the evidence truly conflicts (strong earnings growth alongside rising debt and risks), the probability is pulled toward the base rate, the pick is labeled "contested," and both arguments are shown; if the evidence points one way, a stronger probability is allowed.

**3. Memory.** Short-term memory holds the working state of a single run (shortlist, retrieved evidence, calculations, bull/bear arguments, critic notes). Long-term memory is a persistent record of every prediction (date, ticker, probability, thesis, evidence, later the outcome). It is used to validate probabilities (group past predictions by confidence and compare predicted with actual results; shrink toward the base rate until enough predictions have resolved), to retrieve similar past cases and lessons, and to monitor open picks (thesis, target, and stop-loss persist across days).

**4. Tool use.** Market data API (current and historical prices and fundamentals; addresses stale knowledge); news and economic data search (grounds analysis in current events); document retrieval (RAG) over filings and transcripts (lets the agent cite what a company actually reported); calculator or code execution (momentum, ratios, excess return, calibration scores; LLMs are unreliable at arithmetic); database (reads and writes long-term memory). Example task requiring tools: deciding whether an earnings report was a genuine surprise requires retrieving reported figures and analyst expectations and computing the surprise percentage.

**5. Improvement over prompt-only.** Stale or invented facts are fixed by tools and retrieval with citations; overconfident probabilities by long-term memory calibration and the critic; one-sided reasoning by bull/bear agents and the critic; repeated mistakes by memory. The failure mode I most want to resolve is the overconfident, ungrounded probability, addressed with grounded evidence, an adversarial check, and calibration against recorded outcomes.

---

## Appendix B: Checkpoint 3 draft (fill the [brackets] with REAL results)

**Capstone Checkpoint 3: Adding a Retrieval Layer to the Stock Research Agent**

**1. Architectural decision: retrieval is required.** My agent must reason about what companies have reported and said recently: earnings, guidance changes, and new risks. That information is new (after the model's training cutoff), large (a single 10-K runs to hundreds of pages, and the agent screens many companies), and must be verifiable (an investor needs to see the source of a claim, and a model recalling figures from memory can invent them). Retrieval-augmented generation (RAG) addresses all three: the agent fetches only the relevant passages and bases its analysis on them, with citations. Retrieval is not needed for everything: prices, ratios, and rankings are exact values handled by data APIs, a calculator, and a structured database, so they stay outside RAG.

**2. Retrieval integration.** External data source: SEC EDGAR filings (10-K, 10-Q, and 8-K earnings releases) for [starter set of companies and period]. Documents are downloaded, cleaned, split into chunks, converted to embeddings with [embedding model], and stored in [vector store] with metadata. When the researcher agent investigates a stock, it writes a query such as "forward guidance and new risk factors for [ticker]," the query is embedded, and the closest chunks are returned. The agent decides whether the evidence is sufficient or whether to search again with a refined query.

**3. Design choices.** Chunking: split by document section (for example, Risk Factors and Management's Discussion), then into chunks of roughly [N] tokens with about [N%] overlap; section-aware splitting keeps related statements together and overlap avoids cutting a key sentence in half. Metadata: ticker, form type, filing date, section, source URL, enabling filtering and citation. Number of results: the retriever fetches the top [N] candidates, then keeps the best [N] after filtering and reranking; fewer chunks reduce noise and cost while still covering supporting and opposing evidence. Filters: queries are limited to the target ticker and to documents dated on or before the prediction date.

**4. Demonstration: retrieval changes the output.** Question: "[example question]."

| | Without retrieval | With retrieval |
|---|---|---|
| Answer | [paste real output] | [paste real output] |
| Sources cited | [none or unverifiable] | [filing, section, date] |
| Probability / confidence | [value] | [value] |

[Two or three sentences on what changed.] A retrieval trace (query, chunks returned with metadata, which chunks the answer used) is attached: [screenshot or log].

**5. Failure mode and mitigation.** Failure mode: retrieving irrelevant or outdated passages. Semantic search returns text that sounds similar, not necessarily text from the right company or period, and the agent might present it confidently (also a look-ahead risk in backtests). The design manages it with metadata filters (ticker, as-of date); required citations that the critic agent verifies against the retrieved passage; a sufficiency check (re-query or report lower confidence instead of guessing); and routing aggregate questions (for example, "which company has the highest earnings?") to a structured database query, because top-k retrieval cannot compare all companies. [Concrete example from your own testing.]

**6. Summary.** Retrieval grounds the agent's analysis in current, citable documents, and the design limits its main risk by filtering, requiring citations, and routing exact questions to structured tools. [One sentence on what you will improve next.]

---

## Appendix C: Suggested first message to the new Claude

> I'm continuing a CMU agentic AI capstone (stock research agent). I've pasted the full handoff document. I haven't built anything yet. For Checkpoint 3, I need a minimal retrieval pipeline over a few SEC filings so I have real evidence for the write-up. Please start by helping me pick 3 tickers and write the ingestion script (download from EDGAR, chunk by section, embed, store in Chroma with metadata). Give me one script at a time, and I'll run it and paste back the output before we move on. Don't invent any results.
