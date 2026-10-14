"""Fail-closed source admission, separate from content/Body classification.

Explicit registry entries bind bytes and recursively bind ancestry. No path name,
model claim, timestamp alone, or downstream derived-file label grants admission.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "source-admission-1.0.0"
STAGES = frozenset({"profile", "model_input", "measurement", "teacher", "baseline",
                   "baseline_registry_build", "baseline_rule_resolve", "selection_source_scan"})
BASELINE_RECORD = "data/eda/process_rule_preparation_v1/20261010T090751_511608Z"
P21 = ROOT / "data/eda/schema_generalization_v21/20261011_source_coverage_baseline"


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def relationship_definition(relation):
    """Physical join identity, independent of measured status or CSV contents.

    Column ordering, direction and lexical no-reference sentinels are part of
    the definition. A capture of one join cannot authorize a different join.
    """
    if not isinstance(relation, dict):
        raise ValueError("Relationship definition must be an object")
    result = {field: copy.deepcopy(relation.get(field)) for field in
              ("id", "from_table", "from_columns", "to_table", "to_columns")}
    result["no_reference_values"] = copy.deepcopy(relation.get("no_reference_values", []))
    for field in ("id", "from_table", "to_table"):
        if not isinstance(result[field], str) or not result[field]:
            raise ValueError("Relationship definition needs a nonempty " + field)
    for field in ("from_columns", "to_columns"):
        names = result[field]
        if (not isinstance(names, list) or not names or
                any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names)):
            raise ValueError("Relationship definition needs ordered unique physical " + field)
    if len(result["from_columns"]) != len(result["to_columns"]):
        raise ValueError("Relationship definition column arities differ")
    if (not isinstance(result["no_reference_values"], list) or
            any(not isinstance(value, str) for value in result["no_reference_values"])):
        raise ValueError("Relationship no-reference values must be lexical strings")
    return result


def relationship_definition_sha256(relation):
    return canonical_sha(relationship_definition(relation))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _local(value, root):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Source admission requires a project-local physical path")
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    path.relative_to(root)
    return path


def _rel(path, root):
    return str(Path(path).resolve().relative_to(root))


class SourceAdmissionError(ValueError):
    def __init__(self, errors, stage):
        self.errors = errors
        self.stage = stage
        super().__init__("Source admission rejected: " + "; ".join(
            f"{x['path']}: {x['code']}" for x in errors))


def empty_policy(*, held_families=(), held_content_sha256=()):
    return {"version": VERSION, "default_unknown": "hold", "nodes": [],
            "held_families": list(held_families),
            "held_content_sha256": sorted(set(held_content_sha256)),
            "policy_authority": "explicit_code_registration_not_model_output"}


def register_source(policy, path, *, project_root=ROOT, kind, status="approved",
                    parents=(), period=None, reason, source_family=None, expected_sha256=None,
                    row_admission_required=False, row_scope_proof=None):
    """Return a new policy; callers must explicitly choose source authority.

    Original real files need an approval reason and period. Derived records need
    parents; synthetic/schema-only roots are separately marked, never real data.
    Held SHA/family precedence cannot be overridden by registration.
    """
    root = Path(project_root).resolve()
    local = _local(str(path), root)
    if kind not in {"original_real", "derived", "synthetic", "schema_only", "document"}:
        raise ValueError("Unknown registered source kind")
    if status not in {"approved", "held"} or not isinstance(reason, str) or not reason:
        raise ValueError("Registration needs explicit status and authority reason")
    if kind == "original_real" and not isinstance(period, dict):
        raise ValueError("Real source approval needs explicit period metadata")
    if kind == "derived" and not parents:
        raise ValueError("Derived source registration requires complete parent lineage")
    sha = digest(local)
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError("Registration source bytes differ from the approved SHA")
    result = copy.deepcopy(policy)
    if result.get("version") != VERSION or result.get("default_unknown") != "hold":
        raise ValueError("Policy version/default must be current and fail closed")
    registered_ids = {x["id"] for x in result["nodes"]}
    if any(not isinstance(x, str) or x not in registered_ids for x in parents):
        raise ValueError("Every lineage parent must already be explicitly registered")
    parent_nodes = [x for x in result["nodes"] if x["id"] in parents]
    inherited_row_requirement = kind == "derived" and any(x.get("row_admission_required") for x in parent_nodes)
    inherited_row_requirement |= any(x["sha256"] == sha and x.get("row_admission_required")
                                    for x in result["nodes"])
    node = {"path": _rel(local, root), "sha256": sha, "kind": kind, "status": status,
            "parents": list(parents), "period": copy.deepcopy(period),
            "reason": reason, "source_family": source_family,
            "row_admission_required": bool(row_admission_required or (inherited_row_requirement and row_scope_proof is None)),
            "row_scope_proof": copy.deepcopy(row_scope_proof)}
    if row_scope_proof is not None:
        _validate_row_scope_proof(node, parent_nodes, root)
    node["id"] = canonical_sha(node)
    existing = [x for x in result["nodes"] if x["path"] == node["path"]]
    if existing:
        if existing == [node]:
            return result
        raise ValueError("A registered path cannot silently change bytes, identity or lineage")
    result["nodes"].append(node)
    return result


def _validate_row_scope_proof(node, parent_nodes, root):
    pin = node["row_scope_proof"]
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("Row admission requires an exact physical proof pin")
    path = _local(pin["path"], root)
    if digest(path) != pin["sha256"]:
        raise ValueError("Row admission proof bytes changed")
    proof = json.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {"in_period", "verified_by_approved_transaction_key_and_source"}
    if (proof.get("version") != "approved-row-scope-1.0.0" or
            proof.get("output") != {"path": node["path"], "sha256": node["sha256"]} or
            proof.get("body_values_copied") is not False or
            proof.get("verified_by") != "code" or
            proof.get("period") != node.get("period") or
            not isinstance(proof.get("rows"), list) or
            proof.get("row_count") != len(proof["rows"])):
        raise ValueError("Row proof must bind exact output, period, code verification, and all selected rows")
    parents = {(x["path"], x["sha256"]) for x in parent_nodes}
    for field in ("original_source", "transaction_source"):
        source = proof.get(field, {})
        if (source.get("path"), source.get("sha256")) not in parents:
            raise ValueError("Row proof source/approved transaction must be registered direct parents")
    original = proof["original_source"]
    ordinals = []
    for row in proof["rows"]:
        if (row.get("period_admission") not in allowed or
                row.get("source_path") != original["path"] or
                row.get("source_sha256") != original["sha256"] or
                type(row.get("source_row_ordinal")) is not int or row["source_row_ordinal"] < 1 or
                not isinstance(row.get("transaction_key_sha256"), str) or len(row["transaction_key_sha256"]) != 64):
            raise ValueError("Row proof contains an unverified period/key/source row")
        ordinals.append(row["source_row_ordinal"])
    if len(ordinals) != len(set(ordinals)):
        raise ValueError("Row proof cannot silently duplicate original source rows")


def build_project_source_policy(*, project_root=ROOT, output=None):
    """Reify the preserved approval record; held CSVs are hashed, never profiled."""
    root = Path(project_root).resolve()
    previous = root / BASELINE_RECORD
    old_policy = json.loads((previous / "source_use_policy.json").read_text())
    manifest = json.loads((previous / "source_manifest.json").read_text())
    quality = previous / "builds/attempt_03/source_quality.json"
    process_extent = json.loads(quality.read_text())["source_time_extent"]
    held_paths = sorted(path for path in (root / "data_1002").rglob("*")
                        if path.is_file() and path.suffix.lower() == ".csv")
    held = [{"path": _rel(path, root), "sha256": digest(path), "bytes": path.stat().st_size}
            for path in held_paths]
    policy = empty_policy(held_families=old_policy["held_source_families"],
                          held_content_sha256=[x["sha256"] for x in held])
    policy["authority_sources"] = [{"path": _rel(path, root), "sha256": digest(path)}
                                    for path in (previous / "source_use_policy.json",
                                                 previous / "source_manifest.json", quality)]
    policy["held_source_records"] = held
    policy["held_period"] = old_policy["held_date_description"]
    period = {"start_inclusive": "2026-08-10T00:00:00",
              "end_exclusive": "2026-09-09T00:00:00", "timezone": "unknown",
              "scope": "approved_aug_sep_source_family_wall_clock_window",
              "basis": "preserved original_aug_sep_baseline manifest and process source-time extent",
              "process_observed_extent": process_extent,
              "is_completion_or_operational_time_contract": False}
    approved = {x["path"]: x for x in manifest["sources"]}
    for path in old_policy["allowed_exact_paths"]:
        item = approved[path]
        policy = register_source(policy, path, project_root=root, kind="original_real",
                                 period=period, expected_sha256=item["sha256"],
                                 source_family=manifest["source_family"],
                                 row_admission_required=path == "data/EC_MESSAGE_MONITORING.csv",
                                 reason="preserved explicit baseline exact-path and SHA approval")
    policy["construction_scope"] = {"held_files_content_hash_only": True,
                                     "held_statistics_generated": False,
                                     "allowed_original_count": 3,
                                     "held_csv_count": len(held)}
    if output is not None:
        target = _local(str(output), root)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(policy, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    return policy


def _pins(value):
    if isinstance(value, str):
        return [{"path": value}]
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_pins(item))
        return out
    if not isinstance(value, dict):
        raise ValueError("Source input requires paths, pins, or a source-bearing object")
    if "path" in value and isinstance(value["path"], str):
        return [copy.deepcopy(value)]
    result = []
    for field in ("sources", "source_dependencies", "original_sources", "evidence", "baseline_requests"):
        if field in value:
            result.extend(_pins(value[field]))
    for field in ("original_source_sha256", "source_sha256"):
        if isinstance(value.get(field), dict):
            result.extend({"path": path, "sha256": sha} for path, sha in value[field].items())
    for field in ("schema_source", "source", "csv_source", "source_provenance", "provenance"):
        if value.get(field) is not None:
            result.extend(_pins(value[field]))
    for field in ("source_pins", "sample_sources"):
        if isinstance(value.get(field), dict):
            for path, pin in value[field].items():
                result.extend(_pins(pin if isinstance(pin, dict) else {"path": path, "sha256": pin}))
    if isinstance(value.get("tables"), list):
        for table in value["tables"]:
            if table.get("csv_source"):
                result.extend(_pins(table["csv_source"]))
    return result


def assert_source_paths(paths_or_pins, policy, stage, *, project_root=ROOT):
    if stage not in STAGES:
        raise ValueError("Unknown source admission checkpoint")
    if policy.get("version") != VERSION or policy.get("default_unknown") != "hold":
        raise ValueError("Current fail-closed source policy is required")
    root = Path(project_root).resolve()
    records = policy.get("nodes", [])
    by_id = {x["id"]: x for x in records}
    by_path = {x["path"]: x for x in records}
    if len(by_id) != len(records) or len(by_path) != len(records):
        raise ValueError("Source registry IDs and physical paths must be unique")
    held = set(policy.get("held_content_sha256", []))
    held_families = policy.get("held_families", [])
    errors, checked, visiting, verified = [], {}, set(), set()

    def fail(code, path, message):
        errors.append({"code": code, "path": path, "message": message})

    def check(node):
        identity = node["id"]
        if identity in verified:
            return
        if identity in visiting:
            fail("lineage_cycle", node["path"], "Source ancestry contains a cycle")
            return
        visiting.add(identity)
        try:
            path = _local(node["path"], root)
            sha = digest(path)
        except (ValueError, OSError):
            fail("unreadable_source", node["path"], "Registered source is missing or outside the project")
            visiting.remove(identity)
            return
        expected = {k: v for k, v in node.items() if k != "id"}
        if canonical_sha(expected) != identity:
            fail("registry_identity", node["path"], "Source registration identity changed")
        if sha != node["sha256"]:
            fail("source_sha_changed", node["path"], "Source bytes no longer match approved registration")
        if sha in held or any(node["path"] == fam or node["path"].startswith(fam.rstrip("/") + "/")
                              for fam in held_families) or node.get("source_family") in held_families:
            fail("held_source", node["path"], "Held family/content SHA applies even after rename or registration")
        if node.get("status") != "approved":
            fail("source_status_held", node["path"], "Source is explicitly held")
        parents = node.get("parents")
        if not isinstance(parents, list) or (node.get("kind") == "derived" and not parents):
            fail("lineage_missing", node["path"], "Derived source needs explicit complete parent ancestry")
        else:
            for parent in parents:
                if parent not in by_id:
                    fail("lineage_unknown", node["path"], "A lineage parent is unregistered")
                else:
                    check(by_id[parent])
        if node.get("row_scope_proof") is not None:
            try:
                _validate_row_scope_proof(node, [by_id[x] for x in parents if x in by_id], root)
            except (ValueError, OSError, KeyError, TypeError):
                fail("row_scope_proof_invalid", node["path"], "Row-period/source/output proof is invalid or stale")
        checked[identity] = copy.deepcopy(node)
        visiting.remove(identity)
        verified.add(identity)

    pins = _pins(paths_or_pins)
    if not pins:
        fail("source_pins_missing", "$", "Source-bearing input must include explicit physical pins")
    for index, pin in enumerate(pins):
        try:
            path = _rel(_local(pin["path"], root), root)
        except (KeyError, ValueError):
            fail("source_path", f"sources[{index}]", "Source path must be project local")
            continue
        # Hash an unregistered file only for content-SHA blocking, never statistics.
        node = by_path.get(path)
        if node is None:
            try:
                sha = digest(root / path)
            except OSError:
                sha = None
            is_held = sha in held or any(path == fam or path.startswith(fam.rstrip("/") + "/")
                                       for fam in held_families)
            fail("held_source" if is_held else "unregistered_source", path,
                 "Held original/renamed content" if is_held else "Unknown ancestry defaults to hold")
            continue
        if pin.get("sha256") is not None and pin["sha256"] != node["sha256"]:
            fail("input_pin_sha", path, "Input pin differs from registered approved bytes")
        if node.get("row_admission_required") and stage != "selection_source_scan":
            fail("row_period_admission_required", path,
                 "Raw message input needs approved transaction-key/period row selection; use registered filtered derivative")
        check(node)
    if errors:
        raise SourceAdmissionError(errors, stage)
    identity = {"policy_version": VERSION, "policy_sha256": canonical_sha(policy),
                "stage": stage, "requested_pins": pins,
                "transitive_sources": sorted(checked.values(), key=lambda x: x["path"])}
    return {"passed": True, "admitted": True, "identity": identity,
            "admission_sha256": canonical_sha(identity), "values_read": False,
            "default_unknown": "hold"}


def assert_source_admission(inventory_or_input, policy, stage, *, project_root=ROOT):
    return assert_source_paths(inventory_or_input, policy, stage, project_root=project_root)
