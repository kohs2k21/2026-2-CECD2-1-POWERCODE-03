"""Reproduce global row-model computations on an explicitly identified dataset."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import time

import numpy as np

from .artifacts import (environment, sha256, write_json, save_bundle, load_bundle,
                        compare_roundtrip)
from .datasets import load_input, audit_dataset, split_and_audit
from .measurements import peak_rss_bytes
from model_evaluation.models import ComparisonConfig, Detector
from model_evaluation.metrics import metric_record
from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.rules import evaluate_rules, model_eligible, reference_eligible


def eligible(frame, *, training=False):
    """Named new-experiment policy; not a substitute for original reference archive."""
    config = PipelineConfig.from_dict(frame.attrs.get("trainer_lab_config"))
    flags, _ = evaluate_rules(frame, config)
    return (reference_eligible(frame, flags, exclude_statistical=False) if training
            else model_eligible(frame, flags)).fillna(False)


def evaluate(detector, frame):
    mask = eligible(frame)
    assessed = frame.loc[mask].copy()
    if assessed.empty:
        raise ValueError("No eligible held-out rows")
    scores = detector.score(assessed)
    if not np.isfinite(scores).all():
        raise ValueError("Non-finite test score")
    predicted = scores > detector.threshold
    report = {
        "totalRows": len(frame), "assessedRows": len(assessed),
        "unassessedRows": int((~mask).sum()), "predictedAnomalies": int(predicted.sum()),
        "predictedAnomalyRate": float(predicted.mean()),
        "scoreQuantiles": {str(q): float(np.quantile(scores, q)) for q in (0, .5, .95, .99, 1)},
        "accuracyMetrics": None, "labels": "not_provided",
    }
    # Status S is not ground truth. Only explicit labels on held-out input count.
    if "row_is_anomaly" in assessed:
        values = assessed.row_is_anomaly.astype("string").str.lower().map(
            {"true": True, "false": False, "1": True, "0": False})
        if values.isna().any():
            raise ValueError("Invalid explicit row labels")
        truth = values.to_numpy(dtype=bool)
        report["labels"] = "explicit_input_labels_not_independently_certified"
        report["accuracyMetrics"] = metric_record(
            np.sum(~truth & ~predicted), np.sum(~truth & predicted),
            np.sum(truth & ~predicted), np.sum(truth & predicted))
    return report, assessed


def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    initial = {
        "status": "running", "startedAt": datetime.now(timezone.utc).isoformat(),
        "environment": environment(), "platform": platform.platform(),
        "inputSha256": sha256(args.input), "inputKind": args.kind,
        "scope": "new_global_PROCESS_experiment",
        "originalBenchmarkReproduced": False,
        "originalComparison": {
            "status": "not_comparable",
            "reason": "Original reference archive, complete benchmark/labels and caches require separate provenance-matched reproduction.",
        },
        "memoryMethod": "OS process-lifetime peak RSS; cumulative across both models, not per-model incremental allocation",
        "limitations": [
            "Only global PROCESS models; window/Adapter/ensemble reference results are separate.",
            "Unlabelled observations report anomaly rate, never accuracy or false-positive rate.",
            "Existing completed-event quality/consistency rules select successful candidate references, not guaranteed normals.",
            "No online arrival time is invented; availability audit may remain unknown.",
        ],
    }
    write_json(output / "run.json", initial)
    started = time.perf_counter()
    try:
        frame = load_input(args.input, args.kind)
        quality = audit_dataset(frame)
        write_json(output / "quality.json", quality)
        partitions, split_report = split_and_audit(frame)
        write_json(output / "splits.json", split_report)
        if not args.train:
            initial.update(status="audited", elapsedSeconds=time.perf_counter()-started,
                           peakRssBytes=peak_rss_bytes())
            write_json(output / "run.json", initial)
            return initial
        if (split_report["transaction_separation"] == "failed"
                or split_report["lineage"]["status"] == "failed"
                or split_report["time_availability"]["status"] == "failed"):
            raise ValueError("Known split/lineage/time-availability violation; fitting refused")
        references = {name: part.loc[eligible(part, training=True)].copy()
                      for name, part in partitions.items() if name != "test"}
        config = ComparisonConfig()
        minimums = {"train": config.min_train, "calibration": config.min_calibration,
                    "threshold": config.min_threshold}
        if any(len(references[name]) < minimum for name, minimum in minimums.items()):
            raise ValueError("Too few independent eligible references in train/calibration/threshold")
        provenance = {
            "inputSha256": initial["inputSha256"], "inputKind": args.kind,
            "splitAudit": split_report, "sourceCommit": args.source_commit,
            "featurePreset": "published_PROCESS_10", "config": asdict(config),
            "referencePolicy": "published-reference-eligible-without-statistical-filter-v1",
            "fitRoles": {"encoder": "train", "estimator": "train", "normalizer": "calibration", "threshold": "threshold"},
            "referenceRows": {name: len(part) for name, part in references.items()},
        }
        results = {}
        for algorithm in (["if", "kmeans"] if args.algorithm == "both" else [args.algorithm]):
            stage = time.perf_counter()
            detector = Detector.fit(algorithm, references["train"], references["calibration"],
                                    references["threshold"], "row", config)
            train_seconds = time.perf_counter() - stage
            training_peak = peak_rss_bytes()
            stage = time.perf_counter()
            metrics, assessed = evaluate(detector, partitions["test"])
            eval_seconds = time.perf_counter() - stage
            destination = output / algorithm
            manifest = save_bundle(detector, destination, unit="row", provenance=provenance)
            loaded, _ = load_bundle(destination, trusted=True, expected_unit="row",
                                    expected_features=detector.encoder.columns,
                                    expected_provenance_hash=manifest["provenanceHash"])
            roundtrip = compare_roundtrip(detector, loaded, assessed)
            results[algorithm] = {
                "trainingSeconds": train_seconds, "evaluationSeconds": eval_seconds,
                "peakRssAfterTrainingBytes": training_peak,
                "evaluation": metrics, "roundtrip": roundtrip,
                "rawThreshold": detector.raw_threshold, "normalizedThreshold": detector.threshold,
            }
            write_json(output / f"{algorithm}-result.json", results[algorithm])
        initial.update(status="completed", models=results, config=asdict(config),
                       elapsedSeconds=time.perf_counter()-started, peakRssBytes=peak_rss_bytes())
        write_json(output / "run.json", initial)
        return initial
    except Exception as error:
        initial.update(status="failed", errorType=type(error).__name__,
                       elapsedSeconds=time.perf_counter()-started)
        # Exception text may contain raw values or paths; preserve only the class in artifacts.
        write_json(output / "run.json", initial)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--kind", choices=["csv", "archive"], default="csv")
    parser.add_argument("--output", required=True, help="New ignored runs/ directory, never an existing model directory")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--algorithm", choices=["if", "kmeans", "both"], default="both")
    parser.add_argument("--source-commit", required=True, help="Exact implementation commit/revision identifier")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({"status": result["status"], "originalBenchmarkReproduced": False}))


if __name__ == "__main__":
    main()
