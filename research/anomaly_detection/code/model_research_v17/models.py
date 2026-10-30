"""Base-preserving max evidence; final joint calibration on independent cases."""
import numpy as np
from model_research_v5.models import normalize
from model_research_v16.models import SharedTemporalFamily


class CountTemporalFamily(SharedTemporalFamily):
    def set_operating_point(self, q, weight, phase_aware=True, latency_weight=1., combination="max"):
        super().set_operating_point(q, weight, phase_aware, latency_weight, combination)
        # A separate source-normal threshold partition, not the validation/final
        # labels. Keep the old base decision boundary at zero.
        th = self.threshold_frame
        evidence = normalize(self.threshold_components[:, 3], th.phase.to_numpy(), self.norms["GLOBAL"])
        self.aux_cut = {"GLOBAL": {}}
        valid = np.isfinite(evidence)
        for phase in ("startup", "steady"):
            mask = valid & th.phase.eq(phase).to_numpy()
            if mask.sum() < 50: mask = valid
            self.aux_cut["GLOBAL"][phase] = np.repeat(np.quantile(evidence[mask], q), 3)

    def from_components(self, raw, adapters, phases):
        margin = self.joint(raw, adapters, phases)
        unsupported = ~np.isin(adapters, list(self.experts))
        margin[unsupported, 1] = margin[unsupported, 0]
        margin[unsupported, 2] = margin[unsupported, 0]
        return margin

    def records(self):
        return [{"algorithm": self.settings.algorithm, "temporal_normal_quantile": self.quantile,
                 "old_boundary_preserved_before_joint_case_calibration": True,
                 "temporal_source": "count-conditioned residual_mean_1m",
                 "shared_global_temporal_model": True, "normalized_input_resolution": .001,
                 "phase": p, "auxiliary_thresholds": c.tolist()} for p, c in self.aux_cut["GLOBAL"].items()]
