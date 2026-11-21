"""One global strategy, one supported Adapter strategy, their actual ensemble."""
import numpy as np
from model_research_v14.models import CalibratedFamily


class RoutedFusion:
    def __init__(self, global_family, adapter_family):
        self.global_family, self.adapter_family = global_family, adapter_family
        self.experts, self.support = adapter_family.experts, adapter_family.support
        self.mode, self.weight = "mean", .5
        self.scale, self.samples, self.thresholds = {}, {}, {}

    @staticmethod
    def gate(frame): return frame.traffic_assessed.to_numpy(int)+2*frame.latency_assessed.to_numpy(int)

    def fit_reference(self, frame):
        g = self.global_family.score(frame)[0][:, 0]
        local = self.adapter_family.score(frame)[0][:, 1]
        adapters, phases, gates = frame.adapterType.to_numpy(), frame.phase.to_numpy(), self.gate(frame)
        self.case_support = {}
        for adapter in self.experts:
            for phase in ("startup", "steady"):
                for gate in range(4):
                    mask = (adapters == adapter) & (phases == phase) & (gates == gate)
                    n = len(np.unique(frame.case_id.to_numpy()[mask]))
                    fallback = mask if mask.sum() >= 30 else (adapters == adapter)
                    if fallback.sum() < 30: fallback = phases == phase
                    key = (adapter, phase, gate)
                    pair = np.column_stack([g[fallback], local[fallback]])
                    self.scale[key] = np.maximum(np.quantile(pair, .95, axis=0)-np.median(pair, axis=0), .1)
                    self.samples[key] = pair/self.scale[key]
                    self.case_support[key] = n
        return self

    def set_point(self, weight, mode, quantile, alpha):
        self.weight, self.mode, self.quantile, self.alpha = weight, mode, quantile, alpha
        for key, pairs in self.samples.items():
            combined = self.combine(pairs)
            self.thresholds[key] = float(np.quantile(combined, quantile))*alpha*min(self.case_support[key]/30, 1.)

    def combine(self, pairs):
        if self.mode == "max": return np.maximum(self.weight*pairs[:, 0], (1-self.weight)*pairs[:, 1])
        return self.weight*pairs[:, 0]+(1-self.weight)*pairs[:, 1]

    def components(self, frame):
        global_margin = self.global_family.score(frame)[0][:, 0]
        local_margin = self.adapter_family.score(frame)[0][:, 1]
        covered = frame.adapterType.isin(self.experts).to_numpy()
        local_margin = np.where(covered, local_margin, global_margin)
        return np.column_stack([global_margin, local_margin, self.gate(frame)]), covered

    def from_components(self, raw, adapters, phases):
        gates = raw[:, 2].astype(int)
        result = np.column_stack([raw[:, 0], raw[:, 1], raw[:, 0]])
        for key, scale in self.scale.items():
            adapter, phase, gate = key
            mask = (adapters == adapter) & (phases == phase) & (gates == gate)
            if mask.any(): result[mask, 2] = self.combine(raw[mask, :2]/scale)-self.thresholds[key]
        return result

    def score(self, frame):
        raw, covered = self.components(frame)
        result = self.from_components(raw, frame.adapterType.to_numpy(), frame.phase.to_numpy())
        np.testing.assert_array_equal(result[~covered, 0], result[~covered, 1])
        np.testing.assert_array_equal(result[~covered, 0], result[~covered, 2])
        return result, result > 0, covered

    def records(self):
        return [{"route": "ensemble", "adapter": a, "phase": p, "evidence_gate": g,
                 "global_weight": self.weight, "combination": self.mode, "normal_quantile": self.quantile,
                 "alpha": self.alpha, "independent_calibration_cases": self.case_support[key],
                 "threshold": threshold, "global_and_local_scale": self.scale[key].tolist()}
                for key, threshold in self.thresholds.items() for a, p, g in (key,)]

    def strip_reference(self): del self.samples
