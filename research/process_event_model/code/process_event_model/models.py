"""Three genuine event-row estimators, calibrated on separate development data."""
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.covariance import OAS
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor

from .features import FEATURES


VIEWS = {
    "micro_compact": ("row_upper_tail", "native_latency_run", "native_latency_cusum",
                      "micro_cusum", "micro_density", "micro_run"),
    "micro_context": ("row_upper_tail", "row_residual", "row_log_count", "reference_level",
                      "native_latency_run", "native_latency_cusum", "native_latency_lift",
                      "micro_cusum", "micro_density", "micro_run", "native_traffic_current"),
    "native_compact": ("row_upper_tail", "native_latency_run", "native_latency_cusum",
                       "native_traffic_llr", "native_traffic_current"),
    "native_context": ("row_upper_tail", "native_latency_run", "native_latency_cusum",
                       "native_latency_lift", "native_traffic_llr", "native_traffic_current",
                       "predictive_burst_1m", "predictive_burst_3m"),
    "adaptive_context": ("row_upper_tail", "adaptive_upper", "adaptive_mean_shift", "adaptive_ratio",
                         "adaptive_cusum", "adaptive_recent_excess", "predictive_burst_1m", "predictive_burst_3m"),
    "adaptive_compact": ("row_upper_tail", "adaptive_upper", "adaptive_mean_shift",
                         "predictive_burst_1m", "predictive_burst_3m"),
    "nb_compact": ("row_upper_tail", "local_upper", "local_log_ratio", "predictive_burst_1m", "predictive_burst_3m"),
    "nb_context": ("row_upper_tail", "local_upper", "local_shift", "local_slope",
                   "local_cusum", "local_persistence", "predictive_burst_1m", "predictive_burst_3m"),
    "row_point": ("row_upper_tail", "row_residual", "local_upper", "local_shift",
                  "traffic_lift_1m", "traffic_lift_3m", "traffic_density"),
    "row_context": ("row_upper_tail", "local_upper", "local_shift", "local_slope",
                    "local_cusum", "local_persistence", "traffic_lift_1m",
                    "traffic_lift_3m", "traffic_lift_5m", "traffic_density"),
    "row_compact": ("row_upper_tail", "local_upper", "local_log_ratio", "traffic_lift_1m", "traffic_lift_3m"),
    "point": ("duration_residual", "duration_tail", "recent_duration_shift",
              "prefix_duration_shift", "recent_residual_mean", "delay_cusum",
              "delay_slope", "recent_burst", "prefix_burst", "gap_relative"),
    "context": ("duration_residual", "duration_tail", "recent_duration_shift",
                "prefix_duration_shift", "recent_residual_mean", "recent_residual_max",
                "delay_cusum", "delay_slope", "delay_persistence", "recent_burst",
                "prefix_burst", "gap_relative", "recent_gap_shift"),
    "full": FEATURES,
}


@dataclass
class Settings:
    algorithm: str
    view: str = "context"
    samples: int = 256
    clusters: int = 16
    neighbors: int = 50
    whiten: bool = False
    seed: int = 20261030


class Detector:
    def __init__(self, settings):
        self.settings = settings
        self.columns = VIEWS[settings.view]
        self.threshold = None

    def _array(self, f):
        x = f[list(self.columns)].to_numpy(float)
        # Positive departures are evidence; retain signed residuals in full view.
        if self.settings.view != "full":
            x = np.log1p(np.maximum(x, 0))
        return np.nan_to_num(x, nan=0., posinf=100., neginf=-100.)

    def encode(self, f):
        x = (self._array(f)-self.center)/self.scale
        # Cap finite numerical leverage, not data-dependent anomaly thresholds.
        x = np.clip(x, -100, 100)
        return x @ self.white

    def fit(self, normal):
        x = self._array(normal)
        self.center = np.median(x, axis=0)
        self.scale = np.maximum(np.quantile(x, .95, axis=0)-self.center, .15)
        self.white = np.eye(x.shape[1])
        if self.settings.whiten:
            cov = OAS().fit(np.clip((x-self.center)/self.scale, -10, 10)).covariance_
            eigen, vectors = np.linalg.eigh(cov)
            self.white = (vectors/np.sqrt(np.maximum(eigen, .05))) @ vectors.T
        x = self.encode(normal)
        s = self.settings
        if s.algorithm == "if":
            self.estimator = IsolationForest(n_estimators=400, max_samples=min(s.samples, len(x)),
                contamination="auto", random_state=s.seed, n_jobs=2).fit(x)
        elif s.algorithm == "kmeans":
            clusters=min(s.clusters,len(x))
            self.estimator = MiniBatchKMeans(n_clusters=clusters, batch_size=2048,
                n_init=5, random_state=s.seed).fit(x)
            labels = self.estimator.predict(x)
            distances = np.linalg.norm(x-self.estimator.cluster_centers_[labels], axis=1)
            self.radii = np.array([max(float(np.quantile(distances[labels == i], .95)), .15)
                                  if np.any(labels == i) else 1. for i in range(clusters)])
        elif s.algorithm == "lof":
            # Bound serving memory and omit duplicate vectors, not duplicate raw rows.
            x = np.unique(np.round(x, 6), axis=0)
            if len(x)<2:
                raise ValueError('LOF needs at least two distinct normal feature vectors; no synthetic vectors are added')
            if len(x) > 8192:
                x = x[np.random.default_rng(s.seed).choice(len(x), 8192, replace=False)]
            self.estimator = LocalOutlierFactor(n_neighbors=min(s.neighbors, len(x)-1),
                novelty=True, contamination="auto", n_jobs=2).fit(x)
        else:
            raise ValueError(s.algorithm)
        self.fit_rows = len(normal)
        return self

    def raw_score(self, f):
        x = self.encode(f)
        if self.settings.algorithm == "kmeans":
            labels = self.estimator.predict(x)
            return np.linalg.norm(x-self.estimator.cluster_centers_[labels], axis=1)/self.radii[labels]
        return -self.estimator.score_samples(x)

    def predict(self, f):
        if self.threshold is None:
            raise ValueError("Fit a threshold on calibration/development rows first")
        score = self.raw_score(f)
        return score, score > self.threshold


def metrics(y, predicted):
    y, p = np.asarray(y, bool), np.asarray(predicted, bool)
    tp, fp = int(np.sum(y & p)), int(np.sum(~y & p))
    tn, fn = int(np.sum(~y & ~p)), int(np.sum(y & ~p))
    return dict(n=len(y), tp=tp, fp=fp, tn=tn, fn=fn,
        accuracy=(tp+tn)/max(len(y), 1), precision=tp/max(tp+fp, 1),
        recall=tp/max(tp+fn, 1), f1=2*tp/max(2*tp+fp+fn, 1),
        false_positive_rate=fp/max(fp+tn, 1), positives=tp+fn)
