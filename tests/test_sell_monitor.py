"""Tests for the sell monitor on a synthetic database (no network, no index, scripted LLM)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stockagent import sell_monitor  # noqa: E402
from stockagent.db import connect  # noqa: E402

THESIS = {"side": "bull", "text": "Demand is broadening", "score": 7.0, "evidence_ids": ["E1"]}


@pytest.fixture
def db(tmp_path, monkeypatch):
    conn = connect(tmp_path / "m.db")
    conn.executemany("INSERT INTO prices_daily (ticker, date, close) VALUES (?, ?, ?)", [
        ("SPY", "2026-01-02", 100.0), ("SPY", "2026-02-02", 110.0), ("SPY", "2026-04-15", 110.0),
        ("WIN", "2026-01-02", 50.0), ("WIN", "2026-02-02", 60.0), ("WIN", "2026-04-15", 60.0),
        ("LOSE", "2026-01-02", 50.0), ("LOSE", "2026-02-02", 49.0), ("LOSE", "2026-04-15", 49.0),
        ("MEH", "2026-01-02", 50.0)])
    picks = [("WIN", 1, 0.56), ("LOSE", 2, 0.53), ("MEH", 3, 0.49), ("FLAT", 4, 0.503)]   # FLAT = base rate
    conn.executemany("INSERT INTO daily_picks VALUES (1, ?, ?, ?, ?, 0, 0, ?)", [
        (rank, t, rank, p, json.dumps({"ticker": t, "as_of": "2026-01-02", "p_final": p, "p_quant": 0.503,
                                       "prediction_id": rank, "thesis": THESIS})) for t, rank, p in picks])
    monkeypatch.setattr(sell_monitor, "_has_new_filing", lambda pos: pos["ticker"] == "WIN")
    return conn


def test_opens_only_picks_above_base_rate(db):
    assert sell_monitor.open_from_run(1, db) == 2
    assert {r[0] for r in db.execute("SELECT ticker FROM positions")} == {"WIN", "LOSE"}
    assert sell_monitor.open_from_run(1, db) == 0          # idempotent


def test_no_second_position_while_one_is_open(db):
    sell_monitor.open_from_run(1, db)
    db.execute("""INSERT INTO daily_picks SELECT 2, rank, ticker, prediction_id, p_final, contested, needs_review,
                  replace(pick_json, '2026-01-02', '2026-02-02') FROM daily_picks WHERE run_id = 1""")
    assert sell_monitor.open_from_run(2, db) == 0          # WIN and LOSE are already held
    db.execute("UPDATE positions SET status = 'closed' WHERE ticker = 'WIN'")
    assert sell_monitor.open_from_run(2, db) == 1          # a closed position can be reopened


def test_stop_loss_horizon_and_new_filing_flags(db):
    sell_monitor.open_from_run(1, db)
    by = {r["ticker"]: r for r in sell_monitor.check_positions("2026-02-02", conn=db)}
    assert by["LOSE"]["flags"] == ["stop_loss"]              # -2% vs SPY +10% = -12 points
    assert by["WIN"]["flags"] == ["new_filing"]              # +20% vs +10%: no stop; thesis not checked yet
    by = {r["ticker"]: r for r in sell_monitor.check_positions("2026-04-15", conn=db)}
    assert "horizon_reached" in by["WIN"]["flags"]           # past 2026-04-02


def test_thesis_verdict_needs_a_real_citation(db, monkeypatch):
    sell_monitor.open_from_run(1, db)
    new = [{"eid": "N1", "form_type": "10-Q", "filing_date": "2026-02-01", "section": "MD&A", "text": "Demand fell"}]
    monkeypatch.setattr(sell_monitor, "_new_filing_chunks", lambda pos, as_of: new)
    monkeypatch.setattr(sell_monitor.llm, "structured", lambda s, u, schema: schema(
        status="broken", reason="demand fell", evidence_ids=["N1"]))
    by = {r["ticker"]: r for r in sell_monitor.check_positions("2026-02-02", thesis_check=True, conn=db)}
    assert by["WIN"]["thesis_status"] == "broken" and "thesis_broken" in by["WIN"]["flags"]
    monkeypatch.setattr(sell_monitor.llm, "structured", lambda s, u, schema: schema(
        status="broken", reason="made up", evidence_ids=["N9"]))
    pos = db.execute("SELECT * FROM positions WHERE ticker = 'WIN'").fetchone()
    assert sell_monitor.review_thesis(pos, "2026-02-02")[0] == "unchecked"


def test_thesis_check_reads_evidence_the_pick_never_saw(db, monkeypatch):
    """Entry 2026-01-02. Reviewed: passages filed after entry, and passages from the 90 days before entry
    that are missing from the pick's traces; unseen material-event 8-Ks come first whatever their similarity."""
    from stockagent.retriever import Chunk
    sell_monitor.open_from_run(1, db)
    pos = db.execute("SELECT * FROM positions WHERE ticker = 'WIN'").fetchone()

    def chunk(cid, filed, form="10-Q"):
        return Chunk(chunk_id=cid, text=cid, distance=0.3, ticker="WIN", form_type=form, filing_date=filed,
                     period_end="", section="MD&A", source_url="u")
    monkeypatch.setattr(sell_monitor, "_entry_evidence", lambda p: {"seen", "seen_event"})
    monkeypatch.setattr(sell_monitor, "retrieve", lambda *a, **k: [
        chunk("seen", "2025-12-10"),          # used at entry -> skip
        chunk("too_old", "2025-06-01"),       # before the 90-day window -> skip
        chunk("unseen_recent", "2025-11-15"),  # before entry, never seen -> review
        chunk("after_entry", "2026-01-20")])   # filed after entry -> review

    class FakeCollection:
        def get(self, where, include):
            meta = lambda filed: {"ticker": "WIN", "form_type": "8-K-event", "filing_date": filed,  # noqa: E731
                                  "section": "Event: 5.02 Executive or director change", "source_url": "u"}
            return {"ids": ["ceo_exit", "seen_event"], "documents": ["CEO resigned", "x"],
                    "metadatas": [meta("2025-12-20"), meta("2025-12-01")]}
    monkeypatch.setattr(sell_monitor, "get_collection", lambda: FakeCollection())

    got = sell_monitor._new_filing_chunks(pos, "2026-02-02")
    assert [c["chunk_id"] for c in got] == ["ceo_exit", "unseen_recent", "after_entry"]
    assert [c["timing"] for c in got] == ["filed before entry, not seen by the pick"] * 2 + ["filed after entry"]

    monkeypatch.setattr(sell_monitor, "_entry_evidence", lambda p: None)   # traces gone: only post-entry
    assert [c["chunk_id"] for c in sell_monitor._new_filing_chunks(pos, "2026-02-02")] == ["after_entry"]


def test_close_records_excess_return(db):
    sell_monitor.open_from_run(1, db)
    pid = db.execute("SELECT position_id FROM positions WHERE ticker = 'WIN'").fetchone()[0]
    out = sell_monitor.close_position(pid, conn=db)
    assert out["excess_return"] == pytest.approx(0.20 - 0.10)
    with pytest.raises(ValueError):
        sell_monitor.close_position(pid, conn=db)            # already closed


def test_my_holding_is_tracked_but_kept_apart(db):
    pid = sell_monitor.open_manual("win", "2026-01-02", price=40.0, note="New product cycle", conn=db)
    pos = db.execute("SELECT * FROM positions WHERE position_id = ?", (pid,)).fetchone()
    assert (pos["ticker"], pos["source"], pos["entry_price"], pos["bench_entry"]) == ("WIN", "mine", 40.0, 100.0)
    assert json.loads(pos["thesis_json"])["text"] == "New product cycle"      # the reason is what gets re-checked
    assert pos["prediction_id"] is None                                       # never in the track record
    # +50% vs SPY +10%: tracked like any position, at the user's price
    assert sell_monitor.excess_since_entry(pos, "2026-02-02", db) == pytest.approx(0.4)
    with pytest.raises(ValueError):                                           # one open holding of mine per stock
        sell_monitor.open_manual("WIN", "2026-02-02", conn=db)
    db.execute("UPDATE positions SET opened = '2025-12-31' WHERE position_id = ?", (pid,))
    assert sell_monitor.open_from_run(1, db) == 2                             # my WIN doesn't block the system's
    no_price = sell_monitor.open_manual("LOSE", "2026-02-02", conn=db)         # price defaults to that day's close
    assert db.execute("SELECT entry_price FROM positions WHERE position_id = ?", (no_price,)).fetchone()[0] == 49.0


def test_my_holding_rejects_bad_input(db):
    for args in (("NOPE", "2026-01-02"), ("WIN", "2026-12-01"), ("MEH", "2026-04-15")):
        with pytest.raises(ValueError):   # unknown ticker; after the last price; no recent price
            sell_monitor.open_manual(*args, conn=db)
