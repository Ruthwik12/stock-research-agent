"""Step 10: backtest predictions for the reflector to learn from before live forecasts resolve.

Stores the walk-forward forecasts as already-resolved predictions with source='backtest': every stock
in the S&P 500 on the 15th of each month from config.FORECAST_FIRST_TEST_YEAR on, predicted by a model
fitted only on outcomes known before that year began, then calibrated with the saved model's calibration.
Only the price-signal layer can be replayed cheaply: research, debate and their adjustments need an
LLM run per stock and date, so backtest rows have no evidence adjustment.

Caveats: the calibration (2 numbers) was fitted on these same forecasts, and stocks removed from the
index are missing (survivorship bias).

Run from the project root after 09 (replaces earlier backtest rows):
    python scripts/10_backtest.py
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent import reflector  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.features import FEATURES, HORIZON_DAYS, build_samples, relative_to_date, sample_dates  # noqa: E402
from stockagent.forecast_model import ForecastModel, logit, walk_forward  # noqa: E402


def main() -> None:
    conn = connect()
    a, b = ForecastModel.load().calib
    sectors = dict(conn.execute("SELECT ticker, sector FROM universe").fetchall())
    last_price = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()[0]
    last_known = (date.fromisoformat(last_price) - timedelta(days=HORIZON_DAYS)).isoformat()
    keys, feats, labels = build_samples(conn, sample_dates(config.FORECAST_TRAIN_START, last_known))
    X = relative_to_date([k[0] for k in keys], np.array([[f[k] for k in FEATURES] for f in feats]))
    y = np.array([lab[0] for lab in labels], dtype=float)
    p_raw = walk_forward(np.array([k[0] for k in keys]), X, y, config.FORECAST_FIRST_TEST_YEAR, HORIZON_DAYS)
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for (d, t), f, lab, pr in zip(keys, feats, labels, p_raw):
        if np.isnan(pr):
            continue
        p = float(1 / (1 + np.exp(-(a * logit(pr) + b))))
        rows.append((now, t, d, HORIZON_DAYS, p, 0.0, 0.0, p, 0, json.dumps(f), "[]", None, "resolved",
                     lab[0], lab[1], now, "backtest", json.dumps(reflector.context_for(f, sectors.get(t), None, 0.0)),
                     0.0))
    conn.execute("DELETE FROM predictions WHERE source = 'backtest'")
    conn.executemany(
        """INSERT INTO predictions (created, ticker, as_of, horizon_days, p_quant, evidence_adjustment, shrink_weight,
           p_final, needs_review, features_json, adjustments_json, report_trace, status, outcome, excess_return,
           resolved_on, source, context_json, rule_adjustment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
           ?, ?, ?, ?)""", rows)
    conn.commit()
    dates = sorted({r[2] for r in rows})
    print(f"{len(rows)} backtest predictions, {len(dates)} dates ({dates[0]} .. {dates[-1]}), "
          f"base rate {sum(r[13] for r in rows) / len(rows):.1%}")


if __name__ == "__main__":
    main()
