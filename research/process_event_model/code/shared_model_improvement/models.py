"""Single estimators with train-only bounded/soft-compressed numeric features."""
from dataclasses import dataclass, asdict
import time

import numpy as np
from sklearn.preprocessing import QuantileTransformer
from sklearn.ensemble import IsolationForest
from sklearn.cluster import MiniBatchKMeans
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits

from adapter_feature_ablation.features import BASE, ADAPTER


@dataclass(frozen=True)
class Setting:
    algorithm: str
    view: str = "full"
    transform: str = "asinh"
    adapter_weight: float = .5
    drop_workload: bool = True
    samples: int = 512
    max_features: float = 1.
    trees: int = 300
    clusters: int = 16
    neighbors: int = 35
    reference_cap: int = 4096
    metric: str = "euclidean"
    cluster_radius: bool = True
    deduplicate: bool = False
    seed: int = 42


@dataclass
class StableEncoder:
    columns: tuple
    median: np.ndarray
    scale: np.ndarray
    mode: str
    weights: np.ndarray
    quantiles: object = None

    @classmethod
    def fit(cls, frame, setting):
        added = tuple(c for c in ADAPTER if not(setting.drop_workload and c == "adapter_workload_z"))
        traffic = BASE[:6]
        if setting.view == "balanced":
            base = (*traffic, "duration_upper_max_1m", "duration_lower_max_1m", "duration_upper_mean_5m",
                    "duration_lower_mean_5m", "duration_upper_max_5m", "fixed_shift_5m", "fixed_positive_cusum",
                    "recent_duration_lift", "duration_trend_z")
        elif setting.view == "compact":
            base = (*traffic, "duration_upper_max_1m", "duration_upper_mean_5m", "fixed_positive_cusum", "duration_trend_z")
        elif setting.view == "full": base = BASE
        else: raise ValueError(setting.view)
        columns = (*base, *added)
        x = frame[list(columns)].to_numpy(float)
        median = np.nanmedian(x, axis=0)
        median = np.nan_to_num(median)
        x = np.where(np.isnan(x), median, x)
        q10, q90 = np.quantile(x, [.1, .9], axis=0)
        # A finite normal spread floor avoids amplifying nearly-constant columns.
        scale = np.maximum(q90-q10, .5)
        if setting.transform == "hybrid":
            scale = np.maximum(q90-q10, np.array([.5 if c in ADAPTER else .05 for c in columns]))
        weights = np.array([setting.adapter_weight if c in ADAPTER else 1. for c in columns])
        quantiles = None
        if setting.transform == "quantile":
            quantiles = QuantileTransformer(n_quantiles=min(256, len(x)), output_distribution="normal",
                                            subsample=None, random_state=setting.seed).fit(x)
        return cls(tuple(columns), median, scale, setting.transform, weights, quantiles)

    def transform(self, frame):
        x = frame[list(self.columns)].to_numpy(float)
        if np.isinf(x).any(): raise ValueError("Infinite feature")
        x = np.where(np.isnan(x), self.median, x)
        if self.mode == "quantile":
            x = self.quantiles.transform(x)
        elif self.mode == "asinh":
            x = np.clip(np.arcsinh((x-self.median)/self.scale), -8, 8)
        elif self.mode == "clip":
            x = np.clip((x-self.median)/self.scale, -6, 6)
        elif self.mode == "hybrid":
            x = (x-self.median)/self.scale
            ids = np.array([c in ADAPTER for c in self.columns])
            x[:, ids] = np.clip(np.arcsinh(x[:, ids]), -8, 8)
        else:
            raise ValueError(self.mode)
        return (x*self.weights).astype(np.float32)


class StableDetector:
    def __init__(self, setting):
        self.setting = setting
        self.algorithm = setting.algorithm
        self.threshold = None

    def fit(self, normal):
        start = time.perf_counter()
        s = self.setting
        self.encoder = StableEncoder.fit(normal, s)
        self.columns = self.encoder.columns
        x = self.encoder.transform(normal)
        self.cluster_scales = None
        if s.algorithm == "if":
            self.estimator = IsolationForest(n_estimators=s.trees, max_samples=min(s.samples, len(x)),
                max_features=s.max_features, contamination="auto", n_jobs=2, random_state=s.seed)
        elif s.algorithm == "kmeans":
            self.estimator = MiniBatchKMeans(n_clusters=s.clusters, batch_size=2048, n_init=10,
                max_iter=150, reassignment_ratio=.01, random_state=s.seed)
        elif s.algorithm == "lof":
            if s.deduplicate:
                x = np.unique(x, axis=0)
            if len(x) > s.reference_cap:
                ids = np.sort(np.random.default_rng(s.seed).choice(len(x), s.reference_cap, replace=False))
                x = x[ids]
            self.estimator = LocalOutlierFactor(n_neighbors=s.neighbors, novelty=True, metric=s.metric, n_jobs=2)
        else:
            raise ValueError(s.algorithm)
        with threadpool_limits(limits=2):
            self.estimator.fit(x)
            if s.algorithm == "kmeans" and s.cluster_radius:
                labels = self.estimator.predict(x)
                d = np.linalg.norm(x-self.estimator.cluster_centers_[labels], axis=1)
                default = max(float(np.quantile(d, .95)), .1)
                self.cluster_scales = np.array([max(float(np.quantile(d[labels == k], .95)), .1)
                    if (labels == k).sum() >= 20 else default for k in range(s.clusters)])
        self.fit_rows, self.fit_seconds = len(x), time.perf_counter()-start
        return self

    def score(self, frame):
        x = self.encoder.transform(frame)
        with threadpool_limits(limits=2):
            if self.algorithm == "kmeans":
                labels = self.estimator.predict(x)
                scores = np.linalg.norm(x-self.estimator.cluster_centers_[labels], axis=1)
                if self.cluster_scales is not None: scores /= self.cluster_scales[labels]
            else:
                scores = -self.estimator.score_samples(x)
        if not np.isfinite(scores).all(): raise ValueError("Non-finite score")
        return scores

    def predict(self, frame):
        if self.threshold is None: raise ValueError("No frozen threshold")
        scores = self.score(frame)
        return scores, scores > self.threshold

    def record(self):
        return {"settings": asdict(self.setting), "columns": list(self.columns),
                "adapter_feature_count": sum(c in ADAPTER for c in self.columns),
                "fit_rows": self.fit_rows, "fit_seconds": self.fit_seconds,
                "threshold": self.threshold, "estimator_count": 1, "experts": False}
