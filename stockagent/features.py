"""Price-based features and 90-day outcome labels, shared by model training and live forecasts.

Everything uses closes on or before the as-of date (features) or the horizon end (label), so
training and live forecasts compute features with exactly the same code.
"""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config

FEATURES = ["excess_6m", "excess_1m", "volatility_3m"]   # model inputs, in this order
HORIZON_DAYS = 90


def load_closes(conn, tickers: list[str]) -> pd.DataFrame:
    """Wide frame of adjusted closes: one column per ticker, indexed by trading date."""
    marks = ",".join("?" * len(tickers))
    df = pd.read_sql_query(f"SELECT ticker, date, close FROM prices_daily WHERE ticker IN ({marks})",
                           conn, params=tickers, parse_dates=["date"])
    return df.pivot(index="date", columns="ticker", values="close").sort_index()


def _ret(s: pd.Series, start: str, end: str) -> float | None:
    """Return from the close on/before start to the close on/before end; None if start predates the data."""
    s = s.dropna()
    if s.empty or s.index[0] > pd.Timestamp(start):
        return None
    p0, p1 = s.asof(pd.Timestamp(start)), s.asof(pd.Timestamp(end))
    return None if pd.isna(p0) or pd.isna(p1) or p0 <= 0 else p1 / p0 - 1


def _shift(as_of: str, days: int) -> str:
    return (date.fromisoformat(as_of) + timedelta(days=days)).isoformat()


def features_at(closes: pd.DataFrame, ticker: str, as_of: str) -> dict | None:
    """Feature values for one stock on one date, or None if there is too little history."""
    s, bench = closes[ticker], closes[config.BENCHMARK]
    r6, b6 = _ret(s, _shift(as_of, -182), as_of), _ret(bench, _shift(as_of, -182), as_of)
    r1, b1 = _ret(s, _shift(as_of, -30), as_of), _ret(bench, _shift(as_of, -30), as_of)
    window = s.dropna().loc[pd.Timestamp(_shift(as_of, -90)):pd.Timestamp(as_of)]
    if None in (r6, b6, r1, b1) or len(window) < 40:
        return None
    vol = window.pct_change().dropna().std() * math.sqrt(252)
    return {"excess_6m": r6 - b6, "excess_1m": r1 - b1, "volatility_3m": float(vol)}


def sample_dates(first: str, last: str) -> list[str]:
    """The 15th of every month from first to last (training and backtest sample dates)."""
    d, out = date.fromisoformat(first), []
    while d.isoformat() <= last:
        out.append(d.isoformat())
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=15)
    return out


def excess_6m(conn, ticker: str, as_of: str) -> float | None:
    """6-month return vs the benchmark as of a date (None with too little history)."""
    f = features_at(load_closes(conn, [ticker, config.BENCHMARK]), ticker, as_of)
    return f["excess_6m"] if f else None


def relative_to_date(dates: list[str], X: np.ndarray) -> np.ndarray:
    """Each feature minus that date's median across stocks: the model then ranks stocks within a date and
    makes no market-wide call (that part showed no out-of-sample skill)."""
    dates, out = np.asarray(dates), X.astype(float).copy()
    for d in np.unique(dates):
        m = dates == d
        out[m] -= np.median(X[m], axis=0)
    return out


_MEDIANS: dict[tuple[str, str], tuple[float, ...]] = {}


def universe_medians(conn, as_of: str) -> tuple[float, ...]:
    """Median of each feature across the S&P 500 members on as_of (live forecasts use the training centering).
    Cached per database and date: a daily run forecasts several stocks on one date."""
    key = (conn.execute("PRAGMA database_list").fetchone()[2], as_of)
    if key not in _MEDIANS:
        members = [r[0] for r in conn.execute(
            "SELECT ticker FROM universe WHERE date_added IS NULL OR date_added <= ?", (as_of,))]
        closes = load_closes(conn, sorted(set(members) | {config.BENCHMARK}))
        rows = [[f[k] for k in FEATURES] for t in members if t in closes and (f := features_at(closes, t, as_of))]
        _MEDIANS[key] = tuple(np.median(np.array(rows), axis=0)) if rows else (0.0,) * len(FEATURES)
    return _MEDIANS[key]


def build_samples(conn, dates: list[str]) -> tuple[list[tuple[str, str]], list[dict], list[tuple[int, float]]]:
    """(date, ticker) keys, features and labels for every universe stock that was in the S&P 500 on each
    date (date_added on or before it). Stocks removed from the index are missing: survivorship bias remains."""
    added = dict(conn.execute("SELECT ticker, date_added FROM universe").fetchall())
    closes = load_closes(conn, sorted(set(added) | {config.BENCHMARK}))
    keys, feats, labels = [], [], []
    for d in dates:
        for t in added:
            if t == config.BENCHMARK or t not in closes or (added[t] and added[t] > d):
                continue
            f, lab = features_at(closes, t, d), label_at(closes, t, d)
            if f and lab:
                keys.append((d, t)); feats.append(f); labels.append(lab)
    return keys, feats, labels


def label_at(closes: pd.DataFrame, ticker: str, as_of: str) -> tuple[int, float] | None:
    """(1 if the stock beat the benchmark over the next HORIZON_DAYS else 0, excess return); None if not yet known."""
    end = _shift(as_of, HORIZON_DAYS)
    if closes.index.max() < pd.Timestamp(end):
        return None
    r, b = _ret(closes[ticker], as_of, end), _ret(closes[config.BENCHMARK], as_of, end)
    if r is None or b is None:
        return None
    return int(r > b), r - b
