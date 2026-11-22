# Capstone Checkpoint 3: Adding a Retrieval Layer to the Stock Research Agent

Ruthwik · 2026-10-25

## 1. Architectural decision: retrieval is required

My agent must reason about what companies reported and said recently: results, guidance and new risks. That information is new (after the model's training cutoff), large (one JPMorgan 10-K is over 1.1 million characters of text) and must be verifiable, because an investor needs the source of every claim. Retrieval-augmented generation (RAG) meets all three needs: the agent pulls only the relevant passages and answers from them, with citations.

Retrieval is not used for everything. Prices, ratios and rankings are exact values that top-k search cannot compare across companies, so they go to data APIs, a calculator and a structured database.

## 2. Retrieval integration

The external data source is SEC EDGAR. A script downloads 26 filings for Apple, Microsoft and JPMorgan (10-Ks, 10-Qs and earnings press releases filed November 2024 to October 2026) and re-runs every night to add new filings. Filings are cleaned and split by section, then cut into 1,214 chunks. Each chunk is embedded locally with bge-m3 and stored in a Chroma vector database with its metadata.

The agents are built as a LangGraph state machine. A researcher plans 1-3 search queries, retrieves passages, and judges whether the evidence answers the question; if not, it searches again (up to 3 rounds). It then answers as short claims, each citing the passages it came from. A separate critic checks every claim against its cited text before anything reaches the user.

## 3. Key retrieval design choices

| Choice | Decision | Why |
| --- | --- | --- |
| Data source | 10-K and 10-Q Risk Factors and MD&A; earnings press release (exhibit 99.1) | Where results, guidance and risks are explained. |
| Companies | AAPL, MSFT, JPM | Two tech firms share vocabulary (cloud, AI), testing wrong-company retrieval; a bank adds contrast. |
| Chunking | Split by section, then by paragraph, about 600 tokens with 12.5% overlap | Keeps related statements together; overlap avoids cutting a sentence. |
| Chunk header | "AAPL 10-Q filed 2026-07-31, MD&A" embedded with each chunk | Puts company and period into the vector itself. |
| Metadata | Ticker, form, filing date, period, section, source URL | Enables filters and exact citations. |
| Number of results | Fetch 10 candidates, drop weak matches (cosine distance above 0.55) and near-duplicates, keep the best 5 per query | Less noise and cost, with room for opposing evidence. |
| Filters | Always ticker = target and filing date on or before the prediction date | Blocks other companies and look-ahead leakage. |

## 4. Demonstration: retrieval changes the output

I asked the same question twice with the same model (Claude Opus 5.5), as of 2026-10-25: "What drove Apple's Services net sales growth in its most recent reported quarter, and what risks did Apple highlight in that quarter's filing?"

| | Without retrieval | With retrieval (LangGraph agents) |
| --- | --- | --- |
| Answer | Could not answer for the latest quarter; offered fiscal 2025 figures from memory ("about $27.4B, up about 13%") | Fiscal Q3 2026 (ended June 27, 2026): Services $30,739 million vs. $27,423 million a year earlier, up 12%, "primarily due to higher net sales from advertising and cloud services" |
| Risks | Generic list from 2025 (Google search payments, App Store regulation, tariffs) | The Q3 2026 10-Q's new risk: supply constraints and rising costs for advanced semiconductors, NAND and DRAM, expected to intensify |
| Sources | Named filings it could not check | 5 cited passages, each with filing, date, section and SEC link |
| Confidence | 25%, self-reported | "Moderate", computed from verification (10 claims kept, 2 removed) |

Retrieval turned an honest "I don't know" into a specific, current, cited answer. It even showed the App Store, a driver in Q1 and Q2, dropping out in Q3.

The trace shows the loop working: the first search found only one risk, so the agent judged the evidence insufficient and searched twice more before answering. The critic removed 2 of 12 claims. Both were false alarms (the number check read dates and "10-Q" as figures), which I have since fixed; invented figures are still rejected.

## 5. Failure mode and mitigation

The main failure is retrieving similar-sounding passages from the wrong company or period and presenting them confidently. I tested this directly on the index (top 5 results each):

| Test | Without filters | With filters |
| --- | --- | --- |
| Researching Apple: "growth in cloud services revenue and investment in AI infrastructure and data centers" | 5 of 5 passages from Microsoft filings | 5 of 5 from Apple filings |
| Backtest dated 2026-05-15, JPMorgan: "net interest income in the most recent quarter" | 3 of 5 unusable: two filed after the prediction date (July and August 2026), one from Microsoft | 5 of 5 valid JPMorgan filings dated on or before 2026-05-15 |

The second case is look-ahead leakage, which makes a backtest look better than it is. The design defends in layers:

- Ticker and filing-date filters on every search.
- Critic code checks that reject claims citing another company, a later filing, or a number absent from the cited text.
- A sufficiency check that searches again or reports "insufficient evidence" instead of guessing.
- Aggregate questions routed to a database query (designed, not yet built).

One gap remains: even with filters, "most recent quarter" ranked JPMorgan's November 2025 10-Q above its May 2026 10-Q, because similarity has no sense of recency.

## 6. Summary

Retrieval grounds the agent in current, citable filings; filters, required citations, a claim-by-claim critic and an "insufficient evidence" fallback limit its main risk. Next I will add recency-aware ranking so "latest quarter" questions reach the newest filing first, then connect this researcher to the forecaster and the prediction log.
