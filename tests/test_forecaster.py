"""Tests for features, the forecast model and the forecaster (no network, no real LLM)."""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import features, forecast_model, forecaster  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.forecast_model import ForecastModel  # noqa: E402


def _closes(days=400, growth=None):
    idx = pd.date_range("2025-01-01", periods=days, freq="D")
    growth = growth or {"SPY": 0.0005, "AAA": 0.001}
    return pd.DataFrame({t: 100 * (1 + g) ** np.arange(days) for t, g in growth.items()}, index=idx)


def test_features_and_label_use_only_allowed_dates():
    closes = _closes()
    f = features.features_at(closes, "AAA", "2025-09-01")
    assert f["excess_6m"] > 0 and f["volatility_3m"] >= 0
    assert features.features_at(closes, "AAA", "2025-03-01") is None        # no 6-month history yet
    assert features.label_at(closes, "AAA", "2025-09-01")[0] == 1           # grows faster than SPY
    assert features.label_at(closes, "AAA", "2026-01-15") is None           # horizon end not reached


def test_model_learns_signal_and_rejects_reversed_calibration():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 3))
    y = (rng.random(2000) < 1 / (1 + np.exp(-2 * X[:, 0]))).astype(float)
    m = ForecastModel.train(X, y)
    assert m.coef[1] > 1.0                                                  # feature 0 drives the outcome
    m.fit_calibration(X, y)
    assert m.calib[0] > 0.5
    m.fit_calibration(X, 1 - y)                                             # relationship reversed
    assert m.calib[0] == 0.0
    assert np.allclose(m.predict(X), (1 - y).mean(), atol=1e-6)           # falls back to the base rate


@pytest.fixture
def db(tmp_path, monkeypatch):
    conn = connect(tmp_path / "f.db")
    closes = _closes()
    rows = [(t, d.date().isoformat(), float(v)) for t in closes for d, v in closes[t].items()]
    conn.executemany("INSERT INTO prices_daily (ticker, date, close) VALUES (?, ?, ?)", rows)
    model = ForecastModel([0.0, 0.0, 0.0, 0.0], [0, 0, 0], [1, 1, 1], calib=(0.0, 0.0))   # always 50%
    monkeypatch.setattr(forecast_model, "MODEL_PATH", tmp_path / "m.json")
    model.save(tmp_path / "m.json")
    monkeypatch.setattr(ForecastModel, "load", classmethod(lambda cls, path=None: model))
    return conn


def _fake_llm(adjustments):
    return lambda system, user, schema: schema(adjustments=adjustments)


def test_adjustments_need_real_claims_and_are_capped(db, monkeypatch):
    claims = [{"text": "Revenue grew 20%", "status": "supported"},
              {"text": "Margins may fall", "status": "partially_supported"},
              {"text": "Invented", "status": "unsupported"}]
    monkeypatch.setattr(forecaster.llm, "structured", _fake_llm([
        {"claim_numbers": [1], "direction": "up", "strength": "moderate", "reason": "growth"},
        {"claim_numbers": [2], "direction": "down", "strength": "moderate", "reason": "margin risk"},
        {"claim_numbers": [9], "direction": "up", "strength": "moderate", "reason": "cites nothing real"},
    ]))
    r = forecaster.forecast("AAA", "2025-09-01", {"claims": claims, "trace_file": "t"}, conn=db)
    assert [a["points"] for a in r["adjustments"]] == [0.04, -0.02]   # partial evidence counts half; claim 9 dropped
    assert r["shrink_weight"] == 0.5 and r["p_final"] == pytest.approx(0.5 + 0.5 * 0.02)

    monkeypatch.setattr(forecaster.llm, "structured", _fake_llm(
        [{"claim_numbers": [1], "direction": "up", "strength": "moderate", "reason": "x"}] * 4 * 2))
    monkeypatch.setattr(config, "MAX_EVIDENCE_ADJUSTMENT", 0.05)
    assert forecaster.forecast("AAA", "2025-09-01", {"claims": claims}, conn=db)["evidence_adjustment"] == 0.05


def test_prediction_is_stored_and_resolved(db):
    r = forecaster.forecast("AAA", "2025-09-01", conn=db)
    assert r["p_final"] == 0.5 and not r["needs_review"]
    assert forecaster.resolve_open(db) == 1
    row = db.execute("SELECT status, outcome FROM predictions WHERE prediction_id = ?", (r["prediction_id"],)).fetchone()
    assert tuple(row) == ("resolved", 1)


def test_walk_forward_never_trains_on_the_future(monkeypatch):
    dates = np.array(["2017-03-15", "2017-09-15", "2018-03-15", "2018-11-15", "2019-03-15"])
    X, y = np.arange(10.0).reshape(5, 2), np.array([0, 1, 0, 1, 1.0])
    seen = []
    real = ForecastModel.train
    monkeypatch.setattr(ForecastModel, "train", classmethod(lambda cls, Xt, yt: seen.append(len(yt)) or real(Xt, yt)))
    p = forecast_model.walk_forward(dates, X, y, first_year=2018, gap_days=90)
    assert np.isnan(p[:2]).all() and not np.isnan(p[2:]).any()
    assert seen == [2, 3]   # 2018 uses 2017 dates only; 2019 adds 2018-03-15 (2018-11-15's outcome is not known)


def test_features_are_measured_against_the_date_median():
    X = np.array([[1.0, 10], [3.0, 20], [5.0, 60], [0.0, 0]])
    rel = features.relative_to_date(["d1", "d1", "d1", "d2"], X)
    assert rel.tolist() == [[-2, -10], [0, 0], [2, 40], [0, 0]]


def test_live_forecast_uses_the_universe_median(db, monkeypatch):
    model = ForecastModel([0.0, 1.0, 0.0, 0.0], [0, 0, 0], [1, 1, 1], calib=(1.0, 0.0))   # only excess_6m matters
    monkeypatch.setattr(ForecastModel, "load", classmethod(lambda cls, path=None: model))
    db.execute("INSERT INTO universe (ticker, date_added) VALUES ('AAA', '2000-01-01')")
    assert forecaster.forecast("AAA", "2025-09-01", conn=db, save=False)["p_quant"] == pytest.approx(0.5)  # it is the median
