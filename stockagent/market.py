"""Market-data and calculator tools for the agents.

Every function takes an `as_of` date (YYYY-MM-DD) and uses only data public on or before it:
prices up to that day, financials whose SEC `filed` date is on or before it, earnings already
announced. That keeps backtests free of look-ahead bias. The arithmetic is done here in code,
never by the LLM.
"""
import math
import sqlite3
from datetime import date, timedelta

import config
from stockagent.db import connect


class DataUnavailable(LookupError):
    """Raised when the database has no data for the request; callers fall back to 'insufficient evidence'."""


def _conn(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    return conn or connect()


def price_on(ticker: str, as_of: str, conn=None) -> tuple[str, float]:
    """Last adjusted close on or before as_of, with its trading date."""
    row = _conn(conn).execute(
        "SELECT date, close FROM prices_daily WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (ticker, as_of)).fetchone()
    if not row:
        raise DataUnavailable(f"no price for {ticker} on or before {as_of}")
    return row["date"], row["close"]


def total_return(ticker: str, start: str, end: str, conn=None) -> float:
    """Total return (adjusted for dividends and splits) from the close on/before start to the close on/before end."""
    _, p0 = price_on(ticker, start, conn)
    _, p1 = price_on(ticker, end, conn)
    return p1 / p0 - 1


def excess_return(ticker: str, start: str, end: str, conn=None) -> float:
    """Stock total return minus the benchmark's (S&P 500 fund) over the same window."""
    return total_return(ticker, start, end, conn) - total_return(config.BENCHMARK, start, end, conn)


def momentum(ticker: str, as_of: str, days: int = 182, conn=None) -> float:
    """Return over the trailing `days` calendar days ending at as_of (182 is about 6 months)."""
    start = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    return total_return(ticker, start, as_of, conn)


def volatility(ticker: str, as_of: str, days: int = 90, conn=None) -> float:
    """Annualized standard deviation of daily returns over the trailing `days` calendar days."""
    start = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    closes = [r["close"] for r in _conn(conn).execute(
        "SELECT close FROM prices_daily WHERE ticker = ? AND date > ? AND date <= ? ORDER BY date",
        (ticker, start, as_of))]
    if len(closes) < 20:
        raise DataUnavailable(f"too few prices for {ticker} volatility before {as_of}")
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    mean = sum(rets) / len(rets)
    return math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)


def latest_fundamental(ticker: str, metric: str, as_of: str, period_type: str = "FY", conn=None) -> dict:
    """Most recent period's value as known on as_of: the latest version filed on or before as_of."""
    row = _conn(conn).execute(
        """SELECT period_end, filed, form, value FROM financials
           WHERE ticker = ? AND metric = ? AND period_type = ? AND filed <= ?
           ORDER BY period_end DESC, filed DESC LIMIT 1""",
        (ticker, metric, period_type, as_of)).fetchone()
    if not row:
        raise DataUnavailable(f"no {period_type} {metric} for {ticker} filed by {as_of}")
    return dict(row)


def pe_ratio(ticker: str, as_of: str, conn=None) -> float:
    """Price on as_of divided by the latest annual diluted EPS filed by as_of."""
    _, price = price_on(ticker, as_of, conn)
    eps = latest_fundamental(ticker, "eps_diluted", as_of, "FY", conn)["value"]
    if eps <= 0:
        raise DataUnavailable(f"{ticker} annual EPS is not positive; P/E undefined")
    return price / eps


def earnings_surprise(ticker: str, as_of: str, conn=None) -> dict:
    """Latest earnings announcement on or before as_of that has a reported EPS."""
    row = _conn(conn).execute(
        """SELECT date, eps_estimate, eps_reported, surprise_pct FROM earnings_events
           WHERE ticker = ? AND date <= ? AND eps_reported IS NOT NULL ORDER BY date DESC LIMIT 1""",
        (ticker, as_of)).fetchone()
    if not row:
        raise DataUnavailable(f"no reported earnings for {ticker} by {as_of}")
    return dict(row)


def brier_score(probabilities: list[float], outcomes: list[int]) -> float:
    """Mean of (p - outcome)^2. 0 is perfect; always answering 0.5 scores 0.25."""
    if len(probabilities) != len(outcomes) or not outcomes:
        raise ValueError("need equal, non-empty lists")
    return sum((p - o) ** 2 for p, o in zip(probabilities, outcomes)) / len(outcomes)


def calibration_table(probabilities: list[float], outcomes: list[int], bucket: float = 0.1) -> list[dict]:
    """Group forecasts into probability buckets; compare mean forecast with actual hit rate in each."""
    groups: dict[int, list[tuple[float, int]]] = {}
    for p, o in zip(probabilities, outcomes):
        groups.setdefault(min(int(p / bucket), int(1 / bucket) - 1), []).append((p, o))
    return [{"bucket": f"{k * bucket:.0%}-{(k + 1) * bucket:.0%}", "n": len(v),
             "mean_forecast": sum(p for p, _ in v) / len(v), "hit_rate": sum(o for _, o in v) / len(v)}
            for k, v in sorted(groups.items())]
