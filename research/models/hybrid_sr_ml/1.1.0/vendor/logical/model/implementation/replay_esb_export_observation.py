"""Full immutable CSV replay, partitioned only by proven plan join locality.

Every source family in the finite CSV exports is retained. This is a factual
export observation, not a declaration that production logging is complete.
No generation, fit, threshold change or business contract is introduced here.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import argparse
import csv
import json
import time

from run import PACKAGE
from specmodel.io import read, write, sha
from serve_rules import serve, SERVING_POLICY
from typed_plan import any3

TABLES = {
    "TransactionExport": ("EC_TRANSACTION_MONITORING.csv",
                          ("TRANSACTION_ID", "PROCESS_HUB_ID", "RETRY_COUNT")),
    "ProcessExport": ("EC_PROCESS_MONITORING.csv",
                      ("TRANSACTION_ID", "PROCESS_ID", "RETRY_COUNT")),
    "MessageExport": ("EC_MESSAGE_MONITORING.csv",
                      ("TRANSACTION_ID", "PROCESS_ID", "MESSAGE_ID", "RETRY_COUNT")),
}


def require_family_locality(pack):
    """Refuse chunking whenever a plan could compare distinct TID families."""
    candidates = pack["proposal"]["candidates"]
    if not any(c["plan"] is not None for c in candidates):
        raise ValueError("No executable generated observation to replay")

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            if "join" in node and not any(
                join.get("source") == join.get("target") == "TRANSACTION_ID"
                for join in node["join"]):
                raise ValueError("Cross-family joins cannot use this family-partitioned runner")
            for value in node.values():
                visit(value)

    for candidate in candidates:
        if candidate["role"] == "HR_candidate":
            raise ValueError("This finite-export runner does not authorize company HR")
        plan = candidate["plan"]
        if plan is None:
            continue
        if plan["kind"] == "duplicate_key" and "TRANSACTION_ID" not in plan["keys"]:
            raise ValueError("Cross-family duplicate detection cannot be partitioned by TID")
        visit(plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--families-per-batch", type=int, default=500)
    a = parser.parse_args()
    if not 1 <= a.families_per_batch <= 2000:
        raise ValueError("Family batch size must be 1..2000")
    source, pack = read(a.source), read(a.rules)
    require_family_locality(pack)
    if set(source["records"]) != set(TABLES):
        raise ValueError("Exact reviewed export schema expected")
    if (source["capture"].get("export_snapshot_only") is not True
            or source["capture"].get("business_logging_complete_unconfirmed") is not True
            or source["capture"].get("entity_family_complete") is not True):
        raise ValueError("Explicit finite-export closure and business uncertainty required")
    if any(set(source["schema"][table]["columns"]) != set(fields)
           for table, (_, fields) in TABLES.items()):
        raise ValueError("Frozen metadata projection differs from source schema")
    a.output.mkdir(parents=True, exist_ok=False)
    inputs = [a.source, a.rules] + [a.data_root / spec[0] for spec in TABLES.values()]
    pins = {str(path.resolve()): sha(path) for path in inputs}
    write(a.output / "input_pins.json", {"files": pins, "script_sha256": sha(__file__),
        "serving_policy": SERVING_POLICY, "no_LLM_no_fit": True})
    started = time.perf_counter()
    records, families = {}, {}
    for table, (filename, fields) in TABLES.items():
        records[table] = []
        with (a.data_root / filename).open(encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
            if len(header) != len(set(header)):
                raise ValueError("Duplicate CSV columns")
            indexes = [header.index(key) for key in fields]
            for index, row in enumerate(reader):
                if len(row) != len(header):
                    raise ValueError("Malformed CSV row width")
                value = {key: None if row[i] == "" else row[i]
                         for key, i in zip(fields, indexes)}
                tid = value["TRANSACTION_ID"]
                if tid is None:
                    raise ValueError("Unknown TID prevents a justified complete family partition")
                records[table].append(value)
                families.setdefault(tid, defaultdict(list))[table].append(index)
    # Pointers are values within this finite export only. Their equality does
    # not establish uniqueness of operational retries or business occurrences.
    counts, transaction_counts, coverage_counts = Counter(), Counter(), Counter()
    per_rule, warnings, batches = defaultdict(Counter), [], []
    row_path, transaction_path = a.output / "row_decisions.csv", a.output / "transaction_decisions.csv"
    with row_path.open("x", newline="") as row_out, transaction_path.open("x", newline="") as tx_out:
        row_writer = csv.DictWriter(row_out, fieldnames=["source_table", "source_row_index", "TRANSACTION_ID",
            "source_keys", "rule_id", "role", "value", "decision", "feature_id",
            "feature_definition_sha256", "support_rows"])
        row_writer.writeheader()
        tx_writer = csv.DictWriter(tx_out, fieldnames=["TRANSACTION_ID", "decision",
            "coverage_complete_for_proposed_checks", "warning_rule_ids", "unavailable_rule_ids",
            "source_row_counts"])
        tx_writer.writeheader()
        ordered = list(families)
        for offset in range(0, len(ordered), a.families_per_batch):
            selected = ordered[offset:offset + a.families_per_batch]
            case, mapping = deepcopy(source), {}
            for table in TABLES:
                indexes = [i for tid in selected for i in families[tid].get(table, [])]
                case["records"][table] = [records[table][i] for i in indexes]
                mapping[table] = indexes
            case["capture"]["source_version"] = "full-csv-export-families-" + str(offset)
            result = serve(pack, case, bulk=True)
            values_by_tid_rule = defaultdict(lambda: defaultdict(list))
            links = {(x["original_model_proposal_id"], x["source_table"], x["source_row_index"]): x
                     for x in result["linked_feature_values"]}
            for rule in result["rules"]:
                for row in rule["rows"]:
                    table, local_index = row["source_table"], row["source_row_index"]
                    tid, value = row["source_record"]["TRANSACTION_ID"], row["value"]
                    decision = "warning" if value is True else "unavailable" if value is None else "nonwarning"
                    values_by_tid_rule[tid][rule["rule_id"]].append(value)
                    counts[(table, decision)] += 1
                    per_rule[rule["rule_id"]][decision] += 1
                    linked = links[(rule["rule_id"], table, local_index)]
                    support = [{"table": x["table"],
                                "source_row_index": mapping[x["table"]][x["row_index"]]}
                               for x in row["support_rows"]]
                    evidence = {"source_table": table, "source_row_index": mapping[table][local_index],
                        "TRANSACTION_ID": tid, "source_keys": linked["source_keys"],
                        "rule_id": rule["rule_id"], "role": rule["role"], "value": value,
                        "decision": decision, "feature_id": linked["feature_id"],
                        "feature_definition_sha256": linked["feature_definition_sha256"],
                        "support_rows": support}
                    row_writer.writerow({**evidence,
                        "source_keys": json.dumps(evidence["source_keys"], sort_keys=True),
                        "support_rows": json.dumps(support, sort_keys=True)})
                    if value is True:
                        warnings.append(evidence)
            for tid in selected:
                states, warning_ids, unavailable_ids = [], [], []
                for candidate in pack["proposal"]["candidates"]:
                    observed = values_by_tid_rule[tid].get(candidate["id"], [])
                    value = any3(observed) if observed and candidate["plan"] is not None else None
                    states.append(value)
                    if value is True:
                        warning_ids.append(candidate["id"])
                    elif value is None:
                        unavailable_ids.append(candidate["id"])
                value = any3(states)
                decision = "warning" if value is True else "unavailable" if value is None else "nonwarning"
                complete = bool(states) and not unavailable_ids
                transaction_counts[decision] += 1
                coverage_counts[str(complete)] += 1
                tx_writer.writerow({"TRANSACTION_ID": tid, "decision": decision,
                    "coverage_complete_for_proposed_checks": complete,
                    "warning_rule_ids": json.dumps(warning_ids), "unavailable_rule_ids": json.dumps(unavailable_ids),
                    "source_row_counts": json.dumps({table: len(families[tid].get(table, [])) for table in TABLES})})
            batches.append({"family_offset": offset, "families": len(selected),
                "source_rows": {table: len(case["records"][table]) for table in TABLES},
                "decision": result["decision"], "type_mismatches": len(result["type_mismatches"])})
    seconds = time.perf_counter() - started
    if any(sha(Path(path)) != expected for path, expected in pins.items()):
        raise ValueError("Protected source changed during frozen replay")
    write(a.output / "warning_source_evidence.json", warnings)
    summary = {"source_rows": {table: len(rows) for table, rows in records.items()},
        "distinct_observed_nonmissing_TID_strings": len(families),
        "TID_scope": "Export string lookup groups, not verified operational retry/hub occurrence identities.",
        "row_rule_evaluations": [{"source_table": table, "decision": decision, "count": count}
                                 for (table, decision), count in sorted(counts.items())],
        "per_rule": dict(per_rule), "TID_decisions": dict(transaction_counts),
        "TID_coverage_complete": dict(coverage_counts), "warning_row_rule_evaluations": len(warnings),
        "seconds_CSV_read_projection_serving_and_decision_CSV_writes": seconds,
        "total_source_rows_per_second": sum(map(len, records.values())) / seconds,
        "source_read_and_replay_batch_count": len(batches), "batches": batches,
        "actual_LLM_calls": 0, "model_or_threshold_fit": False, "protected_inputs_unchanged": True,
        "Body_response_message_or_payload_retained": False,
        "pending_company_business_logging_evidence": True,
        "scope": "Frozen AI-generated finite-export factual observation. Missing root observations remain unavailable at TID level. These are not independently labeled operating incidents, approved business HR, or real-time latency measurements.",
        "output_pins": {path.name: sha(path) for path in (row_path, transaction_path,
                                                        a.output / "warning_source_evidence.json")}}
    write(a.output / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("batches", "output_pins")}))


if __name__ == "__main__":
    main()
