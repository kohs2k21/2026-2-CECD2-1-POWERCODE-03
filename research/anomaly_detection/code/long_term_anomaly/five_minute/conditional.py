"""Normal-reference window calibration, without monitoring-history state.

The reference supplies 672 reviewed normal one-hour windows. Mapping each
current summary to that group's normal upper tail reduces differences in
normal variance/tails across Process groups before a shared density is fit.
No anomaly label, group identifier, scenario, or monitoring neighbor enters it.
"""
from dataclasses import dataclass
import numpy as np

from .model import FineWindowModel, FineReference, REFERENCE_BINS, _arrays, _readonly


@dataclass(frozen=True)
class ConditionalReference(FineReference):
    normal_windows: tuple = ()
    normal_tail_scale: np.ndarray = None

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "normal_windows", tuple(_readonly(v) for v in self.normal_windows))
        object.__setattr__(self, "normal_tail_scale", _readonly(self.normal_tail_scale))

    def __reduce__(self):
        return type(self), (self.profile, self.center, self.scale, self.rho, self.slope,
            self.active, self.midpoint, self.reference_bins, self.normal_windows, self.normal_tail_scale)


class ConditionalWindowModel(FineWindowModel):
    def prepare_reference(self, x, o, a):
        ref = super().prepare_reference(x, o, a)
        x, valid = _arrays(x, o, a)
        # Historical positions use their true phase relative to the reference;
        # this negative offset is private reference fitting, never monitoring.
        historical = self._window_block(ref, x, valid, 12, -REFERENCE_BINS)
        normal, scales = [], []
        for column in historical.T:
            values = np.sort(column[np.isfinite(column)])
            normal.append(values)
            if len(values):
                q50, q90, q99 = np.quantile(values, [.5, .9, .99])
                scales.append(max(q90 - q50, q99 - q90, self.scale_floor))
            else:
                scales.append(self.scale_floor)
        return ConditionalReference(ref.profile, ref.center, ref.scale, ref.rho, ref.slope,
            ref.active, ref.midpoint, ref.reference_bins, tuple(normal), np.asarray(scales))

    def sequence_features(self, reference, x, o, a, analysis_hours, offset_bins=0):
        features = super().sequence_features(reference, x, o, a, analysis_hours, offset_bins)
        if analysis_hours != 1:
            return features
        if not isinstance(reference, ConditionalReference):
            raise TypeError("Conditional reference is required")
        result = np.full(features.shape, np.nan)
        for j, values in enumerate(reference.normal_windows):
            if not len(values):
                continue
            good = np.isfinite(features[:, j])
            query = features[good, j]
            rank = np.searchsorted(values, query, side="left")
            tail = (len(values) - rank + 1) / (len(values) + 1)
            result[good, j] = -np.log(tail) + np.maximum(query - values[-1], 0) / reference.normal_tail_scale[j]
        return result
