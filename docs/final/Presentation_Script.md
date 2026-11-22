# Presentation script (8–10 minutes) and elevator pitch

Deck: "Stock Research Agent: Final Capstone" (12 slides). The same text is in each slide's speaker notes.
Target pace: about 140 words per minute. Total about 9 minutes 55 seconds, which needs a brisk pace: practise once with a timer.


| # | Slide | Time | Cumulative |
|---|---|---|---|
| 1 | Cover | 0:20 | 0:20 |
| 2 | The problem and the user | 0:55 | 1:15 |
| 3 | Goal and scope | 0:40 | 1:55 |
| 4 | Architecture | 1:05 | 3:00 |
| 5 | The eight agents | 0:50 | 3:50 |
| 6 | The life of one pick | 1:05 | 4:55 |
| 7 | Key design decisions and how they evolved | 1:00 | 5:55 |
| 8 | Forecasting | 0:40 | 6:35 |
| 9 | Retrieval (RAG) | 0:55 | 7:30 |
| 10 | Evaluation and results | 0:55 | 8:25 |
| 11 | Safety, reliability and oversight | 0:45 | 9:10 |
| 12 | Repository and next steps | 0:45 | 9:55 |


---

## 1. Cover (0:20)

Hi, I'm Ruthwik. This is my capstone: the Stock Research Agent. In the next ten minutes I'll cover the problem, how the
system works, how the design evolved, how well it performs, and where the code lives. One line to keep in mind
throughout: this is decision support with paper trading only. The system never places a trade.

## 2. The problem and the user (0:55)

The user is an individual investor managing their own portfolio. They have the same information as everyone else,
because filings are public, but no analyst team to read it. Around 500 companies, each filing annual, quarterly and
event reports, plus prices, insider trades, macro data and news. The obvious shortcut, asking a chatbot, is unsafe for
money decisions: it answers from stale memory, confuses companies, invents numbers and sounds just as confident when it
is wrong. So the user needs four things: a daily shortlist, answers grounded in the actual filings with citations, an
honest probability rather than an opinion, and a system that keeps watching and learning while leaving the decisions to
them.

## 3. Goal and scope (0:40)

I framed the goal as a measurable question: what is the probability this stock's total return beats the S&P 500 over the
next 90 days? That turns vague advice into something I can grade. Success has five parts: every claim grounded in a
passage with no wrong-company or future data; retrieval that finds the answering passage in the top five for at least 90
percent of test questions; calibration, meaning a Brier score below the 0.25 of a coin flip and no worse than the base
rate; honest failure, saying insufficient evidence instead of guessing; and a person taking every consequential action.
The boundaries keep it safe and affordable: S&P 500 only, paper trading only, free public data, one machine, and bounded
model cost.

## 4. Architecture (1:05)

*(Five clicks.)* Here is the system, layer by layer. **[click]** Public data: SEC filings and insider trades, Yahoo
Finance prices, Finnhub news, FRED macro data and the S&P 500 list. **[click]** A nightly job downloads what’s new,
indexes text, screens all 500 stocks and runs the self-checks. **[click]** Two stores on purpose: exact numbers in
SQLite, computed only by code, and filing and news passages in a Chroma vector database, the RAG store. Every tool
takes an as-of date, so nothing from the future leaks in. **[click]** Then the eight agents; the orange ones call
Claude, always through a validated schema. **[click]** FastAPI and a React UI put the person at the end, where the
decisions are.

## 5. The eight agents (0:50)

Eight agents, each with one job. The key choice is where the language model is used. Screening, ranking, arithmetic
and learning are plain code, so they cannot hallucinate. The screener works on numbers only: Yahoo Finance prices and
volume, EPS from the SEC’s structured XBRL data, and earnings surprises; it never reads filing text. The model is used
only where text needs judgment: the researcher, the critic, the bull and bear analysts, and the sell monitor’s thesis
check. The critic checks the researcher’s claims and scores the debate, sending thin theses back for more evidence.

## 6. The life of one pick (1:05)

*(Four clicks.)* This is the life of a single pick. **[click]** The screener finds it, the researcher builds cited
claims, the critic removes anything unsupported, the bull and bear analysts write three theses each, and the
forecaster turns the survivors into a probability. **[click]** The debate is my Tree-of-Thought: a beam search that
prunes weak theses but always keeps the best bull and best bear, and sends promising but thin ones back for one more
search, at most twice. **[click]** The ranker opens a paper position only above 50 percent; the sell monitor watches
it; it is graded after 90 days; and the reflector finds groups of stocks it gets wrong. **[click]** The loop closes
with a person: corrections apply only when I approve them, and closing a position is my decision.

## 7. Key design decisions and how they evolved (1:00)

The design grew one layer per module. Module 1 framed the task as a gradable probability. Module 2 added the reasoning
loop, tools and memory; the instructor’s suggestion of a self-correction loop became the reflector. Module 3 added
retrieval; an unfiltered Apple query returned five Microsoft passages, so company and date filters became mandatory.
Module 4 placed Tree-of-Thought only in the debate, Module 5 designed the agent team, and Module 6 the guardrails and
human triggers. Building corrected the design three times, in orange: the forecaster was retrained walk-forward on ten
years after collapsing to the base rate; a reranker, recency and keyword search fixed “latest quarter” retrieval; and
two rules failed an 8.5-year re-test and were retired.

## 8. Forecasting (0:40)

How the probability is made: three checked parts. First, a price model: a logistic regression on three price features, the stock's six-month and one-month return against the S&P 500 and its three-month volatility, calibrated so its percentages match reality. It was trained on about 55,000 stock-months and tested walk-forward, each year predicted only from earlier data. Prices carry little signal, so it stays near the 48 percent base rate: an honest anchor. Second, the debate nudge, capped at 15 points and half-weighted until live results prove it helps. Third, correction rules, only once I approve them. Every forecast is saved and graded automatically after 90 days.

## 9. Retrieval (RAG) (0:55)

This is how the researcher finds evidence. Every night, filings and trusted news are split into chunks of about 600
tokens, each tagged with company, form and filing date, embedded locally with the bge-m3 model, and stored in a Chroma
vector database: about seven thousand passages. At question time, the search is first filtered to the selected company
and to filings on or before the as-of date. Then it is hybrid: the top 30 passages by semantic similarity, the top 10
by BM25 keyword search, and the top 8 from the newest filings. A local cross-encoder reranks those 30 to 48
candidates, preferring recent filings. Weak matches, duplicates and excess news are dropped, and the best five go to
the researcher, each with an evidence ID it must cite. This lifted right-passage-first from 0.44 to 0.72.

## 10. Evaluation and results (0:55)

How do I know it works? Four kinds of evidence. Retrieval, from the RAG slide: on 18 hand-built questions the right passage comes
first 72 percent of the time. Isolation: with filters, zero wrong-company or future passages, where the unfiltered search
returned five of five from Microsoft. Forecasting: over 44,660 walk-forward forecasts from 2018 to 2026, the Brier score
is 0.2487, versus 0.2493 for the base rate and 0.25 for a coin flip. That is a small edge, and I present it as small:
90-day returns are hard to predict. Grounding in real runs: claims that the filings do not support get removed, and
nonsense questions get refused. And 53 unit tests cover the logic that keeps it honest.

## 11. Safety, reliability and oversight (0:45)

Safety is enforced in code, not just requested in prompts. Every search is filtered by company and date. Code checks
every citation and every number before the critic even sees a claim. Retrieved text is treated as data, so instructions
hidden in a filing are ignored, and the debate can move a forecast by at most 15 points. When evidence is weak or a model
call fails, the answer is insufficient evidence, never a guess. Every loop has a budget, and every run is saved as a
trace. The Traces tab replays any decision step by step. And the decisions stay with the person: the system never
trades, never closes a position and never activates a rule on its own.

## 12. Repository and next steps (0:45)

Everything is in the public GitHub repository on screen. The README explains the problem, the architecture and every
agent, and how to set it up and build the data. A user manual walks through every screen. Because building the data
takes hours, the examples folder includes real daily reports, full run traces and the evaluation results, so you can
review it without running it. To sum up: the strengths are grounded research that refuses rather than guesses, and a
traceable decision path. The limits are a small forecasting edge, survivorship bias in the backtest, and no live track
record until February. Next I would add index membership history, grade live forecasts as they resolve, track cost per
run, and grow the test set from real failures. Thank you.

---

## Optional: 90-second elevator pitch (about 210 words)

Individual investors have the same public information as professionals, the SEC filings, but no analyst team to read
them, and chatbots are unsafe here: they invent numbers and sound confident when they're wrong.

I built the Stock Research Agent, an autonomous system of eight cooperating agents. Every night it screens the S&P 500
and indexes new filings. On demand, it researches the top candidates only from each company's own filings, with a
citation for every claim, and a critic deletes anything the filings don't support. Bull and bear analysts then debate
each stock, and a forecaster turns a calibrated statistical model plus that debate into one number: the probability the
stock beats the S&P 500 over the next 90 days.

It doesn't stop there. It tracks the positions it would have taken, flags when the reason for holding one has changed,
grades its own forecasts, and proposes corrections, which only take effect when a person approves them.

The results are honest: retrieval finds the right passage first 72 percent of the time, wrong-company evidence dropped to
zero, and the forecasts carry a small but real edge over the base rate. It never trades; the person always decides.

The code, a user manual and real outputs are on GitHub.

---

## Recording checklist

- Slides visible the whole time; share the deck in Present mode.
- Slides 4 and 6 reveal their diagrams step by step: click at each **[click]** in the script (5 clicks on slide 4, 4 on slide 6).
- Say the GitHub link out loud on slide 12 and keep it on screen for a few seconds.
- For a version with a 3-minute live app demo, use `Demo_Script.md` instead.
- After uploading (YouTube unlisted, Vimeo or Loom), check the video plays signed out and the audio is clear.
