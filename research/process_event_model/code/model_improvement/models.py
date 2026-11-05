"""Isolation scores versus radius-normalized prototype distances.

Same inputs and expert support for both families. Cluster radius normalization
is a documented experiment, NOT an implementation of CBLOF. Calibration uses
normal references only. No final labels enter fitting or threshold selection.
"""
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from sklearn.cluster import MiniBatchKMeans
from sklearn.ensemble import IsolationForest
from threadpoolctl import threadpool_limits

from model_evaluation.models import METHODS
from process_anomaly_pipeline.features import ScoreNormalizer


@dataclass(frozen=True)
class Settings:
    algorithm: str
    trees: int = 128
    samples: int = 512
    max_features: float = 1.
    k: int = 6
    radius_normalization: bool = False
    quantile: float = .99
    global_weight: float = .5
    seed: int = 42

    def __post_init__(self):
        if self.algorithm not in ("if", "kmeans"):
            raise ValueError("Invalid algorithm")
        if not 0 < self.quantile < 1 or not 0 <= self.global_weight <= 1:
            raise ValueError("Invalid quantile/weight")
        if not 0 < self.max_features <= 1 or min(self.trees, self.samples, self.k) < 1:
            raise ValueError("Invalid hyperparameters")


@dataclass
class Encoder:
    columns: tuple
    median: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, frame, columns):
        values = frame[list(columns)].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
        median = np.nanmedian(values, axis=0)
        median = np.nan_to_num(median, nan=0.)
        values = np.where(np.isnan(values), median, values)
        lo, hi = np.quantile(values, [.1, .9], axis=0)
        # Avoid a zero IQR magnifying tiny changes or letting a constant feature
        # dominate Euclidean distance. No clipping of test anomalies.
        scale = np.maximum(hi-lo, .2)
        return cls(tuple(columns), median, scale)

    def transform(self, frame):
        values = frame[list(self.columns)].replace([np.inf, -np.inf], np.nan).to_numpy(dtype=float)
        values = np.where(np.isnan(values), self.median, values)
        return ((values-self.median)/self.scale).astype(np.float32)


@dataclass
class Detector:
    settings: Settings
    encoder: Encoder
    model: object
    radii: np.ndarray | None
    normalizer: ScoreNormalizer
    reference_scores: np.ndarray
    raw_threshold: float
    threshold: float

    @staticmethod
    def distance(model, values):
        with threadpool_limits(limits=2):
            labels = model.predict(values)
        return np.linalg.norm(values-model.cluster_centers_[labels], axis=1), labels

    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        encoder = Encoder.fit(train, columns)
        values = encoder.transform(train)
        radii = None
        if settings.algorithm == "if":
            model = IsolationForest(n_estimators=settings.trees, max_samples=min(settings.samples, len(values)),
                                    max_features=settings.max_features, contamination="auto",
                                    random_state=settings.seed, n_jobs=2, bootstrap=False)
        else:
            k = min(settings.k, len(np.unique(values, axis=0)))
            if k < 2:
                raise ValueError("Insufficient unique training vectors")
            model = MiniBatchKMeans(n_clusters=k, n_init=5, batch_size=2048, max_iter=100,
                                   random_state=settings.seed, reassignment_ratio=.01)
        with threadpool_limits(limits=2):
            model.fit(values)
        cal_values = encoder.transform(calibration)
        if settings.algorithm == "kmeans":
            distances, labels = cls.distance(model, cal_values)
            global_radius = max(float(np.quantile(distances, .95)), .1)
            radii = np.array([max(float(np.quantile(distances[labels == i], .95)), .1)
                              if np.sum(labels == i) >= 30 else global_radius
                              for i in range(model.n_clusters)])
            scores = distances/radii[labels] if settings.radius_normalization else distances
        else:
            scores = -model.score_samples(cal_values)
        normalizer = ScoreNormalizer.fit(scores)
        detector = cls(settings, encoder, model, radii, normalizer, np.empty(0), 0., 0.)
        raw = detector.raw(threshold)
        detector.reference_scores = normalizer.transform(raw)
        detector.raw_threshold = float(np.quantile(raw, settings.quantile))
        detector.threshold = float(np.quantile(detector.reference_scores, settings.quantile))
        return detector

    def raw(self, frame):
        values = self.encoder.transform(frame)
        if self.settings.algorithm == "if":
            return -self.model.score_samples(values)
        distances, labels = self.distance(self.model, values)
        return distances/self.radii[labels] if self.settings.radius_normalization else distances

    def score(self, frame):
        return self.normalizer.transform(self.raw(frame))

    def explain_cluster(self, frame):
        if self.settings.algorithm != "kmeans":
            raise ValueError("Cluster explanations require KMeans")
        values = self.encoder.transform(frame)
        distances, labels = self.distance(self.model, values)
        delta = (values-self.model.cluster_centers_[labels])**2
        contribution = delta/np.maximum(delta.sum(axis=1, keepdims=True), 1e-12)
        return labels, distances, contribution


class Family:
    """Global, local and within-family ensemble; unseen adapters use global."""
    def __init__(self, columns, settings):
        self.columns, self.settings = tuple(columns), settings
        self.global_model, self.experts, self.support = None, {}, []
        self.thresholds = {}
        self.threshold_reference = {}

    def fit(self, partitions, support):
        self.global_model = Detector.fit(*(partitions[n] for n in ("train", "calibration", "threshold")),
                                         self.columns, self.settings)
        adapters = sorted(set().union(*(set(p.adapterType) for p in partitions.values())))
        for adapter in adapters:
            parts = {n: p.loc[p.adapterType.eq(adapter)] for n, p in partitions.items()}
            sizes = {n: len(p) for n, p in parts.items()}
            reasons = [f"{n}<{minimum}" for n, minimum in (("train", 500), ("calibration", 100), ("threshold", 100))
                       if sizes[n] < minimum]
            reasons += [f"{n}_source_workflows<20" for n in parts if support.get(adapter, {}).get(n, 0) < 20]
            if not reasons:
                self.experts[adapter] = Detector.fit(*(parts[n] for n in ("train", "calibration", "threshold")),
                                                    self.columns, self.settings)
                self.threshold_reference[adapter] = np.column_stack([
                    self.global_model.score(parts["threshold"]), self.experts[adapter].reference_scores])
            self.support.append({"adapter": adapter, **sizes, "source_workflows": support.get(adapter, {}),
                                 "expert_available": not reasons, "reason": "; ".join(reasons) or "supported"})
        self.set_operating_point(self.settings.quantile, self.settings.global_weight)
        return self

    def set_operating_point(self, quantile, global_weight):
        self.quantile, self.global_weight = quantile, global_weight
        self.thresholds = {"GLOBAL": np.repeat(np.quantile(self.global_model.reference_scores, quantile), 3)}
        for adapter, refs in self.threshold_reference.items():
            mixed = global_weight*refs[:, 0]+(1-global_weight)*refs[:, 1]
            self.thresholds[adapter] = np.array([self.thresholds["GLOBAL"][0],
                                               np.quantile(refs[:, 1], quantile), np.quantile(mixed, quantile)])

    def components(self, frame):
        global_score = self.global_model.score(frame)
        scores = np.repeat(global_score[:, None], 2, axis=1)
        coverage = frame.adapterType.isin(self.experts).to_numpy()
        for adapter, detector in self.experts.items():
            mask = frame.adapterType.eq(adapter).to_numpy()
            if mask.any():
                scores[mask, 1] = detector.score(frame.loc[mask])
        return scores, coverage

    def from_components(self, scores, adapters):
        joined = np.column_stack([scores[:, 0], scores[:, 1],
                                  self.global_weight*scores[:, 0]+(1-self.global_weight)*scores[:, 1]])
        thresholds = np.array([self.thresholds.get(a, self.thresholds["GLOBAL"]) for a in adapters])
        return joined-thresholds

    def score(self, frame):
        scores, coverage = self.components(frame)
        margins = self.from_components(scores, frame.adapterType)
        if not np.isfinite(margins).all():
            raise ValueError("Non-finite scores")
        return margins, margins > 0, coverage

    def records(self):
        records = []
        for adapter, detector in {"GLOBAL": self.global_model, **self.experts}.items():
            raw_reference = detector.normalizer.center+detector.normalizer.spread*detector.reference_scores
            thresholds = self.thresholds.get(adapter, self.thresholds["GLOBAL"])
            records.append({"algorithm": self.settings.algorithm, "route": adapter,
                            "settings": asdict(self.settings), "quantile": self.quantile,
                            "global_weight": self.global_weight,
                            "raw_threshold": float(np.quantile(raw_reference, self.quantile)),
                            "normalized_threshold": float(thresholds[0 if adapter == "GLOBAL" else 1]),
                            "ensemble_threshold": float(thresholds[2]),
                            "normalizer_median": detector.normalizer.center,
                            "normalizer_spread": detector.normalizer.spread,
                            "cluster_radii": detector.radii.tolist() if detector.radii is not None else None,
                            "effective_k": getattr(detector.model, "n_clusters", None)})
        return records


class Suite:
    def __init__(self, families, baseline):
        self.families, self.baseline = families, baseline

    def score(self, frame):
        parts = [self.families[a].score(frame) for a in ("if", "kmeans")]
        if not np.array_equal(parts[0][2], parts[1][2]):
            raise ValueError("Expert cohorts differ; cannot compare fairly")
        return np.column_stack([p[0] for p in parts]), np.column_stack([p[1] for p in parts]), parts[0][2]

    def threshold_records(self):
        return [r for f in self.families.values() for r in f.records()]
