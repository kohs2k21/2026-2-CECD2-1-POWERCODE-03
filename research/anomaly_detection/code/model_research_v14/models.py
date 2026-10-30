"""Normal-control calibration, with exact unsupported fallback.

Score thresholds are learned from a separate NORMAL case block. Labels are
used only to choose an operating point on development/search cases. Neither
final benchmark controls nor confirmation cases are calibration references.
In this pilot, some calibration normals are paired with observed SEARCH
cases. They are disjoint from normal search cases, not all search case IDs.
"""
import numpy as np


class CalibratedFamily:
    def __init__(self, base, mode="phase_eligibility", minimum=100):
        self.base, self.mode, self.minimum = base, mode, minimum
        self.experts, self.support = base.experts, base.support
        self.quantiles = [None] * 3

    def components(self, frame):
        margin, _, coverage = self.base.score(frame)
        # Categorical evidence gates, not labels or scenario names.
        gate = (frame.traffic_assessed.to_numpy(np.int8) +
                2 * frame.latency_assessed.to_numpy(np.int8))
        return np.column_stack([margin, gate]), coverage

    def key(self, adapter, phase, gate, route):
        local = adapter if route and adapter in self.experts else "GLOBAL"
        return (local, phase, int(gate) if self.mode == "phase_eligibility" else -1)

    def fit_reference(self, raw, adapters, phases):
        self.samples = {}
        for route in range(3):
            for adapter in ("GLOBAL", *self.experts):
                for phase in ("startup", "steady"):
                    gates = range(4) if self.mode == "phase_eligibility" else (-1,)
                    for gate in gates:
                        mask = phases == phase
                        if adapter != "GLOBAL": mask &= adapters == adapter
                        if gate != -1: mask &= raw[:, 3] == gate
                        # Pool only eligibility/phase when a local stratum is small.
                        if mask.sum() < self.minimum:
                            mask = phases == phase
                            if gate != -1: mask &= raw[:, 3] == gate
                        if mask.sum() < self.minimum: mask = phases == phase
                        if mask.sum() < self.minimum: mask = np.ones(len(raw), bool)
                        column = route if adapter != "GLOBAL" else 0
                        self.samples[(route, adapter, phase, gate)] = np.sort(raw[mask, column])
        return self

    def set_quantiles(self, quantiles):
        self.quantiles = list(quantiles)
        self.thresholds = {key: float(np.quantile(values, quantiles[key[0]]))
                           for key, values in self.samples.items()}
        # Unsupported adapter/local/ensemble is EXACTLY the global route.
        for key in list(self.thresholds):
            route, adapter, phase, gate = key
            if adapter == "GLOBAL":
                self.thresholds[key] = self.thresholds[(0, adapter, phase, gate)]

    def from_components(self, raw, adapters, phases):
        margin = raw[:, :3].copy()
        for route in range(3):
            keys = [self.key(a, p, g, route) for a, p, g in zip(adapters, phases, raw[:, 3])]
            cutoff = np.fromiter((self.thresholds[(route, *k)] for k in keys), float, len(raw))
            margin[:, route] -= cutoff
        return margin

    def score(self, frame):
        raw, covered = self.components(frame)
        margin = self.from_components(raw, frame.adapterType.to_numpy(), frame.phase.to_numpy())
        if not np.isfinite(margin).all(): raise ValueError("Non-finite detector margin")
        if (~covered).any():
            np.testing.assert_array_equal(margin[~covered, 0], margin[~covered, 1])
            np.testing.assert_array_equal(margin[~covered, 0], margin[~covered, 2])
        return margin, margin > 0, covered

    def records(self):
        return [{"route": route, "adapter": adapter, "phase": phase, "evidence_gate": gate,
                 "quantile": self.quantiles[route], "threshold_on_base_margin": threshold,
                 "normal_reference_windows": len(self.samples[key]), "calibration_mode": self.mode}
                for key, threshold in self.thresholds.items()
                for route, adapter, phase, gate in (key,)]

    def strip_reference(self):
        """Inference needs scalar thresholds, not validation reference scores."""
        del self.samples
