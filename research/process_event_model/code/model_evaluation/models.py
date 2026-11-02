"""Same reference populations, encoders and threshold protocol for six methods."""
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler
from threadpoolctl import threadpool_limits

from process_anomaly_pipeline.features import FeatureEncoder, ScoreNormalizer

METHODS = (
    "if_global", "if_adapter", "if_ensemble",
    "kmeans_global", "kmeans_adapter", "kmeans_ensemble",
)
WINDOW_FEATURES = (
    "log_arrivals", "arrival_deviation", "log_arrivals_5m", "arrival_deviation_5m",
    "log_p50_duration", "log_p95_duration", "p95_duration_relative",
    "missing_duration", "hour_sin", "hour_cos", "weekday_sin", "weekday_cos",
)


@dataclass(frozen=True)
class ComparisonConfig:
    seed: int = 42
    quantile: float = .99
    global_weight: float = .5
    n_estimators: int = 128
    max_samples: int = 512
    k: int = 6
    kmeans_batch_size: int = 2048
    kmeans_n_init: int = 5
    kmeans_max_iter: int = 100
    min_train: int = 500
    min_calibration: int = 100
    min_threshold: int = 100
    min_source_workflows: int = 20
    min_row_train_days: int = 3
    replay_cases_per_adapter: int = 8
    replay_minutes: int = 90
    window_minutes: int = 1
    chunk_rows: int = 50_000

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key in ("quantile", "global_weight"):
                if isinstance(value, bool) or not np.isfinite(value) or not 0 < value < 1:
                    raise ValueError(f"Invalid {key}")
            elif isinstance(value, bool) or not isinstance(value, int) or value < (0 if key == "seed" else 1):
                raise ValueError(f"Invalid {key}")


@dataclass
class WindowEncoder:
    columns: tuple
    medians: pd.Series
    scaler: RobustScaler

    @classmethod
    def fit(cls, frame):
        values = frame[list(WINDOW_FEATURES)].replace([np.inf, -np.inf], np.nan)
        medians = values.median().fillna(0)
        return cls(WINDOW_FEATURES, medians, RobustScaler().fit(values.fillna(medians)))

    def transform(self, frame):
        values = frame[list(self.columns)].replace([np.inf, -np.inf], np.nan).fillna(self.medians)
        return self.scaler.transform(values).astype(np.float32)


@dataclass
class Detector:
    algorithm: str
    model: object
    encoder: object
    normalizer: ScoreNormalizer
    threshold: float
    raw_threshold: float
    effective_k: int | None

    @staticmethod
    def raw(model, algorithm, values):
        if algorithm == "if":
            # sklearn's score_samples is smaller for anomalies; negate it.
            return -model.score_samples(values)
        with threadpool_limits(limits=2):
            labels = model.predict(values)
        return np.linalg.norm(values - model.cluster_centers_[labels], axis=1).astype(float)

    @classmethod
    def fit(cls, algorithm, train, calibration, threshold, unit, config):
        encoder = FeatureEncoder.fit(train) if unit == "row" else WindowEncoder.fit(train)
        values = encoder.transform(train)
        unique = len(np.unique(values, axis=0))
        if unique < 2:
            raise ValueError("Fewer than two distinct training vectors")
        effective_k = None
        if algorithm == "if":
            model = IsolationForest(
                n_estimators=config.n_estimators, max_samples=min(config.max_samples, len(values)),
                contamination="auto", max_features=1., bootstrap=False,
                random_state=config.seed, n_jobs=2,
            )
        else:
            effective_k = min(config.k, unique)
            model = MiniBatchKMeans(
                n_clusters=effective_k, batch_size=config.kmeans_batch_size,
                max_iter=config.kmeans_max_iter, n_init=config.kmeans_n_init,
                random_state=config.seed, reassignment_ratio=.01,
            )
        with threadpool_limits(limits=2):
            model.fit(values)
        cal_scores = cls.raw(model, algorithm, encoder.transform(calibration))
        normalizer = ScoreNormalizer.fit(cal_scores)
        th_scores = cls.raw(model, algorithm, encoder.transform(threshold))
        raw_threshold = float(np.quantile(th_scores, config.quantile))
        return cls(algorithm, model, encoder, normalizer,
                   float(normalizer.transform([raw_threshold])[0]), raw_threshold, effective_k)

    def score(self, frame):
        return self.normalizer.transform(self.raw(self.model, self.algorithm, self.encoder.transform(frame)))


class SixModels:
    """Adapter strategy includes explicit global fallback, not fabricated experts."""

    def __init__(self, unit, config):
        if unit not in ("row", "window"):
            raise ValueError("Unknown evaluation unit")
        self.unit, self.config = unit, config
        self.global_models, self.experts, self.ensemble_thresholds = {}, {}, {}
        self.support = []

    def fit(self, partitions, source_support=None):
        train, calibration, threshold = (partitions[name] for name in ("train", "calibration", "threshold"))
        if min(len(train), len(calibration), len(threshold)) < self.config.min_threshold:
            raise ValueError("Insufficient independent global references")
        for algorithm in ("if", "kmeans"):
            self.global_models[algorithm] = Detector.fit(algorithm, train, calibration, threshold, self.unit, self.config)
            self.experts[algorithm], self.ensemble_thresholds[algorithm] = {}, {}
        adapters = sorted(set().union(*(set(p.adapterType.dropna()) for p in partitions.values())))
        for adapter in adapters:
            subsets = {name: p.loc[p.adapterType.eq(adapter)] for name, p in partitions.items()}
            sizes = {name: len(p) for name, p in subsets.items()}
            reasons = []
            for name, minimum in (("train", self.config.min_train), ("calibration", self.config.min_calibration), ("threshold", self.config.min_threshold)):
                if sizes[name] < minimum:
                    reasons.append(f"{name}<{minimum}")
            days = int(subsets["train"].startTime.dt.normalize().nunique()) if self.unit == "row" else None
            if days is not None and days < self.config.min_row_train_days:
                reasons.append(f"train_days<{self.config.min_row_train_days}")
            independent = (source_support or {}).get(adapter, {})
            if self.unit == "window":
                for name in ("train", "calibration", "threshold"):
                    if independent.get(name, 0) < self.config.min_source_workflows:
                        reasons.append(f"{name}_source_workflows<{self.config.min_source_workflows}")
            fitted = {}
            if not reasons:
                try:
                    for algorithm in ("if", "kmeans"):
                        fitted[algorithm] = Detector.fit(algorithm, subsets["train"], subsets["calibration"], subsets["threshold"], self.unit, self.config)
                except ValueError as exc:
                    reasons.append(str(exc))
            # Identical supported cohorts for both algorithms, even if one fit fails.
            if not reasons:
                for algorithm, detector in fitted.items():
                    self.experts[algorithm][adapter] = detector
                    peer = self.global_models[algorithm].score(subsets["threshold"])
                    local = detector.score(subsets["threshold"])
                    combined = self.config.global_weight * peer + (1 - self.config.global_weight) * local
                    self.ensemble_thresholds[algorithm][adapter] = float(np.quantile(combined, self.config.quantile))
            self.support.append({"unit": self.unit, "adapter": adapter, **sizes,
                                 "train_days": days, "source_workflows": independent,
                                 "expert_available": not reasons, "reason": "; ".join(reasons) or "supported"})
        return self

    def score(self, frame):
        margins = np.empty((len(frame), len(METHODS)), dtype=float)
        covered = frame.adapterType.isin(self.experts["if"]).to_numpy(dtype=bool)
        for algorithm, offset in (("if", 0), ("kmeans", 3)):
            global_detector = self.global_models[algorithm]
            global_scores = global_detector.score(frame)
            global_margin = global_scores - global_detector.threshold
            margins[:, offset:offset+3] = global_margin[:, None]
            for adapter, detector in self.experts[algorithm].items():
                mask = frame.adapterType.eq(adapter).to_numpy(dtype=bool)
                if not mask.any():
                    continue
                local_scores = detector.score(frame.loc[mask])
                margins[mask, offset+1] = local_scores - detector.threshold
                combined = self.config.global_weight * global_scores[mask] + (1-self.config.global_weight) * local_scores
                margins[mask, offset+2] = combined - self.ensemble_thresholds[algorithm][adapter]
        if not np.isfinite(margins).all():
            raise ValueError("Non-finite model score; do not silently classify as normal")
        return margins, margins > 0, covered

    def threshold_records(self):
        records = []
        for algorithm in ("if", "kmeans"):
            for name, model in {"GLOBAL": self.global_models[algorithm], **self.experts[algorithm]}.items():
                records.append({"unit": self.unit, "algorithm": algorithm, "route": name,
                                "raw_threshold": model.raw_threshold, "normalized_threshold": model.threshold,
                                "ensemble_threshold": self.ensemble_thresholds[algorithm].get(name, model.threshold),
                                "normalizer_median": model.normalizer.center,
                                "normalizer_spread": model.normalizer.spread, "effective_k": model.effective_k})
        return records
