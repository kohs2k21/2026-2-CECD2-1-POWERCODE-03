"""Exactly ONE estimator per algorithm/view. No experts or routing thresholds."""
from dataclasses import dataclass
import time

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits

from model_research_v5.models import Encoder


@dataclass
class SharedDetector:
    algorithm: str
    columns: tuple
    encoder: object
    estimator: object
    thresholds: dict
    fit_seconds: float
    fit_rows: int
    cluster_scales: object = None

    @classmethod
    def fit(cls, algorithm, columns, train, normal_calibration, seed=42):
        start = time.perf_counter()
        encoder = Encoder.fit(train, columns)
        values = encoder.transform(train)
        scales = None
        if algorithm == "if":
            estimator = IsolationForest(n_estimators=300, max_samples=min(512, len(values)),
                contamination="auto", n_jobs=2, random_state=seed)
        elif algorithm == "kmeans":
            estimator = MiniBatchKMeans(n_clusters=16, batch_size=2048, n_init=10,
                max_iter=150, reassignment_ratio=.01, random_state=seed)
        elif algorithm == "lof":
            # Fixed normal reference budget; same row indices in both views.
            if len(values) > 2048:
                ids = np.random.default_rng(seed).choice(len(values), 2048, replace=False)
                values = values[np.sort(ids)]
            estimator = LocalOutlierFactor(n_neighbors=35, novelty=True, metric="euclidean", n_jobs=2)
        else:
            raise ValueError(algorithm)
        with threadpool_limits(limits=2):
            estimator.fit(values)
            if algorithm == "kmeans":
                assigned = estimator.predict(values)
                distance = np.linalg.norm(values-estimator.cluster_centers_[assigned], axis=1)
                pooled = max(float(np.quantile(distance, .95)), .1)
                scales = np.array([max(float(np.quantile(distance[assigned == k], .95)), .1)
                    if np.sum(assigned == k) >= 20 else pooled for k in range(16)])
        result = cls(algorithm, tuple(columns), encoder, estimator, {}, time.perf_counter()-start, len(values), scales)
        scores = result.score(normal_calibration)
        result.thresholds = {str(rate): float(np.quantile(scores, 1-rate, method="higher"))
                             for rate in (.01, .03, .05)}
        return result

    def score(self, frame):
        values = self.encoder.transform(frame)
        with threadpool_limits(limits=2):
            if self.algorithm == "kmeans":
                clusters = self.estimator.predict(values)
                scores = np.linalg.norm(values-self.estimator.cluster_centers_[clusters], axis=1)/self.cluster_scales[clusters]
            else:
                scores = -self.estimator.score_samples(values)
        if not np.isfinite(scores).all():
            raise ValueError("Non-finite anomaly score")
        return scores

    def predict(self, frame, false_alarm_target=.03):
        score = self.score(frame)
        return score, score > self.thresholds[str(false_alarm_target)]
