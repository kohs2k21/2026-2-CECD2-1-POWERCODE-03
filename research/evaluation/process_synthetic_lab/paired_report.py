"""Add a paired diagnostic from immutable saved predictions without retraining."""
import argparse
from pathlib import Path
import re
import json

import pandas as pd

from .data import sha256
from .evaluate import paired_score_rows
from .run import digest_json, write_json


def build_report(run_dir, output_dir):
    source = Path(run_dir).resolve()
    output = Path(output_dir).resolve()
    runs_root = Path(__file__).resolve().parent / "runs"
    if not output.is_relative_to(runs_root.resolve()) or output == runs_root.resolve() or output.is_relative_to(source):
        raise ValueError("Use a separate new directory under this package's ignored runs/")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise ValueError("Paired postprocessing requires a completed source run")
    baseline_path = source / "base-predictions.csv"
    trials_path = source / "trials.json"
    trial_info = json.loads(trials_path.read_text(encoding="utf-8"))
    inputs = [source / "manifest.json", baseline_path, trials_path]
    for trial in trial_info:
        if not re.fullmatch(r"P\d{2}-severity\d+-seed\d+", trial["trial"]):
            raise ValueError("Invalid trial path")
        inputs.extend([source / trial["trial"] / "truth.csv", source / trial["trial"] / "predictions.csv"])
    hashes = {str(path.relative_to(source)).replace("\\", "/"): sha256(path) for path in inputs}
    baseline = pd.read_csv(baseline_path, dtype={"row_id": "string"})
    rows = []
    for trial in trial_info:
        folder = source / trial["trial"]
        truth = pd.read_csv(folder / "truth.csv", dtype={"row_id": "string"})
        post = pd.read_csv(folder / "predictions.csv", dtype={"row_id": "string"})
        records = paired_score_rows(truth, baseline, post)
        for record in records:
            record.update(trial=trial["trial"], scenario=trial["scenario"], severity=trial["severity"], seed=trial["seed"])
        rows.extend(records)
    after = {str(path.relative_to(source)).replace("\\", "/"): sha256(path) for path in inputs}
    if hashes != after:
        raise ValueError("Source run changed during postprocessing")
    output.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(rows).to_csv(output / "paired-results.csv", index=False)
    write_json(output / "paired-results.json", rows)
    provenance = {"method": "saved_same_PROCESS_pre_post_predictions_no_retraining_or_generation",
                  "source_run_semantic_digest": manifest.get("semantic_digest"),
                  "source_model_status": manifest.get("model_status"),
                  "source_files": hashes, "source_run_unchanged": True,
                  "report_source_hashes": {name: sha256(Path(__file__).with_name(name)) for name in ("paired_report.py", "evaluate.py")},
                  "paired_results_digest": digest_json(rows),
                  "meaning": "new_vs_existing_alerts_on_label1_injected_rows_not_real_customer_causal_proof"}
    write_json(output / "provenance.json", provenance)
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_report(args.run, args.output)
    print(json.dumps({"paired_results_digest": result["paired_results_digest"]}))


if __name__ == "__main__":
    main()
