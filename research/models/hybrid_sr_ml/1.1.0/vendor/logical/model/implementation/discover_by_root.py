"""Decompose discovery by source population without providing rule conditions.

Every physical table is considered by default. Models still choose operators,
joins, conditions, exceptions and roles. No output is silently substituted or
discarded; no-candidate/evidence-request packs remain in the frozen library.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

from run import PACKAGE
from specmodel.io import read, write, sha, digest
from run_discovery import normalize
from logical_rule_library import build_library

POLICY = "source-root-population-discovery1.0"


def requests(source, roots=None, maximum_roots=8):
    case = normalize(source)
    available = sorted(case["schema"])
    selected = list(dict.fromkeys(roots)) if roots else available
    if not selected or any(root not in available for root in selected):
        raise ValueError("Every requested root must be a table in the admitted physical schema")
    if len(selected) > maximum_roots:
        raise ValueError("Too many roots for this bounded call; explicitly batch --root choices. No tables are silently skipped.")
    result = []
    for index, root in enumerate(selected):
        snapshot = deepcopy(case)
        snapshot["question"] += (" For this call, review source rows of " + root +
            " as the root population. Other tables remain available as context. "
            "Preserve the original requested analysis scope; if this root is outside that scope, return no candidate rather than broadening the task. "
            "Choose any justified checks yourself, or return a specific evidence request/no-candidate reason. "
            "Do not invent a requirement.")
        result.append({"request_id": "root_" + str(index).zfill(4), "root": root, "source": snapshot})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--arm", choices=["Base", "Candidate"], required=True)
    p.add_argument("--candidate", type=Path)
    p.add_argument("--root", action="append")
    p.add_argument("--maximum-roots", type=int, choices=range(1, 33), default=8)
    a = p.parse_args()
    if a.arm == "Candidate" and a.candidate is None:
        raise ValueError("Explicit selected adapter required")
    items = requests(read(a.input), a.root, a.maximum_roots)
    a.output.mkdir(parents=True, exist_ok=False)
    files = []
    for item in items:
        path = a.output / "requests" / (item["request_id"] + ".json")
        write(path, item["source"])
        files.append({"request_id": item["request_id"], "root": item["root"],
                      "input_sha256": sha(path), "path": str(path.resolve())})
    write(a.output / "request_manifest.json", {"policy": POLICY,
        "original_input_sha256": sha(a.input), "root_requests": files,
        "condition_operator_or_business_requirement_supplied_by_dispatcher": False,
        "source_schema_documents_records_capture_changed": False,
        "source_question_scope_refinement_only": True,
        "explicitly_unrequested_roots": sorted(set(read(a.input)["schema"]) - {x["root"] for x in items}),
        "actual_LLM_call_limit": 3 * len(items), "one_shared_model_load": True})
    command = [sys.executable, "-B", str(Path(__file__).with_name("run_logical_completion.py")),
        "--input-dir", str(a.output / "requests"), "--output", str(a.output / "generation"),
        "--arm", a.arm, "--source-review", "--execution-feedback", "--strict-input-guards"]
    if a.candidate is not None:
        command += ["--candidate", str(a.candidate)]
    with (a.output / "generation.log").open("x") as log:
        generated = subprocess.run(command, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)
    issues, packs = [], []
    if generated.returncode:
        issues.append({"error": "generation_process_failed", "exit_code": generated.returncode})
    for item in items:
        path = a.output / "generation" / item["request_id"] / "candidate_pack.json"
        if not path.is_file():
            issues.append({"request": item["request_id"], "error": "missing_model_pack"})
            continue
        pack = read(path)
        if pack["execution"].get("executable_valid") is not True:
            issues.append({"request": item["request_id"], "error": "source_invalid_model_pack"})
        for candidate in pack["proposal"]["candidates"]:
            if candidate["plan"] is not None and candidate["plan"]["select"]["table"] != item["root"]:
                issues.append({"request": item["request_id"], "candidate": candidate["id"],
                               "error": "model_did_not_preserve_requested_root_population"})
        packs.append(pack)
    if issues:
        write(a.output / "result.json", {"policy": POLICY, "state": "incomplete_or_invalid",
            "issues": issues, "library_created": False, "all_model_attempts_preserved": True,
            "decision_if_execution_were_requested": "unavailable"})
        raise SystemExit(2)
    library = build_library(packs)
    write(a.output / "library.json", library)
    write(a.output / "result.json", {"policy": POLICY, "state": "source_valid_proposals_pending_review",
        "roots_considered": [item["root"] for item in items], "library_created": True,
        "library_sha256": library["library_sha256"], "counts": library["counts"],
        "empty_and_held_proposals_preserved": True, "operational_approval": False,
        "semantic_correctness_not_proven_by_source_validity": True,
        "no_original_model_data_or_threshold_modification": True})
    print(json.dumps({"state": "source_valid_proposals_pending_review", "counts": library["counts"]}))


if __name__ == "__main__":
    main()
