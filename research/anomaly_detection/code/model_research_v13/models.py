"""Novelty LOF and same-algorithm persistent-deviation extensions of v12.

Normal-only fitting. Separate calibration/threshold references. Joint scores
are recalibrated on normals, never an uncalibrated OR of anomaly flags.
"""
from dataclasses import dataclass, asdict
import numpy as np
from sklearn.neighbors import LocalOutlierFactor
from threadpoolctl import threadpool_limits

from process_anomaly_pipeline.features import ScoreNormalizer
from model_research_v5.models import Encoder, RawModel, Detector, Family, normalize
from model_research_v8.models import ChannelDetector, ChannelFamily
from model_research_v13.features import TRAFFIC, LATENCY, DRIFT

ALGORITHMS = ("if", "kmeans", "lof")
METHODS = tuple(f"{algorithm}_{route}" for algorithm in ALGORITHMS
                for route in ("global", "adapter", "ensemble"))


@dataclass(frozen=True)
class Settings:
    algorithm: str
    view: str = "anchored"
    samples: int = 512
    rotations: int = 1
    k: int = 6
    shape_distance: bool = False
    neighbors: int = 50
    metric: str = "euclidean"
    deduplicate: bool = True
    reference_cap: int = 2048
    seed: int = 42


class DensityRaw:
    def __init__(self, settings):
        self.settings = settings

    def fit(self, values, calibration):
        if self.settings.deduplicate:
            values = np.unique(values, axis=0)
        if len(values) > self.settings.reference_cap:
            ids = np.random.default_rng(self.settings.seed).choice(
                len(values), self.settings.reference_cap, replace=False)
            values = values[np.sort(ids)]
        if len(values) < 2:
            raise ValueError("Insufficient distinct LOF reference vectors")
        self.reference_size = len(values)
        self.model = LocalOutlierFactor(
            n_neighbors=min(self.settings.neighbors, len(values) - 1),
            novelty=True, contamination="auto", metric=self.settings.metric,
            algorithm="auto", n_jobs=2)
        with threadpool_limits(limits=2):
            self.model.fit(values)
        return self

    def score(self, values):
        # New calibration/validation/test observations ONLY, not fitted samples.
        # Log stabilizes density ratios near duplicated/near-identical normals.
        with threadpool_limits(limits=2):
            return np.log(np.maximum(-self.model.score_samples(values), 1e-8))


class DensityChannelDetector(ChannelDetector):
    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        result = cls(None, None, None, None, calibration.phase.to_numpy(), threshold.phase.to_numpy())
        result.channels = []
        latency = LATENCY if settings.view == "tails" else LATENCY + DRIFT
        for cols, eligible in ((TRAFFIC, "traffic_assessed"), (latency, "latency_assessed")):
            tr, ca = train.loc[train[eligible]], calibration.loc[calibration[eligible]]
            encoder = Encoder.fit(tr, cols)
            raw = DensityRaw(settings).fit(encoder.transform(tr), encoder.transform(ca))
            norm = ScoreNormalizer.fit(raw.score(encoder.transform(ca)))
            result.channels.append((encoder, raw, norm, eligible))
        result.calibration_channels = result.explain(calibration)
        result.threshold_channels = result.explain(threshold)
        result.latency_weight = 1.
        result.refresh()
        return result


def supported_parts(partitions, support):
    for adapter in sorted(set().union(*(set(p.adapterType) for p in partitions.values()))):
        parts = {n: p.loc[p.adapterType.eq(adapter)] for n, p in partitions.items()}
        reasons = [f"{n}<{minimum}" for n, minimum in (("train", 500), ("calibration", 100), ("threshold", 100))
                   if len(parts[n]) < minimum]
        reasons += [f"{n}_source_workflows<20" for n in parts if support.get(adapter, {}).get(n, 0) < 20]
        reasons += [f"{n}_{channel}_support" for n in parts for channel in ("traffic_assessed", "latency_assessed")
                    if int(parts[n][channel].sum()) < (80 if n == "train" else 20)]
        yield adapter, parts, reasons


class DensityFamily(ChannelFamily):
    def fit(self, partitions, source_support):
        self.global_model = DensityChannelDetector.fit(
            *(partitions[n] for n in ("train", "calibration", "threshold")), (), self.settings)
        self.threshold_channels = {}
        for adapter, parts, reasons in supported_parts(partitions, source_support):
            if not reasons:
                expert = DensityChannelDetector.fit(
                    *(parts[n] for n in ("train", "calibration", "threshold")), (), self.settings)
                self.experts[adapter] = expert
                self.threshold_channels[adapter] = np.column_stack([
                    self.global_model.explain(parts["threshold"]), expert.threshold_channels])
            self.support.append({"adapter": adapter, **{n: len(p) for n, p in parts.items()},
                                 "expert_available": not reasons, "reason": ";".join(reasons) or "supported"})
        self.set_operating_point(.99, .5, True, 1., "mean")
        return self


class DriftDetector(Detector):
    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        eligible = lambda f: f.traffic_assessed & f.latency_assessed
        tr, ca = train.loc[eligible(train)], calibration.loc[eligible(calibration)]
        encoder = Encoder.fit(tr, columns)
        raw = RawModel(settings).fit(encoder.transform(tr), encoder.transform(ca))
        result = cls(encoder, raw, None, None, calibration.phase.to_numpy(), threshold.phase.to_numpy())
        result.calibration_raw = result.raw(calibration)
        result.threshold_raw = result.raw(threshold)
        return result

    def raw(self, frame):
        mask = (frame.traffic_assessed & frame.latency_assessed).to_numpy(bool)
        result = np.full(len(frame), np.nan)
        if mask.any():
            result[mask] = self.raw_model.score(self.encoder.transform(frame.loc[mask]))
        return result

    def normalizers(self, phase_aware):
        valid = np.isfinite(self.calibration_raw)
        norms = {}
        for phase in ("startup", "steady"):
            mask = valid & ((self.calibration_phase == phase) if phase_aware else True)
            norms[phase] = ScoreNormalizer.fit(self.calibration_raw[mask if mask.sum() >= 20 else valid])
        return norms


class PersistentFamily:
    """Preserve v12 plus a normal-trained same-algorithm drift branch."""
    def __init__(self, base, settings):
        self.base, self.settings = base, settings
        self.experts = base.experts
        self.support = base.support

    def fit(self, partitions, source_support):
        columns = DRIFT if self.settings.view == "drift" else DRIFT + ("residual_mean_5m", "duration_upper_mean_5m")
        self.global_model = DriftDetector.fit(
            *(partitions[n] for n in ("train", "calibration", "threshold")), columns, self.settings)
        self.drift_experts = {}
        for adapter, parts, reasons in supported_parts(partitions, source_support):
            if adapter in self.experts and not reasons:
                self.drift_experts[adapter] = DriftDetector.fit(
                    *(parts[n] for n in ("train", "calibration", "threshold")), columns, self.settings)
        self.norms = {"GLOBAL": self.global_model.normalizers(True),
                      **{a: d.normalizers(True) for a, d in self.drift_experts.items()}}
        self.calibration_frame = partitions["calibration"]
        self.threshold_frame = partitions["threshold"]
        self.calibration_components = self.components(self.calibration_frame)[0]
        self.threshold_components = self.components(self.threshold_frame)[0]
        m = self.calibration_components[:, :3]
        self.base_spread = np.maximum(np.quantile(m, .95, axis=0) - np.median(m, axis=0), .1)
        self.set_operating_point(.99, .5, True, 1., "max")
        return self

    def components(self, frame):
        baseline, _, covered = self.base.score(frame)
        global_raw = self.global_model.raw(frame)
        local_raw = global_raw.copy()
        for adapter, expert in self.drift_experts.items():
            mask = frame.adapterType.eq(adapter).to_numpy()
            if mask.any():
                local_raw[mask] = expert.raw(frame.loc[mask])
        return np.column_stack([baseline, global_raw, local_raw]), covered

    def evidence(self, raw, adapters, phases):
        glob = normalize(raw[:, 3], phases, self.norms["GLOBAL"])
        pair = np.repeat(glob[:, None], 3, axis=1)
        for adapter, expert in self.drift_experts.items():
            mask = adapters == adapter
            if mask.any():
                local = normalize(raw[mask, 4], phases[mask], self.norms[adapter])
                pair[mask, 1] = local
                pair[mask, 2] = self.global_weight * glob[mask] + (1 - self.global_weight) * local
        return pair

    def joint(self, raw, adapters, phases):
        evidence = self.evidence(raw, adapters, phases)
        cut = np.vstack([self.aux_cut["GLOBAL"][p] for p in phases])
        for adapter in self.drift_experts:
            mask = adapters == adapter
            if mask.any():
                cut[mask] = np.vstack([self.aux_cut[adapter][p] for p in phases[mask]])
        auxiliary = self.latency_weight * (evidence - cut)
        auxiliary = np.where(np.isfinite(auxiliary), auxiliary, -np.inf)
        result = np.maximum(raw[:, :3] / self.base_spread, auxiliary)
        return result

    def set_operating_point(self, q, weight, phase_aware=True, latency_weight=1., combination="max"):
        self.quantile, self.global_weight, self.latency_weight = q, weight, latency_weight
        cal, th = self.calibration_frame, self.threshold_frame
        raw = self.calibration_components
        ev = self.evidence(raw, cal.adapterType.to_numpy(), cal.phase.to_numpy())
        self.aux_cut = {}
        for adapter in ("GLOBAL", *self.drift_experts):
            self.aux_cut[adapter] = {}
            for phase in ("startup", "steady"):
                mask = cal.phase.eq(phase).to_numpy() & (np.ones(len(cal), bool) if adapter == "GLOBAL" else cal.adapterType.eq(adapter).to_numpy())
                for_values = ev[mask]
                valid = np.isfinite(for_values).all(axis=1)
                if valid.sum() < 20:
                    for_values = ev[np.isfinite(ev).all(axis=1)]
                else:
                    for_values = for_values[valid]
                cutoff = np.quantile(for_values, .95, axis=0)
                if adapter == "GLOBAL": cutoff[:] = cutoff[0]
                self.aux_cut[adapter][phase] = cutoff
        scores = self.joint(self.threshold_components, th.adapterType.to_numpy(), th.phase.to_numpy())
        self.thresholds = {}
        for adapter in ("GLOBAL", *self.experts):
            self.thresholds[adapter] = {}
            for phase in ("startup", "steady"):
                mask = th.phase.eq(phase).to_numpy() & (np.ones(len(th), bool) if adapter == "GLOBAL" else th.adapterType.eq(adapter).to_numpy())
                sample = scores[mask] if mask.sum() >= 50 else scores
                threshold = np.quantile(sample, q, axis=0)
                if adapter == "GLOBAL": threshold[:] = threshold[0]
                else: threshold[0] = self.thresholds["GLOBAL"][phase][0]
                self.thresholds[adapter][phase] = threshold

    def from_components(self, raw, adapters, phases):
        scores = self.joint(raw, adapters, phases)
        thresholds = np.vstack([self.thresholds["GLOBAL"][p] for p in phases])
        for adapter in self.experts:
            mask = adapters == adapter
            if mask.any(): thresholds[mask] = np.vstack([self.thresholds[adapter][p] for p in phases[mask]])
        # No eligible completed history => retain baseline evidence, not NaNs.
        invalid = ~np.isfinite(scores)
        scores[invalid] = np.broadcast_to(raw[:, :3] / self.base_spread, scores.shape)[invalid]
        return scores - thresholds

    def score(self, frame):
        raw, covered = self.components(frame)
        margin = self.from_components(raw, frame.adapterType.to_numpy(), frame.phase.to_numpy())
        if not np.isfinite(margin).all(): raise ValueError("Non-finite margin")
        return margin, margin > 0, covered

    def records(self):
        return [{"algorithm": self.settings.algorithm, "settings": asdict(self.settings),
                 "route": adapter, "phase": phase, "quantile": self.quantile,
                 "global_weight": self.global_weight, "drift_weight": self.latency_weight,
                 "thresholds": threshold.tolist()}
                for adapter, values in self.thresholds.items() for phase, threshold in values.items()]


class PreservedFamily:
    """A frozen v12 candidate; selection may retain it instead of a regression."""
    def __init__(self, base, algorithm):
        self.base, self.algorithm = base, algorithm
        self.experts, self.support = base.experts, base.support

    def components(self, frame):
        margin, _, coverage = self.base.score(frame)
        return margin, coverage

    def from_components(self, raw, adapters, phases):
        return raw

    def score(self, frame):
        return self.base.score(frame)

    def records(self):
        return [{"algorithm": self.algorithm, "preserved_v12": True, "original": r}
                for r in self.base.records()]
