"""Retain Adapter experts; share statistically better-supported temporal context."""
import numpy as np
from model_research_v5.models import Encoder
from model_research_v15.models import AlgorithmRaw, TemporalDetector, TemporalFamily, RegularizedCalibration
from model_research_v15.features import TEMPORAL


class StableRaw(AlgorithmRaw):
    def transform(self, values):
        # Normalized feature resolution, far below 1% changes. Removes tiny
        # float timestamp subtraction jitter before fit AND before score.
        # Density models must not treat 1e-7 numeric differences as new modes.
        return np.round(super().transform(values), 3)


class StableDetector(TemporalDetector):
    @classmethod
    def fit(cls, train, calibration, threshold, columns, settings):
        tr, ca = train.loc[train.temporal_assessed], calibration.loc[calibration.temporal_assessed]
        encoder = Encoder.fit(tr, columns)
        raw = StableRaw(settings).fit(encoder.transform(tr), encoder.transform(ca))
        result = cls(encoder, raw, None, None, calibration.phase.to_numpy(), threshold.phase.to_numpy())
        result.calibration_raw = result.raw(calibration); result.threshold_raw = result.raw(threshold)
        return result


class SharedTemporalFamily(TemporalFamily):
    def fit(self, partitions, support):
        self.global_model = StableDetector.fit(*(partitions[n] for n in ("train", "calibration", "threshold")), TEMPORAL, self.settings)
        self.drift_experts = {}  # Original Adapter expert is still in self.base.
        self.norms = {"GLOBAL": self.global_model.normalizers(True)}
        self.calibration_frame, self.threshold_frame = partitions["calibration"], partitions["threshold"]
        self.calibration_components = self.components(self.calibration_frame)[0]
        self.threshold_components = self.components(self.threshold_frame)[0]
        m = self.calibration_components[:, :3]
        self.base_spread = np.maximum(np.quantile(m, .95, axis=0)-np.median(m, axis=0), .1)
        self.set_operating_point(.975, .5, True, .5, "max")
        return self


class CaseAwareCalibration(RegularizedCalibration):
    def fit_reference(self, raw, adapters, phases, case_ids=None):
        if case_ids is None: raise ValueError("Independent calibration case IDs are required")
        super().fit_reference(raw, adapters, phases)
        self.case_support = {}
        for key in self.samples:
            _, adapter, phase, gate = key
            mask = phases == phase
            if adapter != "GLOBAL": mask &= adapters == adapter
            if gate != -1: mask &= raw[:, 3] == gate
            self.case_support[key] = len(np.unique(case_ids[mask]))
        return self

    def set_point(self, quantiles, shrinkage):
        super().set_point(quantiles, shrinkage)
        for key in self.thresholds: self.thresholds[key] *= min(1., self.case_support[key]/30.)
        for key in list(self.thresholds):
            r, a, p, g = key
            if a == "GLOBAL": self.thresholds[key] = self.thresholds[(0, a, p, g)]

    def records(self):
        rows = super().records()
        for row in rows:
            key = (row["route"], row["adapter"], row["phase"], row["evidence_gate"])
            row["independent_calibration_cases"] = self.case_support[key]
            row["case_reliability_weight"] = min(1., self.case_support[key]/30.)
        return rows
