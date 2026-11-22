# Stock Research Agent: User Manual

How to use the app, screen by screen. For what the system is and how to install it, see the [README](../README.md).

> **Decision support only, not financial advice. Paper trading only.** The app never buys or sells anything. A
> "paper position" is a simulated holding used to track how the system's picks perform.

---

## Contents

1. [Before you start](#1-before-you-start)
2. [The screen at a glance](#2-the-screen-at-a-glance)
3. [Shortlist: what the screener found](#3-shortlist-what-the-screener-found)
4. [Research: ask a question about one company](#4-research-ask-a-question-about-one-company)
5. [Today's picks: the daily ranked list](#5-todays-picks-the-daily-ranked-list)
6. [Positions: what you hold and how it's doing](#6-positions-what-you-hold-and-how-its-doing)
7. [Traces: how a decision was made](#7-traces-how-a-decision-was-made)
8. [Reflector: how the system grades and corrects itself](#8-reflector-how-the-system-grades-and-corrects-itself)
9. [A typical day](#9-a-typical-day)
10. [Glossary](#10-glossary)
11. [Troubleshooting and FAQ](#11-troubleshooting-and-faq)

---

## 1. Before you start

1. Make sure the data has been built once (README, section 9.3) and Ollama is running.
2. Start the backend and the UI (README, section 9.4) and open **http://localhost:5173**.
3. **Avoid 11:55 PM to about 1:45 AM.** The nightly job is updating the data then and holds the index lock; research
   may fail or be slow.
4. Buttons marked "uses Claude credit" call the Claude API and cost money. Everything else is free.

## 2. The screen at a glance

The dark bar at the top has six tabs. They follow the system's loop:

| Tab | Question it answers |
|---|---|
| **Today's picks** | Which stocks look best today, and why? |
| **Positions** | What am I holding (on paper), and is it beating the S&P 500? |
| **Shortlist** | Which stocks did the screener flag tonight? |
| **Research** | What do a company's filings actually say about my question? |
| **Traces** | How exactly did the agents reach a given answer or pick? |
| **Reflector** | How accurate has the system been, and what corrections does it propose? |

The **Positions** tab shows the number of open positions and, in red, how many are flagged for your attention. The URL
remembers the tab, so refreshing the page keeps you where you were.

## 3. Shortlist: what the screener found

Every night the screener scores all ~500 S&P 500 stocks and keeps the top 10, plus any stock with a sudden event.

**Columns**

| Column | Meaning |
|---|---|
| Score | Average percentile of the three signals (0 to 1, higher is better) |
| 6-month vs S&P | Price return over 6 months minus the S&P 500's (momentum) |
| Earnings yield | Earnings divided by price (higher = cheaper) |
| EPS surprise | How much the last reported earnings beat or missed analysts' estimates |
| Why | Why it is listed: its score rank, a trigger (an unusual 5-day price move or a recent earnings surprise), or, in grey italics, "human review" for new listings with too little price history to score |
| Filings | Whether the company's SEC filings are indexed and ready for research |

**What to do**

- **Ready: click row** opens the stock in the Research tab.
- **Prepare filings** downloads, parses and indexes that company's filings in the background. It takes several minutes
  (the badge shows "Preparing…"). The nightly job also prepares the top 3 new names automatically.
- **Failed, retry** means preparation hit an error; click again.

## 4. Research: ask a question about one company

The Research agent answers **only from the selected company's own filings** (plus its insider trades, macro data and
trusted news), dated on or before today.

**Steps**

1. Pick a company from the dropdown (or click a row in Shortlist or in **Core companies** at the bottom). Only
   companies with ready filings are listed.
2. Type a specific question, for example:
   - "What drove revenue growth in the latest quarter, and what risks did management highlight?"
   - "Did the company change its full-year guidance?"
   - "What did the latest 8-K say about the CEO?"
3. Click **Research {ticker}** (uses Claude credit; about a minute).

**Reading the report**

| Part | Meaning |
|---|---|
| Evidence strength | strong / moderate / weak / insufficient, computed from how many claims were verified |
| Claims | Each statement ends with its evidence IDs, e.g. `[E3, E7]`. A note such as *partially supported* means the critic found only part of it in the source |
| Not covered by the evidence | What the question asked that the filings did not answer |
| Risks the critic flagged | Risks the critic thought the answer should mention |
| Removed by verification | Claims the critic struck out, with the reason. This is the system catching its own mistakes |
| Sources | Each evidence ID with a link to the SEC filing |

**"Insufficient evidence"** is a deliberate answer, not a crash: the filings did not support an answer, so the system
refused to guess. Ask a narrower question about something a 10-K, 10-Q or 8-K would contain.

**Wrong-company warning.** If your question names a different company than the one selected (for example asking about
Amazon while TGT is selected), a yellow notice appears before anything runs: **Switch to AMZN and ask**, or **Ask about
TGT anyway**. The agents never mix companies' evidence.

**Debate and forecast.** After a report, click **Debate and forecast** (uses Claude credit; about a minute):

- **Bull and bear theses**, each with a score from 0 to 10, the rebuttal it faced, and its score before the rebuttal.
- **Send-backs:** when the critic found a thesis promising but thinly supported, it asked the Researcher for more
  evidence; you see the search and what it added.
- **Forecast card:** the price model's starting probability, each debate adjustment with its reason, any approved
  correction rule, and the final probability of beating the S&P 500 over 90 days.

Click **How this answer was built (trace)** to replay the run in the Traces tab.

## 5. Today's picks: the daily ranked list

Click **Run today's picks** (uses Claude credit; about 2–3 minutes per stock, so 10–15 minutes). For the top 5
shortlisted stocks with ready filings it runs research, the bull/bear debate and the forecast, then ranks them. The page
refreshes by itself while it runs; you can use other tabs meanwhile.

**Each pick card shows**

- **Probability** of beating the S&P 500 over the next 90 days. 50% is a coin flip; historically only about 47% of
  S&P 500 stocks beat the index in a 90-day window.
- **Badges:** *leaning bull / bear* (which side won the debate), *contested* (strong cases on both sides; the
  adjustment is halved), *outside 35–65%* (an unusually confident forecast, sent to human review).
- **Thesis:** the strongest surviving argument on the winning side, with its score and evidence IDs.
- **Main risk:** the strongest argument on the other side.
- **Sources:** links to the filings cited.
- **How this pick was made (trace):** opens the full step-by-step record.

**Why didn't a pick open a position?** A paper position opens only when the probability is above 50% **and** above the
price model's own estimate (the debate must add an edge). If every pick is below 50%, nothing opens; that is the system
declining to act on a near coin flip.

## 6. Positions: what you hold and how it's doing

**Summary tiles:** open positions (opened by the system · yours), average return vs the S&P 500 since entry, how many are
beating the S&P, and how many need attention.

**Table columns:** ticker, opened date and entry price, 90-day horizon end, return vs the S&P 500 since entry (green =
ahead, red = behind), flags, thesis status, and a *mine* badge for holdings you added yourself.

**Flags** (they only recommend a look; nothing happens automatically):

| Flag | Meaning |
|---|---|
| trailing the S&P 500 by 10+ points | Stop-loss signal |
| 90 days reached: close and score | The forecast's horizon is over |
| new filing since entry | Something new was filed; consider *Review open positions* |
| thesis weakened / broken | The reason for holding has been undermined by new information |

**Actions**

- **Review open positions** (uses Claude credit): compares each position's original thesis with filings it never saw
  (filed after entry, or before entry but not used by the pick). Result per position: *intact*, *weakened* or *broken*,
  with the passage that changed it.
- **Close (paper)**: closes the position at the latest price after you confirm. Only you can close a position.
- **Add my holding**: record a stock **you** bought yourself: ticker, purchase date, price paid (or leave empty to use
  that day's close) and an optional reason. It is tracked against the S&P 500 and reviewed like the system's positions,
  but kept out of the system's track record. S&P 500 stocks only.

## 7. Traces: how a decision was made

Every research run is saved. The Traces tab lists them, newest first: time, ticker, question, evidence strength, claims
kept (and removed), the debate's lean, the forecast, and whether it came from a picks run or the Research tab.

Click a row to replay it as a timeline. Each step shows the time, the agent (colored badge) and what it did:

1. **Researcher: planned searches.**
2. **Researcher: search rounds.** Expand a search to see every candidate passage, whether it was **kept**, and why
   others were dropped: *not relevant enough*, *not in the top 5*, *duplicate*, *news limit reached*.
3. **Researcher: evidence judged sufficient / not enough**, with what was missing and the follow-up searches.
4. **Researcher: drafted claims.**
5. **Critic: verdict on every claim**, with its reason.
6. **Bull / Bear: theses**, then **Critic (code)** checks (e.g. "numbers not in cited text").
7. **Critic: scores** (expand a thesis for the parts of its score and the critic's reasoning).
8. **Critic → Researcher: send-backs**, with the extra searches.
9. **Debate: pruning, rebuttals, re-scores, the final lean.**
10. **Forecaster: the arithmetic**: price model + debate adjustment × weight + approved rules = final.

At the bottom, **Evidence the agents could cite** lists every passage with a link to its source.

## 8. Reflector: how the system grades and corrects itself

**Track record.** *Live* covers forecasts made since the app went live (graded once their 90 days pass). *Backtest*
covers the price model replayed on years of history. Both are scored with the **Brier score** (lower is better). The
benchmarks are "always say 50%" (0.2500) and "always say the base rate". Beating them, even slightly, means the forecasts
carry real information.

**Where forecasts miss most.** Groups of stocks (by sector, momentum, volatility…) where the average forecast differs
from what actually happened. A positive gap means the system *underrates* that group; a negative gap means it
*overrates* it.

**Correction rules.** Each night the Reflector turns large, consistent gaps into candidate rules (e.g. "Real Estate:
−5 points"). It learns a rule on older data and keeps it only if it makes forecasts more accurate on **later data it
never saw**. A rule that passes appears as **proposed** and changes nothing until you act:

| Status | Meaning | Your options |
|---|---|---|
| proposed | Passed the test on unseen data | **Approve** (it starts adjusting forecasts) or **Reject** |
| active | Approved; adjusts future forecasts | **Retire** if it stops helping (the nightly re-test adds a note when it does) |
| rejected / retired | Not in use | None |

The evidence column shows the dates it was learned and tested on and the accuracy before → after.

## 9. A typical day

1. **Morning:** open **Positions**. Anything flagged? If a new filing arrived, click **Review open positions**. Decide
   whether to close anything.
2. **Look at Shortlist.** Anything interesting with "Ready"? Click it and ask a question in **Research**.
3. **When you want fresh picks:** **Run today's picks**, then read each card and open its trace for the ones you care
   about.
4. **Weekly:** open **Reflector** and approve or reject any proposed rules.
5. **At night:** close the app before 11:55 PM so the nightly job can update the data.

## 10. Glossary

| Term | Meaning |
|---|---|
| 10-K / 10-Q | Annual / quarterly report filed with the SEC |
| 8-K | Filing for a material event (earnings release, CEO change, acquisition…) |
| Form 4 | Filing that reports a company insider buying or selling shares |
| FRED | Federal Reserve economic data (interest rates, VIX, unemployment, CPI) |
| As-of date | The date the analysis pretends it is; nothing published later is used |
| Evidence ID (E1, E2…) | A numbered passage the agents can cite |
| p_quant | The price model's probability before any research |
| p_final | The final probability after the debate adjustment and approved rules |
| Base rate | How often S&P 500 stocks beat the index over 90 days historically (about 47%) |
| Brier score | Average squared error of probability forecasts; 0 is perfect, 0.25 is a coin flip |
| Contested | Best bull and best bear thesis both scored 7 or more; adjustment halved |
| Paper position | A simulated holding used to track the system's picks; no money involved |

## 11. Troubleshooting and FAQ

| Problem | What to do |
|---|---|
| Page shows "Request failed" or nothing loads | Is the backend running on port 8000? Restart it (README 9.4) |
| Research is very slow or fails at night | The nightly job is running (11:55 PM to about 1:45 AM). Try later |
| A company is missing from the Research dropdown | Its filings aren't ready: use **Prepare filings** in Shortlist |
| "Insufficient evidence" | The filings don't answer the question; make it more specific to what filings contain |
| Error mentioning Claude or the LLM | Check `ANTHROPIC_API_KEY` in `.env` and your API credit |
| Search errors about embeddings | Start Ollama and make sure `bge-m3` is pulled |
| A trace says it could not be displayed | The file is still in `logs/runs/`; report it as a bug |

**Does the app trade for me?** No. It never connects to a brokerage. Positions are simulated.

**Can I trust the probability?** Treat it as a calibrated estimate with a small edge over the base rate, not a
prediction. Read the thesis, the main risk and the trace, and decide yourself.

**Why are most probabilities between 44% and 52%?** Markets are hard to predict over 90 days. A calibrated model stays
near the base rate unless the evidence is strong; a system that showed 80% would be overconfident.
