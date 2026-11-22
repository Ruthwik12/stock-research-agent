# Presentation script (8–10 minutes) and elevator pitch

Deck: "Stock Research Agent: Final Capstone" (10 slides). The same text is in each slide's speaker notes.
Target pace: about 140 words per minute. Total about 9 minutes 55 seconds; trim the evolution slide first if you run long.


| # | Slide | Time | Cumulative |
|---|---|---|---|
| 1 | Cover | 0:30 | 0:30 |
| 2 | The problem and the user | 1:00 | 1:30 |
| 3 | Goal and scope | 0:50 | 2:20 |
| 4 | Architecture | 1:10 | 3:30 |
| 5 | The eight agents | 1:15 | 4:45 |
| 6 | The life of one pick | 1:10 | 5:55 |
| 7 | Design evolution | 1:10 | 7:05 |
| 8 | Evaluation and results | 1:10 | 8:15 |
| 9 | Safety, reliability and oversight | 0:50 | 9:05 |
| 10 | Repository and next steps | 0:50 | 9:55 |

---

## 1. Cover (0:30)

Hi, I'm Ruthwik. This is my capstone: the Stock Research Agent. In the next nine minutes I'll cover the problem and the
user, how the system works end to end, the design decisions I made across the program, what the evaluation shows, and
where the code lives. One line to keep in mind throughout: this is decision support with paper trading only. The system
never places a trade.

## 2. The problem and the user (1:00)

The user is an individual investor managing their own portfolio. They have the same information as everyone else,
because filings are public, but no analyst team to read it. Around 500 companies, each filing annual, quarterly and
event reports, plus prices, insider trades, macro data and news. The obvious shortcut, asking a chatbot, is unsafe for
money decisions: it answers from stale memory, confuses companies, invents numbers and sounds just as confident when it
is wrong. So the user needs four things: a daily shortlist, answers grounded in the actual filings with citations, an
honest probability rather than an opinion, and a system that keeps watching and learning while leaving the decisions to
them.

## 3. Goal and scope (0:50)

I framed the goal as a measurable question: what is the probability this stock's total return beats the S&P 500 over the
next 90 days? That turns vague advice into something I can grade. Success has five parts: every claim grounded in a
passage with no wrong-company or future data; retrieval that finds the answering passage in the top five for at least 90
percent of test questions; calibration, meaning a Brier score below the 0.25 of a coin flip and no worse than the base
rate; honest failure, saying insufficient evidence instead of guessing; and a person taking every consequential action.
The boundaries keep it safe and affordable: S&P 500 only, paper trading only, free public data, one machine, and bounded
model cost.

## 4. Architecture (1:10)

Here is the whole system on one page. A nightly job, scheduled at 11:55 PM, pulls new SEC filings, prices, insider trades
from Form 4, macro data from FRED and trusted news. It indexes new text and screens all 500 stocks. Data lives in two
stores on purpose: exact numbers in SQLite, always computed by code, never by the model; and filing passages in a Chroma
vector index for the researcher to search. Every tool takes an as-of date, so the system never sees information that was
not public yet, which keeps backtests honest. In the middle are eight agents built with LangGraph and plain Python. They
call Claude only for language steps, and every call must return a validated schema. FastAPI and React put the user at the
end, where the decisions are.

## 5. The eight agents (1:15)

Eight agents, each with one job. The key design choice is where the language model is used. Screening, ranking,
arithmetic and learning are plain code, so they cannot hallucinate. The screener works on numbers only: daily prices
and volume from Yahoo Finance for momentum and unusual-move triggers, annual EPS from the SEC's structured XBRL data for
earnings yield, and analysts' estimates versus reported EPS from Yahoo for earnings surprises. It never reads filing text;
that comes later, only for the stocks it shortlists. The model is used only where judgment on text is
needed: the researcher writing claims, the critic judging them, the bull and bear analysts, and the sell monitor's thesis
check. The critic sits on both sides: it checks the researcher's claims, and it scores the debate theses, sending
promising but thinly supported ones back to the researcher for more evidence. The forecaster combines a calibrated
statistical model with a small, capped nudge from the debate. The reflector is pure code: it can only report patterns
that really exist in the outcomes.

## 6. The life of one pick (1:10)

This is the life of a single pick. The screener finds it, the researcher builds cited claims, the critic removes anything
the filings do not support. Then the bull and bear analysts write three theses each. This is where I used
Tree-of-Thought: the debate is a beam search. The critic scores each thesis, prunes weak ones, but always keeps the best
bull and the best bear, so a thinly evidenced but correct argument survives. Promising theses with thin evidence get sent
back to the researcher for one more search, at most two rounds. The forecaster turns the surviving theses into a small,
capped adjustment on top of a calibrated model. Then the loop closes: positions are monitored, graded after 90 days, and
the reflector proposes corrections that only take effect when I approve them.

## 7. Design evolution (1:10)

The design grew one layer per module. Module 1 framed the task as a gradable probability. Module 2 gave the reasoning
loop, tools and memory; the instructor suggested a self-correction loop, which became the reflector. Module 3 added
retrieval; my failure demo showed an unfiltered Apple query returning five Microsoft passages, so ticker and date filters
became mandatory. Module 4 placed Tree-of-Thought only in the thesis stage. Module 5 designed the agent team, and Module 6
the guardrails and human triggers. Building it corrected the design three times, in orange: the first forecaster had too
little history and collapsed to the base rate, so I retrained it walk-forward on ten years; vector search could not find
the latest quarter, so I added a reranker, recency and keyword search; and two correction rules that looked good on 16
months failed an 8.5-year re-test and were retired.

## 8. Evaluation and results (1:10)

How do I know it works? Four kinds of evidence. Retrieval: on an 18-question test set with rule-defined answers, the
right passage comes first 72 percent of the time, up from 44 with vector search alone, and it is in the top five 94
percent of the time. Isolation: with filters, zero wrong-company or future passages, where the unfiltered search
returned five of five from Microsoft. Forecasting: over 44,660 walk-forward forecasts from 2018 to 2026, the Brier score
is 0.2487, versus 0.2493 for the base rate and 0.25 for a coin flip. That is a small edge, and I present it as small:
90-day returns are hard to predict. Grounding in real runs: claims that the filings do not support get removed, and
nonsense questions get refused. And 53 unit tests cover the logic that keeps it honest.

## 9. Safety, reliability and oversight (0:50)

Safety is enforced in code, not just requested in prompts. Every search is filtered by company and date. Code checks
every citation and every number before the critic even sees a claim. Retrieved text is treated as data, so instructions
hidden in a filing are ignored, and the debate can move a forecast by at most 15 points. When evidence is weak or a model
call fails, the answer is insufficient evidence, never a guess. Every loop has a budget, and every run is saved as a
trace. The Traces tab replays any decision step by step. And the decisions stay with the person: the system never
trades, never closes a position and never activates a rule on its own.

## 10. Repository and next steps (0:50)

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
- Say the GitHub link out loud on slide 10 and keep it on screen for a few seconds.
- Optional 20–30 second demo instead of part of slide 6: open the Traces tab and click through one pick (time it so the
  total stays under 10 minutes).
- After uploading (YouTube unlisted, Vimeo or Loom), check the video plays signed out and the audio is clear.
