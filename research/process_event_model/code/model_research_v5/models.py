"""IF rotated-feature variant, cluster-shape distance, phase-aware calibration.

Random rotations are INSPIRED by EIF's rotated-tree alternative. A block shares
one rotation across 32 trees; this is NOT the paper's full random-hyperplane EIF.
Cluster diagonal-shape distances are a local dispersion experiment, NOT CBLOF.
"""
from dataclasses import dataclass, asdict

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits

from process_anomaly_pipeline.features import ScoreNormalizer

METHODS = ("if_global", "if_adapter", "if_ensemble", "kmeans_global", "kmeans_adapter", "kmeans_ensemble")


@dataclass(frozen=True)
class Settings:
    algorithm: str
    samples: int = 512
    rotations: int = 1
    k: int = 6
    shape_distance: bool = False
    neighbors: int = 50
    seed: int = 42

    def __post_init__(self):
        if self.algorithm not in ("if", "kmeans", "lof") or min(self.samples, self.rotations, self.k, self.neighbors) < 1:
            raise ValueError("Invalid model setting")
        if self.rotations not in (1, 4):
            raise ValueError("Only 1/4 IF rotations are declared")


@dataclass
class Encoder:
    columns: tuple
    median: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, train, columns):
        values = train[list(columns)].to_numpy(dtype=float)
        median = np.nanmedian(values, axis=0)
        median = np.nan_to_num(median, nan=0.)
        values = np.where(np.isnan(values), median, values)
        q10, q90 = np.quantile(values, [.1, .9], axis=0)
        return cls(tuple(columns), median, np.maximum(q90-q10, .05))

    def transform(self, frame):
        values = frame[list(self.columns)].to_numpy(dtype=float)
        if np.isinf(values).any(): raise ValueError("Infinite feature")
        return ((np.where(np.isnan(values), self.median, values)-self.median)/self.scale).astype(np.float32)


class RawModel:
    def __init__(self, settings):
        self.settings = settings
        self.models, self.rotations = [], []
        self.cluster_scale = self.cluster_radii = None

    def fit(self, values, calibration):
        settings = self.settings
        if settings.algorithm == "if":
            rng = np.random.default_rng(settings.seed)
            for i in range(settings.rotations):
                rotation = (np.eye(values.shape[1], dtype=np.float32) if settings.rotations == 1 else
                            np.linalg.qr(rng.normal(size=(values.shape[1], values.shape[1])))[0].astype(np.float32))
                model = IsolationForest(n_estimators=128//settings.rotations,
                                        max_samples=min(settings.samples, len(values)), max_features=1.,
                                        contamination="auto", bootstrap=False, n_jobs=2,
                                        random_state=settings.seed+i*101)
                with threadpool_limits(limits=2): model.fit(values@rotation)
                self.models.append(model); self.rotations.append(rotation)
        elif settings.algorithm == "kmeans":
            model = MiniBatchKMeans(n_clusters=min(settings.k, len(np.unique(values, axis=0))),
                                   n_init=5, batch_size=2048, max_iter=100, reassignment_ratio=.01,
                                   random_state=settings.seed)
            with threadpool_limits(limits=2): model.fit(values)
            self.models = [model]
            with threadpool_limits(limits=2): labels = model.predict(calibration)
            residual = calibration-model.cluster_centers_[labels]
            overall = np.maximum(np.sqrt(np.mean(residual**2, axis=0)), .1)
            self.cluster_scale = np.vstack([
                np.maximum(np.sqrt(np.mean(residual[labels == j]**2, axis=0)), .1)
                if np.sum(labels == j) >= 30 else overall for j in range(model.n_clusters)])
            # Radius is learned on separate normal calibration, not anomalies.
            distance = self._cluster_distance(calibration)
            cluster_distance = np.min(distance, axis=1)
            assigned = np.argmin(distance, axis=1)
            global_radius = max(float(np.quantile(cluster_distance, .95)), .1)
            self.cluster_radii = np.array([
                max(float(np.quantile(cluster_distance[assigned == j], .95)), .1)
                if np.sum(assigned == j) >= 30 else global_radius for j in range(model.n_clusters)])
        else:
            model = LocalOutlierFactor(n_neighbors=min(settings.neighbors, len(values)-1),
                                       novelty=True, contamination="auto", n_jobs=2,
                                       algorithm="auto", metric="minkowski", p=2)
            with threadpool_limits(limits=2): model.fit(values)
            self.models = [model]
        return self

    def _cluster_distance(self, values):
        centers = self.models[0].cluster_centers_
        distances = []
        for j, center in enumerate(centers):
            delta = values-center
            if self.settings.shape_distance: delta = delta/self.cluster_scale[j]
            distances.append(np.linalg.norm(delta, axis=1))
        return np.column_stack(distances)

    def score(self, values):
        if self.settings.algorithm == "if":
            with threadpool_limits(limits=2):
                return np.mean([-m.score_samples(values@r) for m, r in zip(self.models, self.rotations)], axis=0)
        if self.settings.algorithm == "lof":
            # novelty=True: score ONLY new calibration/validation/test samples.
            with threadpool_limits(limits=2): return -self.models[0].score_samples(values)
        distances = self._cluster_distance(values)
        # Choose the closest normalized prototype. Wide normal clusters are not
        # automatically anomalous just because raw Euclidean distance is large.
        return np.min(distances/self.cluster_radii, axis=1)


@dataclass
class Detector:
    encoder: Encoder
    raw_model: RawModel
    calibration_raw: np.ndarray
    threshold_raw: np.ndarray
    calibration_phase: np.ndarray
    threshold_phase: np.ndarray

    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        encoder = Encoder.fit(train, columns)
        raw = RawModel(settings).fit(encoder.transform(train), encoder.transform(calibration))
        return cls(encoder, raw, raw.score(encoder.transform(calibration)), raw.score(encoder.transform(threshold)),
                   calibration.phase.to_numpy(), threshold.phase.to_numpy())

    def raw(self, frame):
        return self.raw_model.score(self.encoder.transform(frame))

    def normalizers(self, phase_aware):
        if not phase_aware:
            return {p: ScoreNormalizer.fit(self.calibration_raw) for p in ("startup", "steady")}
        return {p: ScoreNormalizer.fit(self.calibration_raw[self.calibration_phase == p])
                if np.sum(self.calibration_phase == p) >= 50 else ScoreNormalizer.fit(self.calibration_raw)
                for p in ("startup", "steady")}


def normalize(scores, phases, norms):
    output = np.empty(len(scores))
    for phase in ("startup", "steady"):
        mask = phases == phase
        output[mask] = norms[phase].transform(scores[mask])
    return output


class Family:
    def __init__(self, columns, settings):
        self.columns, self.settings = tuple(columns), settings
        self.global_model, self.experts, self.support = None, {}, []
        self.threshold_raw = {}

    def fit(self, partitions, source_support):
        self.global_model = Detector.fit(*(partitions[n] for n in ("train", "calibration", "threshold")), self.columns, self.settings)
        adapters = sorted(set().union(*(set(p.adapterType) for p in partitions.values())))
        for adapter in adapters:
            parts = {n: p.loc[p.adapterType.eq(adapter)] for n, p in partitions.items()}
            reasons = [f"{n}<{minimum}" for n, minimum in (("train", 500), ("calibration", 100), ("threshold", 100))
                       if len(parts[n]) < minimum]
            reasons += [f"{n}_source_workflows<20" for n in parts if source_support.get(adapter, {}).get(n, 0) < 20]
            if not reasons:
                expert = Detector.fit(*(parts[n] for n in ("train", "calibration", "threshold")), self.columns, self.settings)
                self.experts[adapter] = expert
                self.threshold_raw[adapter] = np.column_stack([self.global_model.raw(parts["threshold"]), expert.threshold_raw])
            self.support.append({"adapter": adapter, **{n: len(p) for n, p in parts.items()},
                                 "source_workflows": source_support.get(adapter, {}), "expert_available": not reasons,
                                 "reason": "; ".join(reasons) or "supported"})
        self.set_operating_point(.99, .5, True)
        return self

    def set_operating_point(self, quantile, weight, phase_aware):
        if not 0 < quantile < 1 or not 0 <= weight <= 1: raise ValueError("Invalid operating point")
        self.quantile, self.global_weight, self.phase_aware = quantile, weight, phase_aware
        self.norms = {"GLOBAL": self.global_model.normalizers(phase_aware),
                      **{a: d.normalizers(phase_aware) for a, d in self.experts.items()}}
        self.thresholds = {}
        phases = self.global_model.threshold_phase
        score = normalize(self.global_model.threshold_raw, phases, self.norms["GLOBAL"])
        self.thresholds["GLOBAL"] = {}
        for phase in ("startup", "steady"):
            mask = phases == phase if phase_aware else np.ones(len(phases), bool)
            sample = score[mask] if mask.sum() >= 50 else score
            self.thresholds["GLOBAL"][phase] = np.repeat(np.quantile(sample, quantile), 3)
        for adapter, expert in self.experts.items():
            phases = expert.threshold_phase
            raw = self.threshold_raw[adapter]
            global_score = normalize(raw[:, 0], phases, self.norms["GLOBAL"])
            local_score = normalize(raw[:, 1], phases, self.norms[adapter])
            combined = weight*global_score+(1-weight)*local_score
            self.thresholds[adapter] = {}
            for phase in ("startup", "steady"):
                mask = phases == phase if phase_aware else np.ones(len(phases), bool)
                if mask.sum() < 50: mask = np.ones(len(phases), bool)
                self.thresholds[adapter][phase] = np.array([
                    self.thresholds["GLOBAL"][phase][0], np.quantile(local_score[mask], quantile),
                    np.quantile(combined[mask], quantile)])

    def components(self, frame):
        global_score = self.global_model.raw(frame)
        raw = np.repeat(global_score[:, None], 2, axis=1)
        coverage = frame.adapterType.isin(self.experts).to_numpy()
        for adapter, expert in self.experts.items():
            mask = frame.adapterType.eq(adapter).to_numpy()
            if mask.any(): raw[mask, 1] = expert.raw(frame.loc[mask])
        return raw, coverage

    def from_components(self, raw, adapters, phases):
        global_score = normalize(raw[:, 0], phases, self.norms["GLOBAL"])
        thresholds = np.vstack([self.thresholds["GLOBAL"][p] for p in phases])
        scores = np.repeat(global_score[:, None], 3, axis=1)
        for adapter in self.experts:
            mask = adapters == adapter
            if not mask.any(): continue
            local = normalize(raw[mask, 1], phases[mask], self.norms[adapter])
            scores[mask, 1] = local
            scores[mask, 2] = self.global_weight*global_score[mask]+(1-self.global_weight)*local
            thresholds[mask] = np.vstack([self.thresholds[adapter][p] for p in phases[mask]])
        # Unsupported Adapter has EXACT global scores/thresholds in all routes.
        return scores-thresholds

    def score(self, frame):
        raw, coverage = self.components(frame)
        margin = self.from_components(raw, frame.adapterType.to_numpy(), frame.phase.to_numpy())
        if not np.isfinite(margin).all(): raise ValueError("Non-finite score")
        return margin, margin > 0, coverage

    def records(self):
        return [{"algorithm": self.settings.algorithm, "settings": asdict(self.settings), "route": a,
                 "phase": p, "quantile": self.quantile, "global_weight": self.global_weight,
                 "phase_aware": self.phase_aware, "normalized_threshold": float(t[0 if a == "GLOBAL" else 1]),
                 "ensemble_threshold": float(t[2]), "normalizer_center": self.norms[a][p].center,
                 "normalizer_spread": self.norms[a][p].spread,
                 "raw_threshold": float(self.norms[a][p].center+self.norms[a][p].spread*t[0 if a == "GLOBAL" else 1])}
                for a, phases in self.thresholds.items() for p, t in phases.items()]


class Suite:
    def __init__(self, families): self.families = families

    def score(self, frame):
        parts = [self.families[a].score(frame) for a in ("if", "kmeans")]
        np.testing.assert_array_equal(parts[0][2], parts[1][2])
        return np.column_stack([p[0] for p in parts]), np.column_stack([p[1] for p in parts]), parts[0][2]

    def threshold_records(self): return [r for f in self.families.values() for r in f.records()]
