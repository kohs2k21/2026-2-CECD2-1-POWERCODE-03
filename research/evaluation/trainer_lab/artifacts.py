"""Offline, trusted-only fit/model bundles; strict pairing before pickle loading."""

from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import tempfile
import uuid

import joblib
import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.ensemble import IsolationForest

from model_evaluation.models import Detector, WindowEncoder, WINDOW_FEATURES
from process_anomaly_pipeline.features import FeatureEncoder, ScoreNormalizer

FORMAT = "trainer-lab-bundle-v1"


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding="utf-8")


def fingerprint(value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def environment():
    return {"python": platform.python_version(), **{
        name: version(name) for name in
        ("numpy", "pandas", "scipy", "scikit-learn", "joblib", "threadpoolctl")}}


def save_bundle(detector, output, *, unit, provenance):
    """Publish a new directory only; never overwrite a previous experiment."""
    if unit not in ("row", "window") or detector.algorithm not in ("if", "kmeans"):
        raise ValueError("Unsupported model/unit")
    expected_encoder = FeatureEncoder if unit == "row" else WindowEncoder
    if not isinstance(detector.encoder, expected_encoder):
        raise ValueError("Encoder/unit mismatch")
    output = Path(output)
    if output.exists():
        raise FileExistsError("Bundle output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=output.parent))
    bundle_id = str(uuid.uuid4())
    metadata = {"bundleId": bundle_id, "algorithm": detector.algorithm, "unit": unit}
    try:
        joblib.dump({**metadata, "model": detector.model}, stage / "model.joblib")
        joblib.dump({**metadata, "encoder": detector.encoder,
                     "normalizer": detector.normalizer,
                     "threshold": detector.threshold,
                     "rawThreshold": detector.raw_threshold,
                     "effectiveK": detector.effective_k}, stage / "fit.joblib")
        manifest = {
            "format": FORMAT, **metadata, "environment": environment(),
            "featureColumns": list(detector.encoder.columns),
            "score": "negative_score_samples" if detector.algorithm == "if" else "nearest_center_distance",
            "decision": "normalized_score > threshold", "threshold": float(detector.threshold),
            "rawThreshold": float(detector.raw_threshold),
            "normalizer": asdict(detector.normalizer),
            "provenance": provenance, "provenanceHash": fingerprint(provenance),
            "files": {name: sha256(stage / name) for name in ("model.joblib", "fit.joblib")},
        }
        write_json(stage / "manifest.json", manifest)
        stage.rename(output)
        return manifest
    except BaseException:
        shutil.rmtree(stage)
        raise


def load_bundle(path, *, trusted=False, expected_unit=None, expected_features=None,
                expected_provenance_hash=None):
    """Hashes provide integrity, not trust. Never accept arbitrary uploaded pickle."""
    if not trusted:
        raise ValueError("Executable pickle requires explicit trusted=True")
    path = Path(path).resolve()
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("environment") != environment():
        raise ValueError("Bundle format/environment mismatch")
    if manifest.get("unit") not in ("row", "window") or manifest.get("algorithm") not in ("if", "kmeans"):
        raise ValueError("Invalid model/unit")
    if expected_unit is not None and manifest["unit"] != expected_unit:
        raise ValueError("Requested unit mismatch")
    if expected_features is not None and manifest["featureColumns"] != list(expected_features):
        raise ValueError("Requested feature order mismatch")
    if manifest.get("provenanceHash") != fingerprint(manifest.get("provenance")):
        raise ValueError("Provenance checksum mismatch")
    if expected_provenance_hash is not None and manifest["provenanceHash"] != expected_provenance_hash:
        raise ValueError("Requested provenance mismatch")
    if set(manifest.get("files", {})) != {"model.joblib", "fit.joblib"}:
        raise ValueError("Invalid artifact file list")
    for name, expected in manifest["files"].items():
        target = (path / name).resolve()
        if target.parent != path or sha256(target) != expected:
            raise ValueError("Artifact target/hash mismatch")
    model = joblib.load(path / "model.joblib")
    fit = joblib.load(path / "fit.joblib")
    for item in (model, fit):
        if any(item.get(key) != manifest[key] for key in ("bundleId", "algorithm", "unit")):
            raise ValueError("Fit/model bundle pairing mismatch")
    estimator_type = IsolationForest if manifest["algorithm"] == "if" else MiniBatchKMeans
    encoder_type = FeatureEncoder if manifest["unit"] == "row" else WindowEncoder
    if not isinstance(model.get("model"), estimator_type) or not isinstance(fit.get("encoder"), encoder_type):
        raise ValueError("Unexpected model or encoder class")
    if not isinstance(fit.get("normalizer"), ScoreNormalizer):
        raise ValueError("Unexpected score normalizer")
    if list(fit["encoder"].columns) != manifest["featureColumns"]:
        raise ValueError("Encoder feature order mismatch")
    if model["model"].n_features_in_ != len(manifest["featureColumns"]):
        raise ValueError("Estimator feature count mismatch")
    if fit["threshold"] != manifest["threshold"] or fit["rawThreshold"] != manifest["rawThreshold"]:
        raise ValueError("Threshold mismatch")
    if asdict(fit["normalizer"]) != manifest["normalizer"]:
        raise ValueError("Score normalization mismatch")
    if not np.isfinite([fit["threshold"], fit["rawThreshold"],
                        fit["normalizer"].center, fit["normalizer"].spread]).all() or fit["normalizer"].spread <= 0:
        raise ValueError("Invalid calibration values")
    return Detector(manifest["algorithm"], model["model"], fit["encoder"], fit["normalizer"],
                    fit["threshold"], fit["rawThreshold"], fit["effectiveK"]), manifest


def compare_roundtrip(original, restored, frame):
    before, after = original.encoder.transform(frame), restored.encoder.transform(frame)
    first, second = original.score(frame), restored.score(frame)
    feature_equal = bool(np.allclose(before, after, rtol=1e-7, atol=1e-9, equal_nan=False))
    score_equal = bool(np.allclose(first, second, rtol=1e-7, atol=1e-9, equal_nan=False))
    labels_equal = bool(np.array_equal(first > original.threshold, second > restored.threshold))
    if not (feature_equal and score_equal and labels_equal):
        raise ValueError("Save/reload predictions changed")
    return {"rows": len(frame), "featuresEqual": feature_equal, "scoresEqual": score_equal,
            "decisionsEqual": labels_equal, "rtol": 1e-7, "atol": 1e-9}
