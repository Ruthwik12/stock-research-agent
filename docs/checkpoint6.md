# Capstone Checkpoint 6: Safety, Evaluation and Human Oversight for the Stock Research Agent

Nov 15, 2026 · Ruthwik

## 1. Purpose and risks

My multi-agent system gives active investors a daily ranked list of stocks with a calibrated probability of beating the S&P 500 over 90 days, backed by cited evidence from SEC filings, news, and market and economic data. It is decision support with paper trading only. Main risks:

- **Hallucination:** figures or claims no filing made.
- **Wrong or future evidence:** another company's filings, or filings after the prediction date (look-ahead bias).
- **Overconfidence:** probabilities that calibration doesn't support.
- **Misuse:** output treated as advice for real trades.
- **Bad inputs:** broken feeds, parse errors, or instructions hidden in retrieved text.
- **Runaway loops:** cost and latency from agents looping.

## 2. Guardrails

| Layer | Guardrail |
| --- | --- |
| Input | Validate tickers and data feeds; treat retrieved text as data, never as instructions |
| Retrieval | Filter every search by ticker and by filing date on or before the prediction date |
| Output | Structured outputs; every claim cites a passage; code rejects claims citing another company, a later filing, or a number not in the cited text; the critic removes unsupported claims |
| Forecast | Start at the 50% base rate, cap at ±15 points, shrink toward 50% until enough predictions resolve |
| Tools and runtime | Read-only data and no trading tool; per-stock call budget; failures return "insufficient evidence"; every run logged |
| Presentation | Disclaimer and sources on every output |

## 3. Evaluation metrics

| Metric | Initial target |
| --- | --- |
| Groundedness: claims supported by their citations | 95% or more |
| Retrieval accuracy: wrong-company or future passages reaching the agents | Zero |
| Calibration: Brier score; predicted vs actual hit rate per confidence bucket | Brier below 0.25 ("always 50%"); gap within 5 points |
| Forecast value: hit rate and paper return vs an S&P 500 index fund | Beat the baseline over several hundred predictions |
| Fallback rate: share ending in "insufficient evidence" | Investigate above 15% |
| Escalation rate | About 10% of picks |
| Latency and cost per stock | Within budget |

Measured on as-of-date backtests first, then on every daily run.

## 4. When a human must intervene

Routine, well-supported picks publish automatically. A person must act when:

| Trigger | Human action |
| --- | --- |
| Any real-money trade | Always a human decision; the system cannot trade |
| Contested pick, or probability outside 35-65% | Review before publishing |
| Missing data, parse failure or exhausted budget | Pick held for the operator |
| Suspected prompt injection in a source | Source blocked and reviewed |
| New correction rule from the reflector | Approval before it takes effect |
| Calibration gap above 10 points over 30+ resolved predictions | High-confidence picks paused |
| Stop-loss hit or thesis broken on an open pick | User decides whether to close the paper trade |

## 5. Overall strategy and trade-offs

Guardrails **prevent** known failures, metrics **detect** what slips through and trigger reviews, and humans **decide** where impact or ambiguity is high. Lessons from resolved predictions become rules only after human approval.

- **Autonomy vs oversight:** reviewing every pick would defeat daily automation and cause review fatigue, so escalation targets about 10% of picks.
- **Reliability vs efficiency:** critic loops and strict checks add latency and cost, so both are capped.
- **Safety vs coverage:** failing closed yields more "insufficient evidence"; my number check already dropped two correct claims in testing. I prefer fewer answers to wrong ones, and track the fallback rate.

The agent runs on backtests first, then on paper trading, with every run logged for audit. Autonomy expands only after the metrics hold over several hundred resolved predictions.
