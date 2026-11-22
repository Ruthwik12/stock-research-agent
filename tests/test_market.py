"""Tests for the market-data tools on a small synthetic database (no network)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import market  # noqa: E402
from stockagent.db import connect  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    c.executemany("INSERT INTO prices_daily (ticker, date, close) VALUES (?, ?, ?)", [
        ("AAA", "2026-01-02", 100.0), ("AAA", "2026-04-01", 110.0), ("AAA", "2026-07-01", 121.0),
        ("SPY", "2026-01-02", 500.0), ("SPY", "2026-04-01", 525.0), ("SPY", "2026-07-01", 550.0),
    ])
    c.executemany("INSERT INTO financials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        # FY2025 EPS first filed in February, then restated in a filing made in May.
        ("AAA", "eps_diluted", "FY", "2025-12-31", "2026-02-10", "10-K", 5.0, "USD/shares", "t"),
        ("AAA", "eps_diluted", "FY", "2025-12-31", "2026-05-05", "10-K/A", 5.5, "USD/shares", "t"),
    ])
    c.executemany("INSERT INTO earnings_events VALUES (?, ?, ?, ?, ?)", [
        ("AAA", "2026-01-28", 1.0, 1.2, 20.0),
        ("AAA", "2026-04-29", 1.1, None, None),     # announcement known in advance, not yet reported
    ])
    return c


def test_price_uses_last_trading_day_on_or_before(conn):
    assert market.price_on("AAA", "2026-03-15", conn) == ("2026-01-02", 100.0)


def test_excess_return_vs_benchmark(conn):
    # AAA +10%, SPY +5% from Jan 2 to Apr 1
    assert market.excess_return("AAA", "2026-01-02", "2026-04-01", conn) == pytest.approx(0.05)


def test_fundamentals_respect_filed_date(conn):
    # Before the restatement was filed, only the original 5.0 was public.
    assert market.latest_fundamental("AAA", "eps_diluted", "2026-03-01", conn=conn)["value"] == 5.0
    assert market.latest_fundamental("AAA", "eps_diluted", "2026-06-01", conn=conn)["value"] == 5.5
    with pytest.raises(market.DataUnavailable):
        market.latest_fundamental("AAA", "eps_diluted", "2026-01-15", conn=conn)


def test_pe_ratio_as_of(conn):
    assert market.pe_ratio("AAA", "2026-04-01", conn) == pytest.approx(110.0 / 5.0)


def test_earnings_surprise_ignores_unreported(conn):
    assert market.earnings_surprise("AAA", "2026-06-01", conn)["date"] == "2026-01-28"


def test_brier_and_calibration():
    assert market.brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert market.brier_score([0.7, 0.7], [1, 0]) == pytest.approx((0.09 + 0.49) / 2)
    table = market.calibration_table([0.62, 0.68, 0.31], [1, 0, 0])
    assert [(r["bucket"], r["n"], r["hit_rate"]) for r in table] == [("30%-40%", 1, 0.0), ("60%-70%", 2, 0.5)]
