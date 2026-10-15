"""Strengthen existing injected deviations by 20%, with paired controls frozen.

Reconstruct ORIGINAL cases from their saved seeds and verify their fingerprints.
Never search for cases that a detector can recognize. Hard remains byte-identical.
CSV authoring uses the existing bounded Artifact Tool writer; originals untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
import numpy as np
from evaluation_benchmark.generate import (Case, Catalog, PROCESS, SCENARIOS,
    StreamTables, TABLES, make_history, stable_seed, stamp, time_text)
from model_evaluation.data import sha256

# Original version is a recovery/provenance backup, not a second active dataset.
SOURCE = ROOT / "reports/benchmark_before_mild_20261014"
WORK = ROOT / "reports/benchmark_mild_v1_work"
DEST = ROOT / "data/evaluation"
PROFILE = Path(__file__).with_name("profile.json")
MOD = 1 << 256


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def semantic_bag(records):
    """Order-independent row count + sum of canonical SHA256 row hashes."""
    total, n = 0, 0
    for row in records:
        text = json.dumps(["" if row.get(c) is None else str(row.get(c)) for c in PROCESS],
                          ensure_ascii=False, separators=(",", ":"))
        total = (total + int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")) % MOD
        n += 1
    return total, n


def original_fingerprint(case):
    all_rows = [r for flows in (case.normal, case.observed) for rows in flows.values() for r in rows]
    first = min(r["startTime"] for r in all_rows) // 60_000 * 60_000
    records = [[r["adapterType"], r["processId"], r["totalCount"], r["startTime"] - first,
                r["endTime"] - r["startTime"]] for rows in case.observed.values() for r in rows]
    return hashlib.sha256(json.dumps(sorted(records), separators=(",", ":")).encode()).hexdigest()


def normal_records(case):
    for rows in case.normal.values():
        for row in rows:
            yield {**row, "startTime": time_text(row["startTime"]), "endTime": time_text(row["endTime"])}


def find_attempt(seed, dataset, index, case_seed):
    for attempt in range(128):
        value = stable_seed(seed, dataset, index) if attempt == 0 else stable_seed(seed, dataset, index, "retry", attempt)
        if str(value) == str(case_seed):
            return attempt
    raise ValueError("Original seed cannot be reconstructed; never substitute a case")


def duration(case, tx, pid):
    r = next(r for r in case.observed[tx] if r["processId"] == pid)
    return r["endTime"] - r["startTime"]


def scaled_duration(baseline, observed, multiplier, cap):
    return max(1, min(math.floor(cap - 1), math.floor(baseline + multiplier * (observed - baseline))))


def strengthen(case, profile):
    """Modify only the original injection; preserve every normal control row."""
    if case.hard:
        return case, []
    normal_before = semantic_bag(normal_records(case))
    factor = float(profile["excess_multiplier"])
    audits = []
    for event in case.events:
        kind, eid = event["scenario"], event["event_id"]
        evidence = json.loads(event["evidence_json"])
        before = deepcopy(evidence)
        begin, end = stamp(event["startTime"]), stamp(event["endTime"])
        targets = [(tx, pid) for (tx, pid), marks in case.labels.items() if any(m[1] == eid for m in marks)]
        if kind in ("individual_delay", "group_delay", "column_relation"):
            old_values, new_values = [], []
            for tx, pid in targets:
                row = next(r for r in case.observed[tx] if r["processId"] == pid)
                normal = next(r for r in case.normal[tx] if r["processId"] == pid)
                base = normal["endTime"] - normal["startTime"]
                old = duration(case, tx, pid)
                new = scaled_duration(base, old, factor, case.catalog.cap(row["adapterType"], "duration_ms"))
                case.set_duration(tx, pid, new)
                old_values.append(old); new_values.append(new)
            if kind in ("individual_delay", "group_delay"):
                evidence["target_duration_ms"] = max(new_values)
                evidence["target_duration_range_ms"] = [min(new_values), max(new_values)]
            else:
                evidence["low_count_new_duration_ms"] = next(duration(case, tx, pid) for tx, pid in targets
                    if next(r for r in case.observed[tx] if r["processId"] == pid)["totalCount"] == evidence["low_count"])
                evidence["high_count_new_duration_ms"] = next(duration(case, tx, pid) for tx, pid in targets
                    if next(r for r in case.observed[tx] if r["processId"] == pid)["totalCount"] == evidence["high_count"])
            end = max(end, max(r["endTime"] for tx, pid in targets for r in case.observed[tx] if r["processId"] == pid) + 1)
            audits.append({"case_id": case.case_id, "scenario": kind, "old_duration_sum_ms": sum(old_values), "new_duration_sum_ms": sum(new_values)})
        elif kind == "gradual_degradation":
            p = evidence["reference_profile"]
            maximum = max(v["normal_duration_ms"] for v in evidence["increments"])
            increment = min(evidence["max_increment_ms"] * factor,
                            p["frozen_duration_cap_ms"] - p["p75_ms"] - 2,
                            p["frozen_duration_cap_ms"] - maximum - 2)
            for item in evidence["increments"]:
                tx = item["transactionId"]
                value = math.floor(item["normal_duration_ms"] + item["progress"] * increment)
                case.set_duration(tx, case.key[1], value)
                item["increment_ms"] = value - item["normal_duration_ms"]
            evidence["max_increment_ms"] = increment
            audits.append({"case_id": case.case_id, "scenario": kind,
                           "old_increment_ms": before["max_increment_ms"], "new_increment_ms": increment,
                           "cap_limited": increment < before["max_increment_ms"] * factor})
        elif kind == "time_burst":
            old_added = int(event["added_executions"])
            added = math.ceil(old_added * factor) - old_added
            rng = np.random.default_rng(stable_seed(case.seed, "mild-v1-extra-burst"))
            minute, length = (begin - case.start) / 60_000, (end - begin) / 60_000
            for j in range(added):
                shape = evidence["shape"]
                if shape == "pulse":
                    at = minute + rng.uniform(0, min(length, 2))
                elif shape == "two_pulses":
                    at = minute + (0 if j % 2 == 0 else max(0, length - 2)) + rng.uniform(0, min(length, 2))
                else:
                    at = minute + rng.uniform(0, length)
                tx, pid = case.clone(case.key, min(float(at), minute + length - .8), normal=False)
                case.labels[(tx, pid)].append((kind, eid, False))
            affected = case.focus_rows(begin, end)
            event["added_executions"] += added
            event["affected_process_rows"] = len(affected)
            evidence["observed_focus_rows"] = len(affected)
            evidence["actual_factor"] = len(affected) / evidence["normal_focus_rows"]
            evidence["requested_factor"] = 1 + factor * (before["requested_factor"] - 1)
            audits.append({"case_id": case.case_id, "scenario": kind,
                           "old_added": old_added, "new_added": event["added_executions"]})
        elif kind == "input_stop":
            minutes = (end - begin) / 60_000
            new_minutes = math.ceil(minutes * float(profile["stop_duration_multiplier"]))
            end = begin + new_minutes * 60_000
            removed = sorted({tx for tx, pid in case.focus_rows(begin, end)})
            removed_ids = list(evidence["missing_transaction_ids"]) + removed
            evidence["missing_process_rows"] += sum(len(case.observed[tx]) for tx in removed)
            for tx in removed:
                del case.observed[tx]; del case.meta[tx]
            event["removed_executions"] = evidence["expected_executions"] = len(removed_ids)
            evidence["missing_transaction_ids"] = removed_ids
            audits.append({"case_id": case.case_id, "scenario": kind,
                           "old_minutes": minutes, "new_minutes": new_minutes})
        evidence["difficulty_adjustment"] = {
            "profile": profile["version"], "excess_multiplier": factor,
            "original_evidence": before, "normal_control_unchanged": True}
        event["evidence_json"] = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        event["endTime"] = time_text(end)
    if semantic_bag(normal_records(case)) != normal_before:
        raise ValueError("Normal controls changed")
    for rows in case.observed.values():
        for row in rows:
            if row["status"] != "S" or row["endTime"] < row["startTime"]:
                raise ValueError("Invalid generated Process")
            for metric, value in (("duration_ms", row["endTime"] - row["startTime"]),
                                  ("totalCount", row["totalCount"]), ("retryCount", row["retryCount"])):
                if value > case.catalog.cap(row["adapterType"], metric):
                    raise ValueError("Adjusted case exceeds unchanged frozen hard rule")
    return case, audits


def inventory(source):
    return {str(p.relative_to(source)).replace("\\", "/"): sha256(p) for p in source.rglob("*") if p.is_file()}


def generate():
    if (WORK / "generated").exists() or DEST.exists():
        raise FileExistsError("Use a new version/path; never overwrite an existing benchmark")
    profile, root = read_json(PROFILE), read_json(SOURCE / "manifest.json")
    WORK.mkdir(parents=True, exist_ok=True)
    write_json(WORK / "frozen_protocol.json", {"profile": profile, "original_files": inventory(SOURCE),
        "models": {str(p.relative_to(ROOT)): sha256(p) for p in (ROOT / "reports/model_research_v12/handoff").glob("*.joblib")},
        "profile_sha256": sha256(PROFILE), "selection_by_detector": False,
        "normal_controls_used_for_training": False, "threshold_refit": False})
    bundle = read_json(ROOT / "reports/evaluation_v2_work/inputs.json")
    catalog = Catalog(bundle)
    _, history = make_history(root["seed"])
    root_writer = StreamTables(WORK / "generated", {"scenario_summary.csv": ["dataset", "anomaly_cases", "normal_cases",
        "process_rows", "normal_process_rows", "event_count", "target_adapters", "unique_content_cases"]})
    all_audits = []
    for dataset in SCENARIOS:
        original_meta = read_json(SOURCE / dataset / "manifest.json")
        with (SOURCE / dataset / "cases.csv").open(encoding="utf-8-sig", newline="") as handle:
            records = {r["case_id"]: r for r in csv.DictReader(handle)}
        n = len(records)
        writer = StreamTables(WORK / "generated" / dataset,
            {k: v for k, v in TABLES.items() if k not in ("normal_processes.csv", "normal_transactions_context.csv")})
        fingerprints, periods, events, adapters = set(), Counter(), Counter(), Counter()
        normal_total, normal_n = 0, 0
        for index in range(n):
            cid = f"C{stable_seed(root['seed'], dataset, index, 'id'):016x}"
            saved = records[cid]
            attempt = find_attempt(root["seed"], dataset, index, saved["case_seed"])
            case = Case(catalog, dataset, index, root["seed"], history, attempt).build()
            if original_fingerprint(case) != saved["unique_content_fingerprint"]:
                raise ValueError(f"Original case reconstruction failed: {dataset}/{cid}")
            subtotal, count = semantic_bag(normal_records(case))
            normal_total, normal_n = (normal_total + subtotal) % MOD, normal_n + count
            case, audits = strengthen(case, profile)
            all_audits.extend(audits)
            exported = case.export()
            fp = exported["cases.csv"][0]["unique_content_fingerprint"]
            if fp in fingerprints:
                raise ValueError("Duplicate adjusted case content")
            fingerprints.add(fp)
            for name in writer.schemas:
                writer.add(name, exported[name])
            periods.update(r["source_period"] for r in exported["process_labels.csv"])
            events.update(e["scenario"] for e in case.events)
            events.update("adapter:" + r["adapterType"] for r in exported["processes.csv"])
            adapters[case.key[0]] += 1
            if (index + 1) % 250 == 0 or index + 1 == n:
                print(f"mild {dataset}: {index + 1}/{n} cases", flush=True)
        with (SOURCE / dataset / "normal_processes.csv").open(encoding="utf-8-sig", newline="") as handle:
            actual = semantic_bag(csv.DictReader(handle))
        if actual != (normal_total, normal_n):
            raise ValueError(f"Reconstructed normal controls do not match original CSV: {dataset}")
        metadata = {k: deepcopy(v) for k, v in original_meta.items() if k not in ("files", "table_rows")}
        metadata.update(benchmark_version=profile["version"], difficulty_profile=profile,
            original_manifest_sha256=sha256(SOURCE / dataset / "manifest.json"),
            source_period_process_rows=dict(periods), event_and_adapter_counts=dict(events),
            unique_content_cases=len(fingerprints), all_original_cases_preserved=True,
            regenerated_normal_semantic_hash={"count": normal_n, "sum_sha256": f"{normal_total:064x}"})
        writer.close(metadata)
        rows = {**metadata["table_rows"], **{k: original_meta["files"][k]["rows"] for k in ("normal_processes.csv", "normal_transactions_context.csv")}}
        root_writer.add("scenario_summary.csv", [{"dataset": dataset, "anomaly_cases": n, "normal_cases": n,
            "process_rows": rows["processes.csv"], "normal_process_rows": rows["normal_processes.csv"],
            "event_count": rows["events.csv"], "target_adapters": len(adapters), "unique_content_cases": len(fingerprints)}])
    hard = read_json(SOURCE / "mixed_hard/manifest.json")
    root_writer.add("scenario_summary.csv", [{"dataset": "mixed_hard", "anomaly_cases": hard["anomaly_cases"], "normal_cases": hard["normal_cases"],
        "process_rows": hard["files"]["processes.csv"]["rows"], "normal_process_rows": hard["files"]["normal_processes.csv"]["rows"],
        "event_count": hard["files"]["events.csv"]["rows"], "target_adapters": len(hard["target_adapter_case_counts"]), "unique_content_cases": hard["unique_content_cases"]}])
    metadata = {k: deepcopy(v) for k, v in root.items() if k not in ("files", "table_rows")}
    metadata.update(benchmark_version=profile["version"], datasets=list(SCENARIOS), difficulty_profile=profile,
        original_manifest_sha256=sha256(SOURCE / "manifest.json"), interpretation="difficulty sensitivity, not model improvement")
    root_writer.close(metadata)
    write_json(WORK / "adjustments.json", all_audits)


def finish():
    """Complete exported CSV inventory with byte-identical controls and Hard."""
    root = read_json(DEST / "manifest.json")
    original = read_json(SOURCE / "manifest.json")
    for dataset in SCENARIOS:
        meta = read_json(DEST / dataset / "manifest.json")
        old = read_json(SOURCE / dataset / "manifest.json")
        for name in ("normal_processes.csv", "normal_transactions_context.csv"):
            target = DEST / dataset / name
            if target.exists():
                raise FileExistsError(target)
            shutil.copy2(SOURCE / dataset / name, target)
            if sha256(target) != old["files"][name]["sha256"]:
                raise ValueError("Normal copy changed")
            meta["files"][name] = old["files"][name]
            meta["table_rows"][name] = old["files"][name]["rows"]
        write_json(DEST / dataset / "manifest.json", meta)
    shutil.copytree(SOURCE / "mixed_hard", DEST / "mixed_hard")
    for name, info in original["files"].items():
        if name == "scenario_summary.csv":
            continue
        shutil.copy2(SOURCE / name, DEST / name)
        root["files"][name] = info
        root["table_rows"][name] = info["rows"]
    root["datasets"] = [*SCENARIOS, "mixed_hard"]
    write_json(DEST / "manifest.json", root)
    frozen = read_json(WORK / "frozen_protocol.json")
    if inventory(SOURCE) != frozen["original_files"]:
        raise ValueError("Original benchmark changed")
    print("Original benchmark preserved; all paired controls and mixed Hard copied byte-identically", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("generate", "finish"), required=True)
    args = parser.parse_args()
    (generate if args.stage == "generate" else finish)()
