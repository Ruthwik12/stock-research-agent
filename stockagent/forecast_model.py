"""Quantitative forecast model: logistic regression on price features, plus Platt calibration.

Calibration maps the model's raw probability p to sigmoid(a * logit(p) + b), fitted on later,
held-out dates. If the features carry no real skill, `a` comes out near 0 and every forecast is
pulled toward the observed base rate: the "shrink toward the base rate" rule, driven by data.
"""
import json
from pathlib import Path

import numpy as np

import config
from stockagent.features import FEATURES

MODEL_PATH = config.DATA_DIR / "models" / "forecaster.json"


def _sigmoid(z):
    return 1 / (1 + np.exp(-z))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    """Newton's method with an L2 penalty (intercept unpenalized). X must include a leading 1s column."""
    w = np.zeros(X.shape[1])
    penalty = np.eye(X.shape[1]) * l2
    penalty[0, 0] = 0
    for _ in range(iters):
        p = _sigmoid(X @ w)
        grad = X.T @ (p - y) + penalty @ w
        hess = X.T @ (X * (p * (1 - p))[:, None]) + penalty
        step = np.linalg.solve(hess, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def platt(p_raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(a, b) so that sigmoid(a * logit(p_raw) + b) matches the outcomes y."""
    b, a = fit_logistic(np.column_stack([np.ones(len(y)), logit(p_raw)]), y, l2=0.0)
    if a < 0:
        # The signal reversed out of sample. Flipping it would bet on a pattern that did not hold,
        # so trust neither direction: fall back to the observed base rate.
        a, b = 0.0, float(logit(np.clip(y.mean(), 1e-6, 1 - 1e-6)))
    return float(a), float(b)


def walk_forward(dates: np.ndarray, X: np.ndarray, y: np.ndarray, first_year: int, gap_days: int) -> np.ndarray:
    """Out-of-sample raw probabilities: each test year is predicted by a model fitted only on samples whose
    outcome was known before that year began (sample date + gap_days < Jan 1). NaN before first_year."""
    from datetime import date, timedelta
    p = np.full(len(y), np.nan)
    years = np.array([d[:4] for d in dates]).astype(int)
    for year in range(first_year, years.max() + 1):
        test = years == year
        train = dates <= (date(year, 1, 1) - timedelta(days=gap_days + 1)).isoformat()
        if test.any() and train.any():
            p[test] = ForecastModel.train(X[train], y[train]).raw(X[test])
    return p


def auc_by_date(dates: np.ndarray, p: np.ndarray, y: np.ndarray) -> float:
    """Mean over dates of the chance a stock that beat the benchmark was ranked above one that didn't."""
    out = []
    for d in np.unique(dates):
        m = dates == d
        pos, neg = p[m][y[m] == 1], p[m][y[m] == 0]
        if len(pos) and len(neg):
            out.append((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())
    return float(np.mean(out))


class ForecastModel:
    def __init__(self, coef, mean, std, calib=(1.0, 0.0), meta=None):
        self.coef, self.mean, self.std = np.asarray(coef), np.asarray(mean), np.asarray(std)
        self.calib = tuple(calib)          # (a, b) for sigmoid(a * logit(p) + b)
        self.meta = meta or {}

    @classmethod
    def train(cls, X: np.ndarray, y: np.ndarray, l2: float = 1.0) -> "ForecastModel":
        mean, std = X.mean(axis=0), X.std(axis=0) + 1e-12
        Z = np.column_stack([np.ones(len(X)), (X - mean) / std])
        return cls(fit_logistic(Z, y, l2), mean, std)

    def raw(self, X: np.ndarray) -> np.ndarray:
        Z = np.column_stack([np.ones(len(X)), (X - self.mean) / self.std])
        return _sigmoid(Z @ self.coef)

    def fit_calibration(self, X: np.ndarray, y: np.ndarray) -> None:
        self.calib = platt(self.raw(X), y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        a, b = self.calib
        return _sigmoid(a * logit(self.raw(X)) + b)

    def predict_one(self, features: dict) -> float:
        return float(self.predict(np.array([[features[f] for f in FEATURES]]))[0])

    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"features": FEATURES, "coef": self.coef.tolist(), "mean": self.mean.tolist(),
                                    "std": self.std.tolist(), "calib": list(self.calib), "meta": self.meta}, indent=1))

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "ForecastModel":
        d = json.loads(path.read_text())
        if d["features"] != FEATURES:
            raise ValueError("saved model was trained on different features; retrain it")
        return cls(d["coef"], d["mean"], d["std"], d["calib"], d["meta"])
