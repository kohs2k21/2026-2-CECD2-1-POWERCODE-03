"""Rich Process inputs, disjoint development references and fresh validation."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation_benchmark.generate import Case, Catalog, generate_case
from model_evaluation.data import (ReplayCatalog, SCENARIOS, history_lookup,
                                  source_references, unix_seconds)
from model_evaluation.models import ComparisonConfig
from model_evaluation.scope import ACTIVE_TASKS
from model_evaluation.windowing import window_reference_split

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT/"data/evaluation"
SEARCH = ROOT/"reports/window_search_v2"
SOURCE_CACHE = ROOT/"reports/evaluation_v2_work/inputs.json"
OUTPUT = ROOT/"reports/model_improvement_v3"


def rich_rows(case, flows):
    return pd.DataFrame([
        {"case_id": case.case_id, "transactionId": row["transactionId"],
         "adapterType": row["adapterType"], "processId": row["processId"],
         "totalCount": row["totalCount"], "retryCount": row["retryCount"],
         "start_seconds": row["startTime"]/1000, "end_seconds": row["endTime"]/1000,
         "duration": row["endTime"]-row["startTime"]}
        for flow in flows.values() for row in flow
        if (row["adapterType"], row["processId"]) == case.key[:2]],
        columns=["case_id", "transactionId", "adapterType", "processId", "totalCount", "retryCount",
                 "start_seconds", "end_seconds", "duration"])


def reference_rows(bundle, state):
    original, _, _, _ = source_references(bundle)
    refs, sets, support, _ = window_reference_split(original, set(state["reserved_template_ids"]))
    history = history_lookup(BENCHMARK)
    config = ComparisonConfig(**json.loads((SEARCH/"selection.json").read_text(encoding="utf-8"))["config"])
    completed = {}
    for split_index, name in enumerate(("train", "calibration", "threshold")):
        templates = [t for t in bundle["templates"] if t["template_id"] in sets[name]]
        catalog = ReplayCatalog(templates)
        rows = []
        for index in range(len(catalog.adapter_names)*config.replay_cases_per_adapter):
            case = Case(catalog, "input_stop", index, config.seed+91_000+split_index*10_000, history)
            case.baseline(config.replay_minutes)
            rows.append(rich_rows(case, case.observed))
        completed[name] = pd.concat(rows, ignore_index=True)
        # Exactly regenerate v2's independent normal references, not final controls.
        if set(completed[name].case_id) != set(state["native"][name].case_id):
            raise ValueError("Reference replay mismatch")
        old = state["completions"][name].sort_values(["case_id", "end_seconds", "duration"]).reset_index(drop=True)
        new = completed[name].sort_values(["case_id", "end_seconds", "duration"]).reset_index(drop=True)
        if len(old) != len(new) or not np.allclose(old[["end_seconds", "duration"]], new[["end_seconds", "duration"]]):
            raise ValueError("Reference regeneration differs from previous protocol")
        print(f"Rich normal references {name}: {len(new):,} completions", flush=True)
    return refs["train"], completed, support


def validation_inputs(bundle, state, count=120, seed=1_910_042):
    catalog = Catalog({**bundle, "templates": [t for t in bundle["templates"]
                                             if t["template_id"] in set(state["reserved_template_ids"])]})
    history = history_lookup(BENCHMARK)
    final_ids = set().union(*(set(pd.read_csv(BENCHMARK/name/"cases.csv", usecols=["case_id"]).case_id)
                              for name in json.loads((BENCHMARK/"manifest.json").read_text(encoding="utf-8"))["datasets"]))
    old_ids = set().union(*json.loads((SEARCH/"selection.json").read_text(encoding="utf-8"))["validation_case_ids"].values())
    bank = {}
    for name in ACTIVE_TASKS:
        observed, normal, rows, normal_rows = [], [], [], []
        for index in range(count):
            case = generate_case(catalog, name, index, seed, history)
            if case.case_id in final_ids | old_ids:
                raise ValueError("Fresh validation case overlaps previous validation or test")
            exported = case.export()
            native = pd.DataFrame(exported["windows.csv"])
            native = native.loc[native.scope.eq("TARGET")].reset_index(drop=True)
            labels = pd.DataFrame(exported["window_labels.csv"])
            labels = labels.loc[labels.scope.eq("TARGET")].reset_index(drop=True)
            native["window_is_anomaly"] = labels.window_is_anomaly.to_numpy(dtype=bool)
            native["scenario_types"] = labels.scenario_types.to_numpy()
            # Alternate 3-case severity blocks so both subsets retain all severities.
            native["validation_split"] = "search" if (index//3) % 2 == 0 else "confirmation"
            control = native.drop(columns=["window_is_anomaly", "scenario_types"]).copy()
            raw, nraw = rich_rows(case, case.observed), rich_rows(case, case.normal)
            first = nraw.groupby("transactionId").start_seconds.min()
            arrivals = (first//60*60).value_counts()
            starts = unix_seconds(control.window_start)
            control["execution_arrivals"] = [int(arrivals.get(t, 0)) for t in starts]
            if not np.array_equal(control.execution_arrivals, labels.normal_execution_arrivals):
                raise ValueError("Normal arrivals mismatch")
            groups = nraw.groupby(nraw.end_seconds//60*60).duration
            for column, values in (("duration_p50_ms", groups.median()), ("duration_p95_ms", groups.quantile(.95))):
                control[column] = values.reindex(starts).to_numpy()
            observed.append(native); normal.append(control); rows.append(raw); normal_rows.append(nraw)
        bank[name] = {"observed": pd.concat(observed, ignore_index=True),
                      "normal": pd.concat(normal, ignore_index=True),
                      "observed_rows": pd.concat(rows, ignore_index=True),
                      "normal_rows": pd.concat(normal_rows, ignore_index=True)}
        print(f"Fresh validation {name}: {count} cases + paired controls", flush=True)
    return bank


def final_rows(folder, labels, cases, events):
    pairs = labels.reset_index(drop=True)[["transactionId", "case_id"]].drop_duplicates()
    if pairs.transactionId.duplicated().any():
        raise ValueError("Ambiguous transaction-case mapping")
    mapping = pairs.set_index("transactionId").case_id.to_dict()
    for event in events.loc[events.scenario.eq("input_stop")].itertuples():
        for tx in json.loads(event.evidence_json)["missing_transaction_ids"]:
            if tx in mapping and mapping[tx] != event.case_id:
                raise ValueError("Ambiguous missing input")
            mapping[tx] = event.case_id
    targets = cases.set_index("case_id")
    result = {}
    for cohort, filename in (("observed", "processes.csv"), ("normal", "normal_processes.csv")):
        chunks = []
        columns = ["transactionId", "adapterType", "processId", "totalCount", "retryCount", "status", "startTime", "endTime"]
        for raw in pd.read_csv(folder/filename, usecols=columns, dtype="string", chunksize=100_000, encoding="utf-8-sig"):
            if not raw.status.eq("S").all():
                raise ValueError("Benchmark must remain all S")
            raw["case_id"] = raw.transactionId.map(mapping)
            if raw.case_id.isna().any():
                raise ValueError("Unmapped execution")
            mask = raw.adapterType.eq(raw.case_id.map(targets.target_adapter)) & raw.processId.eq(raw.case_id.map(targets.target_process_id))
            part = raw.loc[mask].copy()
            part["start_seconds"] = unix_seconds(part.startTime)
            part["end_seconds"] = unix_seconds(part.endTime)
            part["duration"] = (part.end_seconds-part.start_seconds)*1000
            for column in ("totalCount", "retryCount"):
                part[column] = pd.to_numeric(part[column], errors="raise")
            chunks.append(part.drop(columns=["startTime", "endTime", "status"]))
        result[cohort] = pd.concat(chunks, ignore_index=True)
    return result


def control_grid(observed, rows):
    """Rebuild a normal counterfactual from raw normal logs, never oracle counts."""
    frame = observed.drop(columns=["window_is_anomaly", "scenario_types"], errors="ignore").copy()
    starts = unix_seconds(frame.window_start)
    first = rows.groupby(["case_id", "transactionId"]).start_seconds.min().reset_index()
    first["_minute"] = first.start_seconds//60*60
    arrivals = first.groupby(["case_id", "_minute"]).size()
    index = pd.MultiIndex.from_arrays([frame.case_id, starts])
    frame["execution_arrivals"] = arrivals.reindex(index).fillna(0).to_numpy()
    completed = rows.groupby([rows.case_id, rows.end_seconds//60*60]).duration
    frame["duration_p50_ms"] = completed.median().reindex(index).to_numpy()
    frame["duration_p95_ms"] = completed.quantile(.95).reindex(index).to_numpy()
    return frame
