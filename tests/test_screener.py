"""Tests for the screener on a synthetic database (no network)."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import screener  # noqa: E402
from stockagent.db import connect  # noqa: E402

AS_OF = "2026-06-30"


def _series(conn, ticker, daily_growth, days=200, volume=1000, last5_volume=None, last5_jump=0.0):
    """Insert `days` daily closes ending on AS_OF, growing by daily_growth (alternating noise)."""
    end = date.fromisoformat(AS_OF)
    price, rows = 100.0, []
    for i in range(days):
        d = (end - timedelta(days=days - 1 - i)).isoformat()
        price *= 1 + daily_growth + (0.004 if i % 2 else -0.004)
        if last5_jump and i == days - 5:
            price *= 1 + last5_jump
        vol = last5_volume if (last5_volume and i >= days - 5) else volume
        rows.append((ticker, d, price, vol))
    conn.executemany("INSERT INTO prices_daily (ticker, date, close, volume) VALUES (?, ?, ?, ?)", rows)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TICKERS", {})          # no core names in the synthetic universe
    monkeypatch.setattr(config, "SHORTLIST_SIZE", 1)
    c = connect(tmp_path / "s.db")
    _series(c, "SPY", 0.0005)
    _series(c, "FAST", 0.003)                            # strong momentum
    _series(c, "SLOW", -0.001)                           # weak momentum
    _series(c, "JUMP", 0.0, last5_jump=0.30)             # flat, then +30% in the last 5 days
    _series(c, "VOL", 0.0, last5_volume=10_000)          # volume 10x normal in the last 5 days
    _series(c, "NEW", 0.001, days=40)                    # recent listing
    _series(c, "SPIN", 0.001, days=170)                  # enough days for volatility, but no price 182 days ago
    filed = "2026-02-01"
    c.executemany("INSERT INTO financials VALUES (?, 'eps_diluted', 'FY', '2025-12-31', ?, '10-K', ?, 'USD', 't')",
                  [("FAST", filed, 1.0), ("SLOW", filed, 20.0), ("JUMP", filed, 5.0), ("VOL", filed, 5.0)])
    c.executemany("INSERT INTO earnings_events VALUES (?, ?, 1.0, ?, ?)", [
        ("FAST", "2026-05-01", 1.1, 10.0),
        ("SLOW", "2026-05-01", 0.9, -10.0),
        ("JUMP", "2026-06-26", 1.5, 50.0),               # recent big beat -> trigger
        ("VOL", "2026-05-01", 3.0, 200.0),               # implausible surprise -> ignored
    ])
    return c


def test_stage_a_signals_and_triggers(conn):
    sig = screener.stage_a(conn, AS_OF, ["FAST", "SLOW", "JUMP", "VOL", "NEW", "SPIN"])
    assert sig["SPIN"]["triggers"] == ["needs_review:new_or_thin_history"]
    assert sig["FAST"]["momentum"] > sig["SLOW"]["momentum"]
    assert any(t.startswith("price_move_5d") for t in sig["JUMP"]["triggers"])
    assert any(t.startswith("volume_spike") for t in sig["VOL"]["triggers"])
    assert sig["NEW"]["momentum"] is None and sig["NEW"]["triggers"] == ["needs_review:new_or_thin_history"]


def test_stage_b_surprise_rules(conn):
    sig = screener.stage_b(conn, AS_OF, ["FAST", "JUMP", "VOL"])
    assert sig["FAST"]["surprise"] == 10.0
    assert sig["JUMP"]["triggers"] == ["earnings_surprise(+50.0%)"]
    assert sig["VOL"]["surprise"] is None                # above MAX_CREDIBLE_SURPRISE


def test_shortlist_has_top_score_and_triggered_but_not_new_listing(conn):
    names = ["FAST", "SLOW", "JUMP", "VOL", "NEW"]
    a = screener.stage_a(conn, AS_OF, names)
    rows = screener.score(AS_OF, a, screener.stage_b(conn, AS_OF, screener.candidates(a)))
    by = {r["ticker"]: r for r in rows}
    shortlisted = {r["ticker"] for r in rows if r["shortlisted"]}
    assert by["FAST"]["score"] > by["SLOW"]["score"]
    assert "JUMP" in shortlisted and "VOL" in shortlisted   # triggers bypass the score cut
    assert "NEW" not in shortlisted and by["NEW"]["reason"].startswith("human review")
    assert len([r for r in rows if r["reason"] == "top 1 score"]) == 1


def test_review_flag_beats_trigger(conn):
    # A thin-history name with a big recent earnings beat still goes to human review.
    conn.execute("INSERT INTO earnings_events VALUES ('NEW', '2026-06-27', 1.0, 1.4, 40.0)")
    a = screener.stage_a(conn, AS_OF, ["FAST", "SLOW", "NEW"])
    rows = {r["ticker"]: r for r in screener.score(AS_OF, a, screener.stage_b(conn, AS_OF, screener.candidates(a)))}
    assert not rows["NEW"]["shortlisted"] and rows["NEW"]["reason"].startswith("human review")
