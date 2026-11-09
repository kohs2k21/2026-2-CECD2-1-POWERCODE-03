"""Same-algorithm temporal branches and regularized normal calibration."""
from dataclasses import dataclass
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.covariance import OAS
from threadpoolctl import threadpool_limits
from model_research_v5.models import Encoder, RawModel, Detector
from model_research_v13.models import DensityRaw, PersistentFamily, supported_parts
from model_research_v14.models import CalibratedFamily
from model_research_v15.features import TEMPORAL


@dataclass(frozen=True)
class Settings:
    algorithm: str
    view: str = "temporal"
    samples: int = 512
    rotations: int = 1
    tree_count: int = 256
    k: int = 6
    shape_distance: bool = False
    neighbors: int = 50
    deduplicate: bool = True
    reference_cap: int = 2048
    metric: str = "euclidean"
    whiten: bool = False
    seed: int = 42


class AlgorithmRaw:
    def __init__(self, settings): self.settings = settings

    def fit(self, values, calibration):
        self.center = np.zeros(values.shape[1]); self.transformer = np.eye(values.shape[1])
        if self.settings.whiten:
            covariance = OAS().fit(values)
            self.center = covariance.location_
            eigenvalues, vectors = np.linalg.eigh(covariance.covariance_)
            self.transformer = vectors @ np.diag(1/np.sqrt(np.maximum(eigenvalues, .05))) @ vectors.T
        values, calibration = self.transform(values), self.transform(calibration)
        if self.settings.algorithm == "if":
            self.raw = IsolationForest(n_estimators=self.settings.tree_count, max_samples=min(self.settings.samples, len(values)),
                contamination="auto", bootstrap=False, max_features=1., random_state=self.settings.seed, n_jobs=2)
            with threadpool_limits(limits=2): self.raw.fit(values)
        elif self.settings.algorithm == "lof": self.raw = DensityRaw(self.settings).fit(values, calibration)
        else: self.raw = RawModel(self.settings).fit(values, calibration)
        return self

    def transform(self, values): return ((values-self.center) @ self.transformer).astype(np.float32)

    def score(self, values):
        values = self.transform(values)
        if self.settings.algorithm == "if":
            with threadpool_limits(limits=2): return -self.raw.score_samples(values)
        return self.raw.score(values)


class TemporalDetector(Detector):
    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        tr, ca = train.loc[train.temporal_assessed], calibration.loc[calibration.temporal_assessed]
        if len(tr) < 80 or len(ca) < 20: raise ValueError("Insufficient observed temporal support")
        encoder = Encoder.fit(tr, columns)
        raw = AlgorithmRaw(settings).fit(encoder.transform(tr), encoder.transform(ca))
        result = cls(encoder, raw, None, None, calibration.phase.to_numpy(), threshold.phase.to_numpy())
        result.calibration_raw = result.raw(calibration); result.threshold_raw = result.raw(threshold)
        return result

    def raw(self, frame):
        eligible = frame.temporal_assessed.to_numpy(bool)
        out = np.full(len(frame), np.nan)
        if eligible.any(): out[eligible] = self.raw_model.score(self.encoder.transform(frame.loc[eligible]))
        return out

    def normalizers(self, phase_aware):
        from process_anomaly_pipeline.features import ScoreNormalizer
        valid = np.isfinite(self.calibration_raw)
        result = {}
        for phase in ("startup", "steady"):
            mask = valid & ((self.calibration_phase == phase) if phase_aware else True)
            result[phase] = ScoreNormalizer.fit(self.calibration_raw[mask if mask.sum() >= 20 else valid])
        return result


class TemporalFamily(PersistentFamily):
    def from_components(self, raw, adapters, phases):
        margin = super().from_components(raw, adapters, phases)
        unsupported = ~np.isin(adapters, list(self.experts))
        margin[unsupported, 1] = margin[unsupported, 0]
        margin[unsupported, 2] = margin[unsupported, 0]
        return margin

    def fit(self, partitions, source_support):
        self.global_model = TemporalDetector.fit(*(partitions[n] for n in ("train", "calibration", "threshold")), TEMPORAL, self.settings)
        self.drift_experts = {}
        for adapter, parts, reasons in supported_parts(partitions, source_support):
            if adapter in self.experts and not reasons and all(p.temporal_assessed.sum() >= (80 if n == "train" else 20) for n, p in parts.items()):
                self.drift_experts[adapter] = TemporalDetector.fit(*(parts[n] for n in ("train", "calibration", "threshold")), TEMPORAL, self.settings)
        self.norms = {"GLOBAL": self.global_model.normalizers(True), **{a: d.normalizers(True) for a, d in self.drift_experts.items()}}
        self.calibration_frame, self.threshold_frame = partitions["calibration"], partitions["threshold"]
        self.calibration_components = self.components(self.calibration_frame)[0]
        self.threshold_components = self.components(self.threshold_frame)[0]
        m = self.calibration_components[:, :3]
        self.base_spread = np.maximum(np.quantile(m, .95, axis=0)-np.median(m, axis=0), .1)
        self.set_operating_point(.99, .5, True, .5, "max")
        return self


class RegularizedCalibration(CalibratedFamily):
    def set_point(self, quantiles, shrinkage):
        self.shrinkage = list(shrinkage)
        super().set_quantiles(quantiles)
        for key in self.thresholds: self.thresholds[key] *= shrinkage[key[0]]
        for key in list(self.thresholds):
            route, adapter, phase, gate = key
            if adapter == "GLOBAL": self.thresholds[key] = self.thresholds[(0, adapter, phase, gate)]

    def from_components(self, raw, adapters, phases):
        out = raw[:, :3].copy(); gates = raw[:, 3].astype(int)
        for adapter in ("GLOBAL", *self.experts):
            a = np.ones(len(raw), bool) if adapter == "GLOBAL" else adapters == adapter
            if not a.any(): continue
            for phase in ("startup", "steady"):
                for gate in (range(4) if self.mode == "phase_eligibility" else (-1,)):
                    mask = a & (phases == phase)
                    if gate != -1: mask &= gates == gate
                    if not mask.any(): continue
                    if adapter == "GLOBAL":
                        out[mask] -= np.asarray([self.thresholds[(r, adapter, phase, gate)] for r in range(3)])
                    else:
                        # Global was already applied; replace local/ensemble cuts.
                        for r in (1, 2):
                            out[mask, r] += self.thresholds[(r, "GLOBAL", phase, gate)]-self.thresholds[(r, adapter, phase, gate)]
        return out

    def records(self):
        return [{**r, "calibration_shrinkage": self.shrinkage[r["route"]]} for r in super().records()]
