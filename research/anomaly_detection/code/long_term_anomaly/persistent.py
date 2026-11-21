"""Long-event focused score: optional local evidence, stronger segment evidence.

This is a tuning parameter, not a task-specific router. Exactly the same
component weights and threshold apply to every scenario and operating group.
"""
import numpy as np
from .models import LongTermDetector, feature_names


class PersistentDetector(LongTermDetector):
    def __init__(self, setting, energy_weight=1.0, local_weight=.3):
        super().__init__(setting, energy_weight)
        self.local_weight = float(local_weight)

    def score_components(self, components):
        names = feature_names(self.setting)
        values = (components - self.center_) / self.spread_
        weights = []
        for name in names:
            weight = self.energy_weight if "energy" in name else 1.0
            if name.startswith("local_"):
                weight *= self.local_weight
            if name.endswith(":items_per_process"):
                weight = 0.0
            weights.append(weight)
        values *= np.asarray(weights)
        valid = np.isfinite(values).any(axis=1)
        score = np.full(len(components), np.nan)
        reasons = np.full(len(components), "collection_missing", dtype=object)
        indices = np.argmax(np.nan_to_num(values[valid], nan=-np.inf), axis=1)
        score[valid] = values[valid, indices]
        reasons[valid] = np.asarray(names, dtype=object)[indices]
        return score, reasons

    def record(self):
        return {**super().record(), "local_evidence_weight": self.local_weight,
                "context_work_volume_is_not_anomaly_target": True}
