from dataclasses import dataclass
import numpy as np

@dataclass
class ScoreNormalizer:
    center: float
    spread: float

    @classmethod
    def fit(cls, scores):
        scores = np.asarray(scores, dtype=float)
        scores = scores[np.isfinite(scores)]
        if not len(scores):
            raise ValueError("No finite calibration scores")
        center = float(np.median(scores))
        spread = float(np.quantile(scores, .95) - center)
        if spread <= 1e-12:
            spread = max(float(np.std(scores)), 1e-6)
        return cls(center, spread)

    def transform(self, scores):
        # Unbounded: unlike ECDF percentile, novel extremes do not saturate at 1.
        return (np.asarray(scores) - self.center) / self.spread
