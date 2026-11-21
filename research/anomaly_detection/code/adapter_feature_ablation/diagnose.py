"""Normal-only shift diagnostics; no threshold selection or test retuning."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np

from adapter_feature_ablation.features import ADAPTER
from adapter_feature_ablation.run import OUTPUT, load_development
from model_research_v13.data import read, write
from model_evaluation.data import sha256


def diagnose():
    frozen = read(OUTPUT/"frozen.json")
    path = OUTPUT/"shared_models.joblib"
    assert sha256(path) == frozen["model_sha256"]
    fitted, context = joblib.load(path), load_development()
    reference = fitted["adapter_reference"]
    train = reference.transform(context["partitions"]["train"])
    calibration = reference.transform(context["normal_reference"])
    model = fitted["models"]["kmeans_adapter_features"]
    x, z = model.encoder.transform(train), model.encoder.transform(calibration)
    rows = []
    for column in ADAPTER:
        j = model.columns.index(column)
        rows.append({"feature": column, "encoder_scale": float(model.encoder.scale[j]),
            "train_abs_scaled_p95": float(np.quantile(np.abs(x[:, j]), .95)),
            "normal_calibration_abs_scaled_p95": float(np.quantile(np.abs(z[:, j]), .95)),
            "normal_calibration_abs_scaled_p99": float(np.quantile(np.abs(z[:, j]), .99)),
            "normal_calibration_fraction_abs_scaled_gt_20": float(np.mean(np.abs(z[:, j]) > 20))})
    rows.sort(key=lambda r:r["normal_calibration_abs_scaled_p99"], reverse=True)
    scores = []
    for method, detector in fitted["models"].items():
        a, b = detector.score(train), detector.score(calibration)
        scores.append({"method": method, "train_score_p95": float(np.quantile(a, .95)),
            "normal_calibration_score_p95": float(np.quantile(b, .95)),
            "normal_calibration_score_p99": float(np.quantile(b, .99)), "threshold": detector.thresholds["0.03"]})
    report = {"normal_only": True, "parameters_changed": False, "feature_scale_shift": rows,
        "score_shift": scores, "interpretation": "Large unseen normal feature magnitudes can raise distance-based cutoffs. Descriptive evidence, not causal feature attribution."}
    write(OUTPUT/"normal_shift_diagnostics.json", report)
    print(rows[:4], flush=True)


if __name__ == "__main__":
    diagnose()
