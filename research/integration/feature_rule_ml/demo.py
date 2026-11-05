"""Small, artificial end-to-end CPU check; NOT customer accuracy evaluation.

Replays eight saved real LLM responses, then reuses their frozen definitions.
Makes 3 tiny new ML models from 3 disjoint artificial reference captures.
"""
import argparse
import copy
import csv
from pathlib import Path
import shutil

from .common import SPEC, new_dir, read, sha, write
from .spec_bridge import prepare, transform
from .ml_bridge import fit, inspect_published
from .runner import predict


def artificial_capture(folder, role, n=64):
    """Declared controlled fixture, with no operational data or incident labels."""
    folder = new_dir(folder)
    manifest = copy.deepcopy(read(SPEC / "examples/capture.json"))
    for name in ("CASE_HDR.csv", "controlled_authority.json"):
        shutil.copy2(SPEC / "examples" / name, folder / name)
    columns = ["ROW_ID", "REGION", "LINK_CODE", "A", "B", "QTY", "CLASS"]
    offsets = {"train": 0, "calibration": .11, "threshold": .23, "application": .37}
    with (folder / "CASE_LOG.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for i in range(n):
            a, b = 1 + i % 13 + offsets[role], 2
            if role == "application" and i >= n - 3:
                a = 2000 + i  # Numeric extremes, not confirmed operational faults.
            if role == "application" and i == 0:
                b = 0  # A real unavailable result, never filled with normal/zero.
            writer.writerow(dict(ROW_ID=f"{role}-{i}", REGION="x", LINK_CODE="one",
                                 A=a, B=b, QTY=i % 4, CLASS="controlled"))
    for table in manifest["tables"]:
        table["sha256"] = sha(folder / table["file"])
        if table["name"] == "CASE_LOG":
            table["row_count"] = n
    manifest.update(dataset_id="bridge_artificial_" + role, split=role,
                    source_identity={"fixture": "bridge_generated_artificial", "role": role})
    write(folder / "capture.json", manifest)
    return folder / "capture.json"


def run(output):
    root = new_dir(output)
    catalog = root / "catalog"
    prepare(SPEC / "examples/generated/public_delivery", SPEC / "examples/delivery/suite.json", catalog)
    predict(catalog, root / "public_rules_only")
    packs = {}
    for role in ("train", "calibration", "threshold", "application"):
        capture = artificial_capture(root / "captures" / role, role)
        packs[role] = root / "features" / role
        transform(catalog, capture, packs[role])
    g = read(packs["train"] / "prepared.json")["groups"][0]
    model_paths = []
    for algorithm in ("if", "kmeans", "lof"):
        path = root / "models" / algorithm
        fit(packs["train"], packs["calibration"], packs["threshold"],
            g["id"], ["ratio_value"], algorithm, path)
        model_paths.append(path)
    result = predict(packs["application"], root / "combined", model_paths, trusted=True)
    write(root / "published_v12_inspection.json", inspect_published())
    write(root / "demo_summary.json", {
        "kind": "artificial_functional_check_not_accuracy", "new_LLM_calls": 0,
        "actual_LLM_responses_replayed": 8, "new_small_artificial_ML_fits": 3,
        "customer_training": False, "published_v12_weights_modified": False,
        "train_calibration_threshold_rows_each": 64, "application_rows": 64,
        "expected_missing_numeric_rows": 1,
        "groups": [{"grain": x["grain"], "summary": x["summary"]} for x in result["groups"]],
        "unavailable_HR_is_not_a_healthy_HR_result": True,
    })
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(run(args.output))
