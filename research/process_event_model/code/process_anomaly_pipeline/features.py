"""Fixed numeric Process features; IDs, STATUS and response codes are excluded."""
from dataclasses import dataclass
import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler

NUMERIC_FEATURES = ("duration_ms", "totalCount", "retryCount")


def raw_features(frame):
    out = pd.DataFrame(index=frame.index)
    for c in NUMERIC_FEATURES:
        out[f"log_{c}"] = np.log1p(frame[c].where(frame[c].ge(0)))
        out[f"missing_{c}"] = frame[c].isna().astype(float)
    for name, value, period in (("hour", frame.startTime.dt.hour, 24),
                                ("weekday", frame.startTime.dt.dayofweek, 7)):
        out[f"{name}_sin"] = np.sin(2 * np.pi * value / period)
        out[f"{name}_cos"] = np.cos(2 * np.pi * value / period)
    return out.replace([np.inf, -np.inf], np.nan)


@dataclass
class FeatureEncoder:
    medians: pd.Series
    scaler: RobustScaler
    columns: tuple

    @classmethod
    def fit(cls, frame):
        raw = raw_features(frame)
        medians = raw.median().fillna(0)
        filled = raw.fillna(medians)
        # Keep a stable schema. Constant features cannot teach IF unseen behavior.
        if not filled.nunique().gt(1).any():
            raise ValueError("All training features are constant; IF cannot learn useful behavior")
        return cls(medians, RobustScaler().fit(filled), tuple(filled.columns))

    def transform(self, frame):
        raw = raw_features(frame).reindex(columns=self.columns).fillna(self.medians)
        return self.scaler.transform(raw).astype(np.float32)


@dataclass
class ScoreNormalizer:
    center: float
    spread: float

    @classmethod
    def fit(cls, scores):
        scores = np.asarray(scores, dtype=float)
        scores = scores[np.isfinite(scores)]
        if not len(scores):
            raise ValueError("No finite calibration scores")
        center = float(np.median(scores))
        spread = float(np.quantile(scores, .95) - center)
        if spread <= 1e-12:
            spread = max(float(np.std(scores)), 1e-6)
        return cls(center, spread)

    def transform(self, scores):
        # Unbounded: unlike ECDF percentile, novel extremes do not saturate at 1.
        return (np.asarray(scores) - self.center) / self.spread


def feature_profile(frame):
    result = {}
    for c in NUMERIC_FEATURES:
        s = frame[c].dropna()
        if len(s):
            result[c] = {"n": int(len(s)), "p01": float(s.quantile(.01)),
                         "p50": float(s.median()), "p95": float(s.quantile(.95)),
                         "p99": float(s.quantile(.99))}
    return result
