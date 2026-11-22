"""Step 9: train and evaluate the quantitative forecast model on past data.

Samples: every stock that was in the S&P 500 on the 15th of each month (from config.FORECAST_TRAIN_START
to the latest date whose 90-day outcome is known). Label: did its total return beat SPY's?
Features are measured against that date's median stock, so the model ranks stocks and makes no market call.

Walk-forward evaluation (never train on the future): each year from FORECAST_FIRST_TEST_YEAR on is
predicted by a model fitted only on samples whose outcome was known before that year began. Calibration
is fitted on those out-of-sample predictions; its reported Brier is cross-fitted by year (calibrate on
the other years, score this one). The saved model is fitted on every sample, with that calibration.

Run from the project root:
    python scripts/09_train_forecaster.py
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from stockagent.db import connect  # noqa: E402
from stockagent.features import FEATURES, HORIZON_DAYS, build_samples, relative_to_date, sample_dates  # noqa: E402
from stockagent.forecast_model import ForecastModel, auc_by_date, brier, platt, walk_forward  # noqa: E402
from stockagent.market import calibration_table  # noqa: E402


def main() -> None:
    conn = connect()
    last_price = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()[0]
    last_known = (date.fromisoformat(last_price) - timedelta(days=HORIZON_DAYS)).isoformat()
    keys, feats, labels = build_samples(conn, sample_dates(config.FORECAST_TRAIN_START, last_known))
    dates = np.array([k[0] for k in keys])
    X = relative_to_date([k[0] for k in keys], np.array([[f[k] for k in FEATURES] for f in feats]))
    y = np.array([lab[0] for lab in labels], dtype=float)
    xr = np.array([lab[1] for lab in labels])
    print(f"{len(y)} samples, {len(set(dates))} dates ({dates[0]} .. {dates[-1]}), base rate {y.mean():.1%}")

    p_raw = walk_forward(dates, X, y, config.FORECAST_FIRST_TEST_YEAR, HORIZON_DAYS)
    oos = ~np.isnan(p_raw)
    d, pr, yo, xo = dates[oos], p_raw[oos], y[oos], xr[oos]
    years = np.array([x[:4] for x in d])
    p_cal = np.empty(len(yo))
    for yr in np.unique(years):      # cross-fitted: calibrate on the other years, score this one
        a, b = platt(pr[years != yr], yo[years != yr])
        p_cal[years == yr] = 1 / (1 + np.exp(-(a * np.log(pr[years == yr] / (1 - pr[years == yr])) + b)))

    results = {"base rate": brier(np.full(len(yo), yo.mean()), yo), "model, uncalibrated": brier(pr, yo),
               "model, calibrated (cross-fitted)": brier(p_cal, yo)}
    print(f"\nWalk-forward out-of-sample, {d[0]} .. {d[-1]}, {len(yo)} forecasts")
    for k, v in results.items():
        print(f"  Brier, {k:34} {v:.4f}")
    auc = auc_by_date(d, pr, yo)
    print(f"  Ranking skill (AUC within each date, 0.5 = none): {auc:.3f}")
    rank = np.empty(len(yo))
    for x in np.unique(d):
        m = d == x
        rank[m] = np.argsort(np.argsort(pr[m])) / max(m.sum() - 1, 1)
    top, bottom = rank >= 0.8, rank < 0.2
    print(f"  Top fifth: beat SPY {yo[top].mean():.1%}, mean excess {xo[top].mean():+.2%}; "
          f"bottom fifth: {yo[bottom].mean():.1%}, {xo[bottom].mean():+.2%}")
    print("  AUC by year:", {str(yr): round(auc_by_date(d[years == yr], pr[years == yr], yo[years == yr]), 3)
                             for yr in np.unique(years)})
    print("\nCalibration of cross-fitted forecasts:")
    for row in calibration_table(list(p_cal), list(yo), bucket=0.05):
        print(f"  {row['bucket']:9} n={row['n']:<6} mean forecast {row['mean_forecast']:.1%}  actual {row['hit_rate']:.1%}")

    model = ForecastModel.train(X, y)
    model.calib = platt(pr, yo)
    model.meta = {"trained": date.today().isoformat(), "sample_dates": [dates[0], dates[-1]],
                  "n_samples": int(len(y)), "base_rate": round(float(y.mean()), 4),
                  "walk_forward": [d[0], d[-1]], "n_out_of_sample": int(len(yo)),
                  "holdout_brier": {k: round(v, 4) for k, v in results.items()}, "auc_by_date": round(auc, 3),
                  "universe_note": "stocks in the S&P 500 on each date by date_added; removed members missing "
                                   "(survivorship bias)"}
    model.save()
    print("\ncoefficients (standardized):", dict(zip(["intercept", *FEATURES], np.round(model.coef, 3))))
    print(f"calibration (a, b) = ({model.calib[0]:.3f}, {model.calib[1]:.3f}); saved {model.save.__defaults__[0]}")


if __name__ == "__main__":
    main()
