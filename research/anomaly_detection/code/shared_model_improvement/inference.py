"""Export one shared detector per algorithm without changing old artifacts."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"code"))
import joblib
import numpy as np
from model_research_v15.inference import ProcessResearchMonitor as FeatureProcessor
from model_research_v13.data import read, write
from model_evaluation.data import sha256
from shared_model_improvement.run import OUTPUT, ALGORITHMS, load_models, prepare


class SharedStableMonitor:
    def __init__(self, detector, reference, context):
        self.detector = detector
        self.adapter_reference = reference
        self.processor = FeatureProcessor(None, context, detector.algorithm)

    def features(self, native, completions):
        return self.adapter_reference.transform(self.processor.features(native, completions))

    def predict_features(self, frame):
        scores, prediction = self.detector.predict(frame)
        result = frame[["case_id", "adapterType", "processId", "window_start",
                        "adapter_reference_level", "adapter_reference_support"]].copy()
        result["score"], result["threshold"], result["anomaly"] = scores, self.detector.threshold, prediction
        return result

    def predict(self, native, completions):
        return self.predict_features(self.features(native, completions))


def export(folder=None):
    target = OUTPUT if folder is None else Path(folder).resolve()
    selected, fitted = load_models(target)
    sample = prepare()["confirmation"]["frame"].iloc[:250]
    folder = target/"handoff"; folder.mkdir(exist_ok=True)
    records = []
    for algorithm in ALGORITHMS:
        monitor = SharedStableMonitor(fitted["models"][algorithm], fitted["adapter_reference"], fitted["feature_context"])
        path = folder/(algorithm+"_shared_stable.joblib")
        joblib.dump(monitor, path, compress=3)
        restored = joblib.load(path)
        np.testing.assert_array_equal(monitor.predict_features(sample).score, restored.predict_features(sample).score)
        np.testing.assert_array_equal(monitor.predict_features(sample).anomaly, restored.predict_features(sample).anomaly)
        records.append({"algorithm": algorithm, "file": path.name, "bytes": path.stat().st_size,
                        "sha256": sha256(path), **monitor.detector.record(), "save_load_predictions_identical": True})
    write(folder/"manifest.json", {"models": records, "runtime_reads_benchmark": False,
        "single_detector_per_algorithm": True, "adapter_experts": False, "production_promoted": False,
        "requires_module": "shared_model_improvement.inference", "transaction_required": False,
        "requires_past_state_and_contiguous_minute_grid": True})
    print("Three single shared detectors exported and round-trip verified", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path)
    from shared_model_improvement.inference import export
    export(parser.parse_args().output)
