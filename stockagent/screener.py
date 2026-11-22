"""Screener: score the universe as of a date and pick the daily shortlist.

Deterministic code, no LLM: screening is ranking numbers, which code does exactly and cheaply.

Stage A (every universe member, prices only): 6-month return vs the S&P 500, plus discovery
triggers for unusual price moves and volume spikes.
Stage B (the best stage-A names, after their fundamentals and earnings are loaded): value
(earnings yield) and the latest earnings surprise.
Score = mean of the available signal percentiles (equal weights: an untuned starting point,
to be validated by backtests). Triggered names join the shortlist even if they score lower, so a
sudden event is researched rather than missed.
"""
import json
import math
from datetime import date, timedelta

import config
from stockagent import market


def _percentiles(values: dict[str, float | None]) -> dict[str, float]:
    """Percentile rank (0-1) of each non-missing value among the others."""
    present = sorted((v, t) for t, v in values.items() if v is not None)
    if len(present) < 2:
        return {t: 0.5 for _, t in present}
    return {t: i / (len(present) - 1) for i, (_, t) in enumerate(present)}


def _history(conn, ticker: str, as_of: str, days: int = 200) -> list[tuple[str, float, int]]:
    start = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    return [(r["date"], r["close"], r["volume"] or 0) for r in conn.execute(
        "SELECT date, close, volume FROM prices_daily WHERE ticker = ? AND date > ? AND date <= ? ORDER BY date",
        (ticker, start, as_of))]


def stage_a(conn, as_of: str, tickers: list[str]) -> dict[str, dict]:
    """Price signals and price/volume triggers for every ticker."""
    out = {}
    bench = market.total_return(config.BENCHMARK, (date.fromisoformat(as_of) - timedelta(days=182)).isoformat(),
                                as_of, conn)
    for t in tickers:
        hist = _history(conn, t, as_of)
        # Needs enough days for the volatility baseline AND a price at the start of the 6-month window
        # (a recent listing or spin-off can have the first without the second).
        try:
            if len(hist) < config.SCREENER_MIN_HISTORY_DAYS:
                raise market.DataUnavailable("thin history")
            momentum = market.momentum(t, as_of, 182, conn) - bench
        except market.DataUnavailable:
            out[t] = {"momentum": None, "triggers": ["needs_review:new_or_thin_history"]}
            continue
        closes = [c for _, c, _ in hist]
        vols = [v for _, _, v in hist]
        triggers = []
        rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
        base = rets[-66:-5]                       # ~3 months of daily returns before the last 5 days
        mean = sum(base) / len(base)
        daily_sd = (sum((r - mean) ** 2 for r in base) / (len(base) - 1)) ** 0.5
        move5 = closes[-1] / closes[-6] - 1
        if daily_sd > 0 and abs(move5) / (daily_sd * math.sqrt(5)) > 2.5:
            triggers.append(f"price_move_5d({move5:+.1%})")
        base_vol = sum(vols[-65:-5]) / 60
        if base_vol > 0 and sum(vols[-5:]) / 5 > 3 * base_vol:
            triggers.append(f"volume_spike({sum(vols[-5:]) / 5 / base_vol:.1f}x)")
        out[t] = {"momentum": momentum, "triggers": triggers}
    return out


def candidates(signals_a: dict[str, dict]) -> list[str]:
    """Best momentum names, plus anything triggered, plus the core companies (always tracked)."""
    ranked = sorted((s["momentum"], t) for t, s in signals_a.items() if s["momentum"] is not None)
    top = [t for _, t in ranked[::-1][:config.SCREENER_CANDIDATES]]
    triggered = [t for t, s in signals_a.items() if s["triggers"]]
    return list(dict.fromkeys(top + triggered + [t for t in config.TICKERS if t in signals_a]))


def stage_b(conn, as_of: str, tickers: list[str]) -> dict[str, dict]:
    """Value and earnings-surprise signals, plus an earnings trigger, for stage-A candidates."""
    out = {}
    for t in tickers:
        sig = {"earnings_yield": None, "surprise": None, "triggers": []}
        try:
            sig["earnings_yield"] = 1 / market.pe_ratio(t, as_of, conn)
        except market.DataUnavailable:
            pass
        try:
            e = market.earnings_surprise(t, as_of, conn)
            age = (date.fromisoformat(as_of) - date.fromisoformat(e["date"])).days
            s = e["surprise_pct"]
            if s is not None and age <= config.EARNINGS_FRESH_DAYS and abs(s) <= config.MAX_CREDIBLE_SURPRISE:
                sig["surprise"] = s
                if age <= 10 and abs(s) >= 10:
                    sig["triggers"].append(f"earnings_surprise({s:+.1f}%)")
        except market.DataUnavailable:
            pass
        out[t] = sig
    return out


def score(as_of: str, signals_a: dict[str, dict], signals_b: dict[str, dict]) -> list[dict]:
    """Combine signals into ranked rows; mark the shortlist."""
    names = list(signals_b)
    mom = _percentiles({t: signals_a[t]["momentum"] for t in names})
    val = _percentiles({t: signals_b[t]["earnings_yield"] for t in names})
    sur = _percentiles({t: signals_b[t]["surprise"] for t in names})
    rows = []
    for t in names:
        parts = [p for p in (mom.get(t), val.get(t), sur.get(t)) if p is not None]
        triggers = signals_a[t]["triggers"] + signals_b[t]["triggers"]
        rows.append({
            "as_of": as_of, "ticker": t,
            "score": sum(parts) / len(parts) if len(parts) >= 2 else None,   # need 2+ signals to be scored
            "momentum_pct": mom.get(t), "value_pct": val.get(t), "surprise_pct": sur.get(t),
            "signals": {"excess_return_6m": signals_a[t]["momentum"],
                        "earnings_yield": signals_b[t]["earnings_yield"],
                        "eps_surprise_pct": signals_b[t]["surprise"]},
            "triggers": triggers, "shortlisted": False, "reason": "",
        })
    scored = sorted((r for r in rows if r["score"] is not None), key=lambda r: -r["score"])
    for r in scored[:config.SHORTLIST_SIZE]:
        r["shortlisted"], r["reason"] = True, f"top {config.SHORTLIST_SIZE} score"
    for r in rows:
        if any(x.startswith("needs_review") for x in r["triggers"]):
            # Human review wins over any trigger: too little history to judge automatically.
            r["shortlisted"], r["reason"] = False, "human review: too little price history to score"
            continue
        if r["triggers"] and not r["shortlisted"]:
            r["shortlisted"], r["reason"] = True, "trigger: " + ", ".join(r["triggers"])
    return sorted(rows, key=lambda r: (not r["shortlisted"], -(r["score"] or 0)))


def save(conn, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO screener_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(r["as_of"], r["ticker"], r["score"], r["momentum_pct"], r["value_pct"], r["surprise_pct"],
          json.dumps(r["signals"]), ",".join(r["triggers"]), int(r["shortlisted"]), r["reason"]) for r in rows])
    conn.commit()
