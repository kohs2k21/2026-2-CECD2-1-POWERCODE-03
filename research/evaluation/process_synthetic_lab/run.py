"""One-command offline generation/inference/scoring; no service/DB mutations."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import joblib
import numpy as np
import pandas as pd

from .config import ExperimentConfig
from .controls import noop_report
from .data import frame_hash, load_dataset, reference_mask, sha256
from .evaluate import control_report, paired_score_rows, score_rows
from .model import FrozenModel
from .scenarios import adapter_baselines, generate


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def write_json(path, value):
    Path(path).write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def digest_json(value):
    return hashlib.sha256(json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_hashes():
    package = Path(__file__).resolve().parent
    root = package.parents[2]
    sources = list(package.glob("*.py"))
    sources += list((root / "research/anomaly_detection/code/process_anomaly_pipeline").glob("*.py"))
    sources += [root / "research/evaluation/trainer_lab/datasets.py"]
    return {str(path.relative_to(root)).replace("\\", "/"): sha256(path) for path in sorted(sources)}


def run(process_path, transaction_path, output_dir, config, *, source_commit):
    output = Path(output_dir).resolve()
    runs_root = Path(__file__).resolve().parent / "runs"
    if not output.is_relative_to(runs_root.resolve()) or output == runs_root.resolve():
        raise ValueError("Output must be a new directory under this package's ignored runs/")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    before = {"process": sha256(process_path), "transaction": sha256(transaction_path)}
    manifest = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                "source_commit": source_commit, "source_hashes": source_hashes(),
                "input_hashes_before": before, "config": config.to_dict(),
                "python": platform.python_version(),
                "packages": {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn", "joblib")},
                "scope": "offline_research_only_no_customer_SLA_or_deployment_claim"}
    write_json(output / "manifest.json", manifest)
    try:
        frame, parts, audit = load_dataset(process_path, transaction_path, config)
        write_json(output / "data-audit.json", audit)
        for name, part in parts.items():
            part[["row_id", "startTime"]].to_csv(output / f"{name}-membership.csv", index=False)
        train, validation, test = (parts[name] for name in ("train", "threshold", "test"))
        if test.empty:
            raise ValueError("Empty test partition")
        test.to_csv(output / "test-base.csv", index=False)
        model = FrozenModel.fit(train, validation, config)
        joblib.dump(model, output / "frozen-model.joblib")
        write_json(output / "model-manifest.json", model.manifest)
        baselines = adapter_baselines(train)
        write_json(output / "generator-fit.json", {"fit_role": "train_only", "adapter_duration_medians_ms": baselines})
        controls = model.predict(test)
        controls.to_csv(output / "base-predictions.csv", index=False)
        write_json(output / "control-report.json", control_report(test, controls))
        control_digest = frame_hash(controls)
        noop = {"status": "disabled_by_config"}
        if config.run_noop_control:
            noop, noop_predictions = noop_report(output / "test-base.csv", controls, model, expected_frame=test)
            write_json(output / "noop-control.json", noop)
            noop_predictions.to_csv(output / "noop-predictions.csv", index=False)
            if noop["status"] != "passed":
                raise ValueError("C00 unchanged roundtrip changed predictions or scores")
        results, paired_results, trials, trial_digests = [], [], [], []
        for scenario in config.scenarios:
            levels = config.count_increments if scenario == "P04" else config.delay_multipliers
            for severity in range(len(levels)):
                for seed in config.seeds:
                    name = f"{scenario}-severity{severity + 1}-seed{seed}"
                    folder = output / name
                    folder.mkdir()
                    tick = time.perf_counter()
                    generated = generate(test, baselines, config, scenario=scenario, severity=severity, seed=seed)
                    predictions = model.predict(generated.frame) if not generated.truth.empty else controls.copy()
                    records = score_rows(generated.truth, predictions)
                    for record in records:
                        record.update(trial=name, scenario=scenario, severity=severity + 1, seed=seed)
                    results.extend(records)
                    paired = paired_score_rows(generated.truth, controls, predictions)
                    for record in paired:
                        record.update(trial=name, scenario=scenario, severity=severity + 1, seed=seed)
                    paired_results.extend(paired)
                    generated.truth.to_csv(folder / "truth.csv", index=False)
                    generated.changes.to_csv(folder / "changes.csv", index=False)
                    generated.parent_changes.to_csv(folder / "parent-overlay.csv", index=False)
                    generated.rejects.to_csv(folder / "rejects.csv", index=False)
                    predictions.to_csv(folder / "predictions.csv", index=False)
                    # Full evaluated frame retained only for a successful mutation.
                    if not generated.truth.empty:
                        generated.frame.to_csv(folder / "process.csv", index=False)
                    deterministic = {"trial": name, "dataset_hash": frame_hash(generated.frame),
                                     "truth_hash": frame_hash(generated.truth), "changes_hash": frame_hash(generated.changes),
                                     "parent_overlay_hash": frame_hash(generated.parent_changes),
                                     "prediction_hash": frame_hash(predictions), "metrics": records,
                                     "paired_metrics": paired,
                                     "generation": generated.metadata}
                    trial_digests.append(deterministic)
                    report = {**generated.metadata, "trial": name,
                              "parent_base_sha256": before["transaction"],
                              "parent_overlay_sha256": sha256(folder / "parent-overlay.csv"),
                              "parent_reconstruction": "canonicalize original TRANSACTION CSV; join overlay on source_period/transactionId/processHubId/retryCount; assert endTime=before then set endTime=after; preserve all other fields and rows",
                              "semantic_digest": digest_json(deterministic), "seconds": time.perf_counter() - tick}
                    trials.append(report)
                    write_json(folder / "generation.json", report)
        pd.DataFrame(results).to_csv(output / "results.csv", index=False)
        write_json(output / "results.json", results)
        pd.DataFrame(paired_results).to_csv(output / "paired-results.csv", index=False)
        write_json(output / "paired-results.json", paired_results)
        write_json(output / "trials.json", trials)
        adbench = {"status": "disabled_by_config"}
        if config.run_adbench:
            from .adbench_probe import run_probe
            # ADBench transforms numeric feature space, not original ESB rows.
            # Keep it outside the PROCESS score table and customer-normal claims.
            train_x = model.transform(train.loc[reference_mask(train)])
            test_x = model.transform(test.loc[reference_mask(test)])
            adbench = run_probe(train_x, test_x, seed=config.model_seed, output_dir=output / "adbench", predictor=model.predict_matrix)
        write_json(output / "adbench-summary.json", adbench)
        semantic = {"inputs": before, "config": config.to_dict(), "source_hashes": manifest["source_hashes"],
                    "partition_hashes": {key: frame_hash(value) for key, value in parts.items()},
                    "model_manifest": model.manifest, "control_prediction_hash": control_digest,
                    "noop_control": noop,
                    "trials": trial_digests}
        write_json(output / "reproducibility.json", {"semantic_digest": digest_json(semantic),
                   "excludes": ["wall_clock_timing", "absolute_paths", "ADBench_network_probe"], "evidence": semantic})
        manifest.update(status="completed", elapsed_seconds=time.perf_counter() - started,
                        trial_count=len(trials), successful_trials=sum(row.get("accepted_rows", 0) > 0 for row in trials),
                        semantic_digest=digest_json(semantic), model_status=model.manifest["model_status"],
                        final_teammate_model_validated=False, verified_normal_FPR_available=False,
                        production_HR_SR_composite_validated=False)
        manifest["noop_control_status"] = noop["status"]
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc), elapsed_seconds=time.perf_counter() - started)
        raise
    finally:
        after = {"process": sha256(process_path), "transaction": sha256(transaction_path)}
        source_after = source_hashes()
        source_unchanged = manifest["source_hashes"] == source_after
        manifest.update(input_hashes_after=after, source_inputs_unchanged=before == after,
                        source_hashes_after=source_after, source_code_unchanged=source_unchanged)
        if before != after or not source_unchanged:
            manifest["status"] = "invalidated_inputs_or_source_changed"
        write_json(output / "manifest.json", manifest)
        if before != after:
            raise RuntimeError("Input file changed during experiment")
        if not source_unchanged:
            raise RuntimeError("Source code changed during experiment; results require a frozen-source rerun")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", required=True)
    parser.add_argument("--transactions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    config = ExperimentConfig.from_dict(json.loads(Path(args.config).read_text(encoding="utf-8-sig"))) if args.config else ExperimentConfig()
    result = run(args.process, args.transactions, args.output, config, source_commit=args.source_commit)
    print(json.dumps({key: result[key] for key in ("status", "trial_count", "successful_trials", "semantic_digest")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
