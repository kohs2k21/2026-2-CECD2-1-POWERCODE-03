from dataclasses import dataclass
from time import perf_counter

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler


@dataclass
class Detector:
    kind: str
    threshold: float
    scaler: object = None
    forest: object = None
    center: float = 0.0
    scale: float = 1.0
    scale_method: str = "MAD"
    fit_seconds: float = 0.0

    def score(self, values):
        x = np.asarray(values, dtype=float)
        if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
            raise ValueError("finite univariate input required")
        if self.kind == "iforest":
            return -self.forest.score_samples(self.scaler.transform(x.reshape(-1, 1)))
        return np.abs(x - self.center) / self.scale

    def predict(self, values):
        return self.score(values) > self.threshold


def fit_detectors(train, calibration, seed, config):
    train = np.asarray(train, dtype=float)
    calibration = np.asarray(calibration, dtype=float)
    if min(len(train), len(calibration)) < 100 or not np.isfinite(train).all() or not np.isfinite(calibration).all():
        raise ValueError("insufficient or invalid reference values")
    if np.ptp(train) == 0:
        raise ValueError("constant training signal")
    started = perf_counter()
    scaler = RobustScaler().fit(train.reshape(-1, 1))
    forest = IsolationForest(n_estimators=config["forest_trees"],
        max_samples=config["forest_max_samples"], random_state=seed, n_jobs=1)
    forest.fit(scaler.transform(train.reshape(-1, 1)))
    model = Detector("iforest", 0, scaler=scaler, forest=forest)
    model.threshold = float(np.quantile(model.score(calibration), config["calibration_quantile"]))
    model.fit_seconds = perf_counter() - started
    started = perf_counter()
    center = float(np.median(train))
    candidates = [(1.4826 * float(np.median(np.abs(train - center))), "MAD"),
                  (float(np.subtract(*np.quantile(train, [.75, .25]))) / 1.349, "IQR"),
                  (float(np.std(train)), "std-fallback")]
    scale, method = next((v, name) for v, name in candidates if v > 1e-9)
    robust = Detector("median_mad", 0, center=center, scale=scale, scale_method=method)
    robust.threshold = float(np.quantile(robust.score(calibration), config["calibration_quantile"]))
    robust.fit_seconds = perf_counter() - started
    return [model, robust]
