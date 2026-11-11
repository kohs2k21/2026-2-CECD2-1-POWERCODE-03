"""Replay actual LLM provenance, then freeze and remeasure its exact definitions."""
import copy
from pathlib import Path

from .common import (CONTRACT, InputFailure, digest, json_key, load_pack,
                     new_dir, pin, read, seal, sha, write)
from specmodel.batch_runner import replay_batch, resolve
from specmodel.calculations import compile_feature
from specmodel.cli import replay
from specmodel.dataset import Dataset


def group_id(feature, authority):
    return "grain_" + digest({"schema": feature["schema_version"],
                              "grain": feature["plan"]["grain"],
                              "specification_authority": authority})[:20]


def column(feature, alias, proposal):
    # Stable between captures; source-bound IDs are kept separately as evidence.
    return {"name": "f_" + feature["semantic_definition_sha"][:24], "alias": alias,
            "semantic_sha": feature["semantic_definition_sha"],
            "dtype": feature["dtype"], "unit": feature["unit"],
            "proposal": proposal, "specification_feature_id": feature["id"],
            "definition_sha": feature["definition_sha"],
            "availability": feature["availability"]}


def materialize_group(dataset, columns, rules, pending, frozen_id=None):
    compiled = []
    for col in columns:
        result = compile_feature(dataset, col["proposal"], "bridge_CPU_measurement")
        if not result["passed"]:
            raise InputFailure("Frozen Feature no longer compiles: " + repr(result["errors"]))
        f = result["compiled_feature"]
        if f["semantic_definition_sha"] != col["semantic_sha"]:
            raise InputFailure("Feature meaning/type/unit/population changed; redesign required")
        compiled.append(result)
    grain = compiled[0]["compiled_feature"]["plan"]["grain"]
    all_rows = dataset.rows[grain["table"]]
    values = [{json_key(key): value for key, value in zip(c["row_ids"], c["values"])}
              for c in compiled]
    rows = []
    for row in all_rows:
        key = [row[k] for k in grain["keys"]]
        token = json_key(key)
        cells = {}
        for col, measured in zip(columns, values):
            if token not in measured:
                cells[col["name"]] = {"state": "not_selected"}
            else:
                v = measured[token]
                cells[col["name"]] = {"state": "unavailable" if v is None else "value", "value": v}
        rows.append({"source_row_key": key, "features": cells})
    first = compiled[0]["compiled_feature"]
    return {"id": frozen_id or group_id(first, dataset.evidence),
            "grain": grain, "schema_version": first["schema_version"],
            "source_version": dataset.source_version, "split": dataset.manifest.get("split"),
            "data_class": dataset.manifest["data_class"], "period": dataset.manifest["period"],
            "source_pins": dataset.source_pins,
            "split_independence": "Declared role plus original row keys; no independent incident or clone provenance certificate",
            "capture": pin(dataset.path), "columns": columns, "rules": rules,
            "pending": pending, "rows": rows,
            "measurements": [{"column": col["name"],
                              "feature_id": c["compiled_feature"]["id"],
                              "definition_sha": c["compiled_feature"]["definition_sha"],
                              "measurement_sha": c["compiled_feature"]["measurement"]["measurement_sha256"],
                              "independent_calculation": c["compiled_feature"]["independent_calculation"]}
                             for col, c in zip(columns, compiled)]}


def prepare(spec_run, suite, output):
    spec_run, suite = Path(spec_run).resolve(), Path(suite).resolve()
    cfg = read(suite)
    batch = (spec_run / "suite_pin.json").is_file()
    if batch:
        replay_batch(spec_run, suite)
    elif len(cfg["cases"]) != 1:
        raise InputFailure("A single pipeline output requires a one-case suite")
    groups, holds, datasets = {}, [], {}
    for case in cfg["cases"]:
        source = resolve(suite, case["input_manifest"])
        request = resolve(suite, case["request"])
        reference = resolve(suite, case["reference_manifest"]) if case.get("reference_manifest") else None
        folder = spec_run / "cases" / case["id"] if batch else spec_run
        replay(folder, source, request, reference)
        feature = read(folder / "feature_bundle.json")
        if feature["status"] != "feature_verified":
            holds.append({"case": case["id"], "status": feature["status"],
                          "reason": feature.get("reason"), "not_a_feature_or_healthy_result": True})
            continue
        # Only actual generated records become this bridge's frozen model input.
        if not feature.get("origin", "").startswith("actual_Model"):
            raise InputFailure("prepare requires a verified actual LLM definition, not a CPU answer template")
        ds = datasets.setdefault(str(source), None)
        if ds is None:
            ds = datasets[str(source)] = Dataset(source)
        f = feature["compilation"]["compiled_feature"]
        # Same physical CSV does not imply the same confirmed business contract.
        gid = group_id(f, ds.evidence)
        g = groups.setdefault(gid, {"dataset": ds, "columns": [], "rules": [], "pending": []})
        if g["dataset"].source_version != ds.source_version:
            raise InputFailure("One capture per grain per pack required; prepare periods separately")
        col = column(f, case["id"], feature["proposal"])
        previous = next((x for x in g["columns"] if x["name"] == col["name"]), None)
        if previous and previous["semantic_sha"] != col["semantic_sha"]:
            raise InputFailure("Feature hash collision")
        if previous is None:
            g["columns"].append(col)
        rule_file = folder / "validated_rules.json"
        if rule_file.exists():
            for rule in read(rule_file):
                if rule["decision"]["state"] != "proposed":
                    raise InputFailure("Only validated proposed rules may be applied")
                if rule["feature"]["definition_sha"] != f["definition_sha"]:
                    raise InputFailure("Rule is bound to a different Feature")
                d = rule["decision"]
                constraints = {e: copy.deepcopy(ds.evidence[e]) for e in d["evidence"]
                               if e in ds.evidence and ds.evidence[e].get("kind") == "ddl_not_null"}
                rid = "r_" + digest({"feature": col["semantic_sha"], "decision": d})[:24]
                if any(x["id"] == rid for x in g["rules"]):
                    continue
                g["rules"].append({"id": rid, "column": col["name"],
                                   "kind": "HR" if d["request"] == "hr" else "SR",
                                   "decision": d, "source_binding": rule["feature"],
                                   "authority": constraints, "origin": pin(rule_file),
                                   "operational_approval": False})
        if (folder / "pending_decisions.json").exists():
            g["pending"].extend({"column": col["name"], "decision": p["decision"]}
                                for p in read(folder / "pending_decisions.json"))
    pack = {"contract": CONTRACT, "new_LLM_calls": 0, "new_ML_fit": False,
            "origin": {"spec_run": str(spec_run), "suite": pin(suite)},
            "use": "offline research; rules are not operationally approved",
            "holds": holds, "groups": [materialize_group(g["dataset"], g["columns"],
                                                          g["rules"], g["pending"])
                                         for g in groups.values()]}
    out = new_dir(output)
    write(out / "prepared.json", pack)
    seal(out, "feature_pack")
    return pack


def transform(catalog, input_manifest, output):
    frozen = load_pack(catalog)
    ds = Dataset(input_manifest)
    schema = ds.inventory["schema_version"]
    eligible = [g for g in frozen["groups"] if g["schema_version"] == schema]
    if not eligible:
        raise InputFailure("No identical physical/type/unit/relationship schema; explicit redesign required")
    groups, holds = [], []
    for g in eligible:
        missing_authority = []
        for rule in g["rules"]:
            for eid, evidence in rule["authority"].items():
                if ds.evidence.get(eid) != evidence:
                    missing_authority.append(eid)
        if missing_authority:
            holds.append({"group": g["id"], "status": "authority_unassessable",
                          "reason": "HR authority changed or missing", "evidence_ids": sorted(set(missing_authority))})
            continue
        groups.append(materialize_group(ds, g["columns"], g["rules"], g["pending"], g["id"]))
    if not groups:
        raise InputFailure("No applicable catalog group after HR authority verification")
    out = new_dir(output)
    pack = {"contract": CONTRACT, "new_LLM_calls": 0, "new_ML_fit": False,
            "origin": {"frozen_catalog": pin(Path(catalog) / "prepared.json"),
                       "application_capture": pin(input_manifest)},
            "use": frozen["use"], "holds": holds, "groups": groups}
    write(out / "prepared.json", pack)
    seal(out, "feature_pack")
    return pack
