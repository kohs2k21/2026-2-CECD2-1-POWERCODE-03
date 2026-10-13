"""Selected global inference using the published encoders and score calibration.

Trusted pickle loading is restricted to the repository's exact inventory. Fitted
synthetic references are a mechanical pilot, never a customer accuracy estimate.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import warnings

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.exceptions import InconsistentVersionWarning
from threadpoolctl import threadpool_limits

RESEARCH = Path(__file__).resolve().parents[2] / "anomaly_detection"
CODE = RESEARCH / "code"
if str(CODE) not in sys.path:
    sys.path.insert(0, str(CODE))
from model_evaluation.models import ComparisonConfig, Detector, WINDOW_FEATURES
from process_anomaly_pipeline.features import raw_features


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_digest(frame):
    # Stable normalized representation, not an export of raw customer rows.
    serial = frame.to_json(orient="split", date_format="iso", double_precision=15)
    return hashlib.sha256(serial.encode()).hexdigest()


def detector_margin(detector, frame):
    """The same global margin as SixModels.score, without scoring five peers."""
    if frame.empty:
        return np.empty(0, dtype=float)
    if "log_duration_ms" in detector.encoder.columns:
        if tuple(detector.encoder.columns) != tuple(raw_features(frame).columns):
            raise ValueError("PROCESS feature schema mismatch")
    values = detector.encoder.transform(frame)
    if detector.algorithm == "if":
        raw = -detector.model.score_samples(values)
    elif detector.algorithm == "kmeans":
        labels = detector.model.predict(values)
        raw = np.linalg.norm(values - detector.model.cluster_centers_[labels], axis=1).astype(float)
    else:
        raise ValueError("Unsupported algorithm")
    margins = detector.normalizer.transform(raw) - detector.threshold
    if not np.isfinite(margins).all():
        raise ValueError("Non-finite margin; prediction unavailable")
    return np.asarray(margins, dtype=float)


@dataclass
class GlobalPair:
    algorithm: str
    row: Detector
    window: Detector
    lookback_seconds: int
    reference_hashes: dict
    label: str = "synthetic_mechanical_pilot"

    def score_rows(self, frame):
        return detector_margin(self.row, frame)

    def score_window(self, frame):
        if not set(WINDOW_FEATURES).issubset(frame):
            raise ValueError("TIME_WINDOW feature schema mismatch")
        return detector_margin(self.window, frame)

    def metadata(self):
        def info(detector):
            return {"features": list(detector.encoder.columns),
                    "threshold": detector.threshold, "raw_threshold": detector.raw_threshold,
                    "normalizer_center": detector.normalizer.center,
                    "normalizer_spread": detector.normalizer.spread,
                    "effective_k": detector.effective_k}
        return {"algorithm": self.algorithm, "strategy": "global",
                "lookback_seconds": self.lookback_seconds, "label": self.label,
                "sklearn_version": sklearn.__version__, "reference_hashes": self.reference_hashes,
                "row": info(self.row), "window": info(self.window)}


def fit_pair(algorithm, row_partitions, window_partitions, lookback_seconds, config=None):
    if algorithm not in ("if", "kmeans"):
        raise ValueError("Unsupported algorithm")
    names = ("train", "calibration", "threshold")
    settings = ComparisonConfig(**(config or {}))
    if lookback_seconds not in (60, 300, 900):
        raise ValueError("Unsupported lookback")
    hashes = {}
    fitted = {}
    for unit, partitions in (("row", row_partitions), ("window", window_partitions)):
        if any(name not in partitions or partitions[name].empty for name in names):
            raise ValueError("Missing independent normal reference")
        sources = {name: set(partitions[name].source_template_id) for name in names}
        if any(sources[a] & sources[b] for a, b in (("train", "calibration"), ("train", "threshold"), ("calibration", "threshold"))):
            raise ValueError("Source template leaked across reference roles")
        with threadpool_limits(limits=1):
            fitted[unit] = Detector.fit(algorithm, *(partitions[n] for n in names), unit, settings)
        if hasattr(fitted[unit].model, "n_jobs"):
            fitted[unit].model.n_jobs = 1
        hashes[unit] = {name: frame_digest(partitions[name]) for name in names}
    return GlobalPair(algorithm, fitted["row"], fitted["window"], lookback_seconds, hashes)


def trusted_published_bundle(relative_path, *, trusted=False, expected_sklearn=None):
    if not trusted:
        raise ValueError("Explicit trust required for executable pickle")
    inventory = json.loads((RESEARCH / "model_inventory.json").read_text(encoding="utf-8"))
    entry = next((entry for entry in inventory["models"] if entry["path"] == relative_path), None)
    target = (RESEARCH / relative_path).resolve()
    if entry is None or not target.is_relative_to(RESEARCH.resolve()):
        raise ValueError("Artifact outside published inventory")
    if sha256_file(target) != entry["sha256"] or target.stat().st_size != entry["bytes"]:
        raise ValueError("Published artifact hash/size mismatch")
    expected = expected_sklearn or "1.9.1"
    if sklearn.__version__ != expected:
        raise ValueError(f"sklearn version mismatch: expected {expected}")
    with warnings.catch_warnings():
        warnings.simplefilter("error", InconsistentVersionWarning)
        bundle = joblib.load(target)
    if not isinstance(bundle, dict) or set(bundle) != {"row", "window", "baselines"}:
        raise ValueError("Expected row/window comparison bundle")
    for unit in ("row", "window"):
        if bundle[unit].unit != unit or set(bundle[unit].global_models) != {"if", "kmeans"}:
            raise ValueError("Published bundle unit/model mismatch")
        for detector in bundle[unit].global_models.values():
            if hasattr(detector.model, "n_jobs"):
                detector.model.n_jobs = 1
    return bundle, {"path": relative_path, "sha256": entry["sha256"],
                    "sklearn_version": sklearn.__version__, "scope": "published_reference_smoke_only"}


def inspect_published_bundle(*, trusted=False):
    """Synthetic shape/margin equivalence; no original dataset reproduction claim."""
    from process_anomaly_pipeline.data import prepare
    from model_evaluation.data import window_features
    bundle, metadata = trusted_published_bundle(
        "reports/model_evaluation_v2/comparison_models.joblib", trusted=trusted)
    row = prepare([{"transactionId": "SYN-1", "processId": "P-01", "processHubId": "H",
                    "adapterType": "SYNTHETIC", "startTime": "2026-01-01T12:00:00+09:00",
                    "endTime": "2026-01-01T12:00:00.123+09:00", "retryCount": 0,
                    "totalCount": 2, "successCount": 2, "errorCount": 0, "status": "S"}])
    native = pd.DataFrame([{"case_id": "SYN-1", "adapterType": "SYNTHETIC", "processId": "P-01",
                            "window_start": "2026-01-01T12:00:00+09:00",
                            "window_end": "2026-01-01T12:05:00+09:00", "execution_arrivals": 2,
                            "history_median_arrivals": 2, "history_p95_arrivals": 4,
                            "duration_p50_ms": 123, "duration_p95_ms": 123}])
    frames = {"row": row, "window": window_features(native, bundle["baselines"])}
    checks = []
    with threadpool_limits(limits=1):
        for unit, frame in frames.items():
            margins, predictions, _ = bundle[unit].score(frame)
            for algorithm, index in (("if", 0), ("kmeans", 3)):
                selected = detector_margin(bundle[unit].global_models[algorithm], frame)
                checks.append({"unit": unit, "algorithm": algorithm,
                               "equivalent": bool(np.allclose(selected, margins[:, index], rtol=1e-12, atol=1e-12)),
                               "prediction_equivalent": bool(np.array_equal(selected > 0, predictions[:, index])),
                               "feature_count": len(bundle[unit].global_models[algorithm].encoder.columns)})
    if not all(item["equivalent"] and item["prediction_equivalent"] for item in checks):
        raise ValueError("Selected global inference differs from published core")
    return {**metadata, "checks": checks, "original_data_reproduction": "blocked_missing_source_and_labels"}
