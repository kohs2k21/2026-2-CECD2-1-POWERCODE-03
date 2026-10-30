"""Portable research monitors. Every exported monitor contains ONE detector."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np

from adapter_feature_ablation.run import OUTPUT, ALGORITHMS, load_development
from model_evaluation.data import sha256
from model_research_v13.data import read, write
from model_research_v15.inference import ProcessResearchMonitor as FeatureProcessor


class SharedProcessMonitor:
    def __init__(self, detector, reference, context):
        self.detector, self.adapter_reference = detector, reference
        self.processor = FeatureProcessor(None, context, detector.algorithm)

    def features(self, native, completions):
        return self.adapter_reference.transform(self.processor.features(native, completions))

    def predict_features(self, frame, false_alarm_target=.03):
        score, prediction = self.detector.predict(frame, false_alarm_target)
        result = frame[["case_id", "adapterType", "processId", "window_start",
                        "adapter_reference_level", "adapter_reference_support"]].copy()
        result["score"] = score
        result["threshold"] = self.detector.thresholds[str(false_alarm_target)]
        result["anomaly"] = prediction
        return result

    def predict(self, native, completions, false_alarm_target=.03):
        return self.predict_features(self.features(native, completions), false_alarm_target)


def export():
    frozen = read(OUTPUT/"frozen.json")
    source = OUTPUT/"shared_models.joblib"
    if sha256(source) != frozen["model_sha256"]:
        raise ValueError("Frozen models changed")
    data = joblib.load(source)
    sample = data["adapter_reference"].transform(load_development()["partitions"]["threshold"].iloc[:150])
    folder = OUTPUT/"handoff"; folder.mkdir(exist_ok=True)
    records = []
    for algorithm in ALGORITHMS:
        model = data["models"][algorithm+"_adapter_features"]
        monitor = SharedProcessMonitor(model, data["adapter_reference"], data["feature_context"])
        path = folder/(algorithm+"_shared_adapter_features_experimental.joblib")
        joblib.dump(monitor, path, compress=3)
        restored = joblib.load(path)
        np.testing.assert_array_equal(monitor.predict_features(sample).anomaly,
                                      restored.predict_features(sample).anomaly)
        np.testing.assert_array_equal(monitor.predict_features(sample).score,
                                      restored.predict_features(sample).score)
        records.append({"algorithm": algorithm, "file": path.name, "bytes": path.stat().st_size,
                        "sha256": sha256(path), "detector_count": 1, "experts": False,
                        "thresholds": model.thresholds, "columns": list(model.columns),
                        "serialization_predictions_identical": True})
    write(folder/"manifest.json", {"models": records, "production_promotion": False,
        "requires_module": "adapter_feature_ablation.inference", "runtime_reads_benchmark": False,
        "transaction_table_required": False, "stateful_stream_worker_implemented": False,
        "input": "Process-derived contiguous minute grid and completed rows; prefix retained"})
    print("Three one-detector experimental artifacts exported; not production replacements", flush=True)


if __name__ == "__main__":
    from adapter_feature_ablation.inference import export
    export()
