# Capstone Checkpoint: Multi-Agent Architecture for the Stock Research Agent

Nov 8, 2026 · Ruthwik

## 1. Problem and why multiple agents

My agent gives active investors a daily, ranked list of stocks with a calibrated probability of beating the S&P 500 over 90 days, each backed by cited filings. It is decision support, not financial advice.

A single agent fails here for three reasons. The work needs different skills (screening hundreds of stocks, reading filings, quantitative forecasting). One agent arguing both sides drifts toward one story and overconfidence. And one context window cannot hold a whole universe of filings. Separate agents give focused context, an adversarial bull/bear check, and an independent critic.

## 2. Agents and roles

The system has **nine agents**. I kept one agent per distinct judgment and moved anything without judgment (ratios, momentum, Brier scores) into deterministic tools. Bull and bear stay separate so each argues one side fully. Separate news or macro agents would add coordination cost without new judgment, so the researcher covers them.

| Agent | Responsibility |
| --- | --- |
| Screener | Filters the universe daily to a shortlist using price, fundamentals and earnings signals |
| Researcher | Retrieves cited evidence from filings and news, filtered by ticker and date |
| Bull analyst | Builds the strongest cited case for outperformance |
| Bear analyst | Builds the strongest cited case against |
| Critic | Checks every claim against its citation, scores the theses, sends weak work back |
| Forecaster | Turns signals and surviving theses into a calibrated probability |
| Ranker / reporter | Ranks picks and writes the report with thesis, risks and sources |
| Sell monitor | Reviews open picks daily against thesis, target and stop-loss |
| Reflector | Scores resolved predictions and records lessons and correction rules |

## 3. Coordination strategy

Coordination is **hybrid and graph-based**, run by one LangGraph controller:

- **Sequential spine:** screener, then researcher, then the debate, then forecaster, then ranker.
- **Parallel fan-out:** each shortlisted stock runs through the spine independently.
- **Iterative debate:** bull and bear generate competing theses, and the critic scores and prunes them (the Tree-of-Thought beam from the previous checkpoint). The critic can send a thesis back to the researcher for missing evidence, at most twice.
- **Scheduled branches:** the sell monitor runs daily on open picks; the reflector runs when a 90-day prediction resolves.

## 4. Communication

Agents do not chat freely. They read and write a **shared, typed state**: each output is a validated structure, and every claim carries the IDs of the passages it cites.

- **One-way handoffs** along the spine keep the daily run efficient.
- **Two-way validation** links the critic with the bull, bear and researcher: rejected claims return with reasons.
- **Brainstorming** happens only in the debate, where bull and bear produce several competing theses.
- **Long-term memory** (a predictions database) carries lessons from the reflector to the critic and forecaster on later runs.

## 5. Trade-offs and scalability

| Decision | Gain | Cost | How I manage it |
| --- | --- | --- | --- |
| Critic feedback loops | Reliability: unsupported claims removed | Latency and LLM cost | At most 2 send-backs; 15-call budget per stock |
| Nine agents | Focused roles, adversarial check | Complexity | One controller, typed state, math in code |
| Graph instead of a simple chain | Parallel stocks, loops, scheduled branches | Coordination overhead; harder to debug | Every run's state is logged and replayable |

Failures stay contained: an agent that cannot find evidence returns "insufficient evidence" and the probability stays at the 50% base rate. The design scales by stocks rather than by agents: a larger universe adds parallel runs of the same graph, and a new capability is a new node. Over time, the reflector's lessons make the critic and forecaster better calibrated.
