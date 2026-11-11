"""Immutable logical-rule library composition and no-LLM serving."""

from copy import deepcopy
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import PACKAGE  # noqa: E402
sys.path.insert(0, str(PACKAGE))

from specmodel.io import digest, read, sha, write  # noqa: E402
from discovery_runtime import POLICY  # noqa: E402
from serve_rules import serve  # noqa: E402

LIBRARY_POLICY = "logical-rule-library1.1-pending-evidence"
COMPATIBILITY_KEYS = ("physical_schema_sha", "documents_sha", "policy",
                      "input_availability_policy", "predicate_consistency_policy")


def _validate_pack(pack):
    if not isinstance(pack, dict):
        raise ValueError("Every library item must be a rule pack object")
    for key in ("policy", "physical_schema_sha", "documents_sha", "definition_sha",
                "proposal", "execution"):
        if key not in pack:
            raise ValueError("Rule pack missing " + key)
    if pack["policy"] != POLICY:
        raise ValueError("Unsupported execution policy")
    execution = pack["execution"]
    if not isinstance(execution, dict) or execution.get("policy") != pack["policy"]:
        raise ValueError("Rule pack execution policy mismatch")
    if execution.get("executable_valid") is not True:
        raise ValueError("Invalid rule pack cannot enter a library")
    if not all(isinstance(pack[k], str) and pack[k] for k in
               ("physical_schema_sha", "documents_sha", "definition_sha")):
        raise ValueError("Rule pack is missing required SHA pins")
    for key in ("input_availability_policy", "predicate_consistency_policy"):
        value = pack.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ValueError("Invalid " + key)
    proposal = pack["proposal"]
    if not isinstance(proposal, dict) or not isinstance(proposal.get("candidates"), list):
        raise ValueError("Rule pack proposal must contain a candidates list")
    for candidate in proposal["candidates"]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
            raise ValueError("Malformed candidate in rule pack")
        if "plan" not in candidate or not isinstance(candidate.get("role"), str):
            raise ValueError("Candidate must preserve its plan and role")
        if not isinstance(candidate.get("requires_complete_capture"), bool):
            raise ValueError("Candidate must preserve requires_complete_capture")


def _library_hash(library):
    content = {k: v for k, v in library.items() if k != "library_sha256"}
    return digest(content)


def build_library(packs):
    """Bundle compatible valid frozen packs without collapsing proposals."""
    if not isinstance(packs, (list, tuple)):
        raise TypeError("packs must be a list or tuple of frozen rule packs")
    canonical = []
    for pack in packs:
        _validate_pack(pack)
        canonical.append(tuple(pack.get(key) for key in COMPATIBILITY_KEYS))
    if canonical and any(x != canonical[0] for x in canonical[1:]):
        raise ValueError("Library packs must share physical schema, documents, and execution policy")

    entries = []
    candidate_index = []
    calculation_groups = {}
    hold_count = 0
    for index, original in enumerate(packs):
        pack = deepcopy(original)
        pack_id = f"pack-{index:04d}-{digest(pack)[:12]}"
        entries.append({"pack_id": pack_id, "definition_sha": pack["definition_sha"],
                        "source_file_sha256": pack.get("source_file_sha256"), "pack": pack})
        for candidate in pack["proposal"]["candidates"]:
            plan = candidate["plan"]
            fingerprint = (digest({"plan": plan, "role": candidate["role"],
                                   "requires_complete_capture": candidate["requires_complete_capture"]})
                           if plan is not None else None)
            is_hold = plan is None
            hold_count += int(is_hold)
            candidate_index.append({"pack_id": pack_id, "candidate_id": candidate["id"],
                                    "role": candidate["role"], "is_hold": is_hold,
                                    "calculation_fingerprint": fingerprint})
            if fingerprint is not None:
                group = calculation_groups.setdefault(fingerprint, {"calculation_fingerprint": fingerprint,
                    "pack_candidates": []})
                group["pack_candidates"].append({"pack_id": pack_id, "candidate_id": candidate["id"]})

    if canonical:
        schema_sha, documents_sha, execution_policy, input_policy, predicate_policy = canonical[0]
    else:
        schema_sha = documents_sha = execution_policy = input_policy = predicate_policy = None
    library = {"library_policy": LIBRARY_POLICY, "execution_policy": execution_policy,
               "physical_schema_sha": schema_sha, "documents_sha": documents_sha,
               "input_availability_policy": input_policy,
               "predicate_consistency_policy": predicate_policy,
               "operational_approval": False,
               "approval_scope": "source_validity is not semantic or operational approval",
               "packs": entries, "candidate_index": candidate_index,
               "calculation_groups": list(calculation_groups.values()),
               "calculation_count_scope": "Exact plan AST + role + capture requirement signatures; not algebraically normalized semantic feature counts or approved rule counts.",
               "counts": {"pack_count": len(entries), "candidate_proposal_count": len(candidate_index),
                          "unique_calculation_count": len(calculation_groups),
                          "hold_proposal_count": hold_count}}
    library["library_sha256"] = _library_hash(library)
    return library


def _validate_library(library):
    if not isinstance(library, dict) or library.get("library_policy") != LIBRARY_POLICY:
        raise ValueError("Unsupported or malformed logical rule library")
    if library.get("library_sha256") != _library_hash(library):
        raise ValueError("Logical rule library integrity mismatch")
    entries = library.get("packs")
    if not isinstance(entries, list):
        raise ValueError("Logical rule library packs must be a list")
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("pack_id"), str):
            raise ValueError("Malformed library pack entry")
        if entry["pack_id"] in ids:
            raise ValueError("Duplicate library pack ID")
        ids.add(entry["pack_id"])
        pack = entry.get("pack")
        _validate_pack(pack)
        if (pack["physical_schema_sha"] != library.get("physical_schema_sha")
                or pack["documents_sha"] != library.get("documents_sha")
                or pack["policy"] != library.get("execution_policy")
                or pack.get("input_availability_policy") != library.get("input_availability_policy")
                or pack.get("predicate_consistency_policy") != library.get("predicate_consistency_policy")):
            raise ValueError("Library pack metadata mismatch")


def serve_library(library, raw, bulk=False):
    """Serve all compatible packs; uncertainty is preserved across the library."""
    _validate_library(library)
    outcomes = []
    any_warning = False
    any_unavailable = False
    entries = library["packs"]
    for entry in entries:
        pack = entry["pack"]
        try:
            result = serve(pack, raw, bulk=bulk)
        except Exception as exc:
            any_unavailable = True
            outcomes.append({"pack_id": entry["pack_id"], "decision": "unavailable",
                             "coverage_status": "execution_exception", "error": f"{type(exc).__name__}: {exc}",
                             "rule_count": 0, "result": None})
            continue
        rules = result.get("rules", [])
        decision = result.get("decision")
        warning = decision == "warning" or any(r.get("decision") == "warning" for r in rules)
        known_decisions = {"warning", "nonwarning", "unavailable"}
        invalid_decision = (decision not in known_decisions
                            or any(not isinstance(r, dict) or r.get("decision") not in known_decisions
                                   for r in rules))
        unavailable = (not rules or result.get("coverage_status") == "no_executable_rule"
                       or bool(result.get("evidence_requests"))
                       or decision == "unavailable"
                       or any(r.get("decision") == "unavailable" for r in rules)
                       or bool(result.get("type_mismatches")) or invalid_decision)
        any_warning |= warning
        any_unavailable |= unavailable
        outcomes.append({"pack_id": entry["pack_id"], "definition_sha": entry["definition_sha"],
                         "decision": decision, "coverage_status": result.get("coverage_status"),
                         "rule_count": len(rules),
                         "unavailable_rule_count": sum(r.get("decision") == "unavailable" for r in rules),
                         "pending_evidence_count": len(result.get("evidence_requests", [])),
                         "result": result})
    decision = "warning" if any_warning else "unavailable" if any_unavailable or not entries else "nonwarning"
    return {"library_policy": LIBRARY_POLICY, "library_sha256": library["library_sha256"],
            "decision": decision, "pack_count": len(entries), "pack_results": outcomes,
            "actual_LLM_calls": 0, "model_or_threshold_fit": False,
            "pending_evidence_count": sum(x.get("pending_evidence_count", 0) for x in outcomes),
            "operational_approval": False,
            "aggregation": "any warning; else unavailable if any unknown, exception, or no-rule pack; else nonwarning"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--packs", nargs="+", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    apply = commands.add_parser("serve")
    apply.add_argument("--library", required=True, type=Path)
    apply.add_argument("--input", required=True, type=Path)
    apply.add_argument("--output", required=True, type=Path)
    apply.add_argument("--bulk", action="store_true")
    args = parser.parse_args()
    if args.command == "build":
        packs = [read(path) for path in args.packs]
        library = build_library(packs)
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output / "library.json", library)
        write(args.output / "source_pins.json", {"pack_files": [
            {"path": str(p.resolve()), "sha256": sha(p)} for p in args.packs],
            "library_sha256": library["library_sha256"]})
        print(json.dumps({"library": str(args.output / "library.json"), **library["counts"]}))
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output / "input_pins.json", {"library_sha256": sha(args.library),
              "input_sha256": sha(args.input), "actual_LLM_calls": 0})
        try:
            result = serve_library(read(args.library), read(args.input), bulk=args.bulk)
        except Exception as exc:
            write(args.output / "result.json", {"library_policy": LIBRARY_POLICY,
                  "decision": "unavailable", "coverage_status": "rejected",
                  "reason": f"{type(exc).__name__}: {exc}", "actual_LLM_calls": 0,
                  "model_or_threshold_fit": False, "operational_approval": False})
            raise
        write(args.output / "result.json", result)
        print(json.dumps({"decision": result["decision"], "pack_count": result["pack_count"],
                          "actual_LLM_calls": 0}))


if __name__ == "__main__":
    main()
