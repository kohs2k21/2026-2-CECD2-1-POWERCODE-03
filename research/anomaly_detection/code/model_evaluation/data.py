"""Oracle-safe inputs and independent normal development references.

The original benchmark, source CSVs and old serving artifact are never edited.
Counterfactual normal evaluation episodes are not training/calibration data.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation_benchmark.generate import Case
from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.data import chronological_split, prepare
from .models import WINDOW_FEATURES
from .scope import ACTIVE_TASKS

ROOT = Path(__file__).resolve().parents[2]
POINT_DATASETS = {"individual_delay", "group_delay", "column_relation", "mixed_hard"}
# Serialized scenario_bits in historical caches use these positions. NEVER renumber.
SCENARIOS = ("individual_delay", "group_delay", "column_relation", "time_burst", "input_stop", "gradual_degradation")
KEYS = ["transactionId", "processId", "retryCount"]
WINDOW_KEYS = ["case_id", "scope", "window_start", "window_end"]


def unix_seconds(values):
    """Explicit resolution: pandas 3 may store parsed timestamps in microseconds."""
    stamps = pd.to_datetime(values,format="ISO8601",errors="raise")
    return stamps.astype("datetime64[ns]").astype("int64").to_numpy()/1e9


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_benchmark(root, source_cache):
    manifest = json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((root/"validation.json").read_text(encoding="utf-8"))
    expected_coverage = "all_five_active_datasets" if manifest["datasets"] == list(ACTIVE_TASKS) else "all_seven_datasets"
    if not validation.get("passed") or validation.get("coverage") != expected_coverage:
        raise ValueError("A complete, scope-matching validation report is required")
    if manifest["datasets"] not in (list(ACTIVE_TASKS), [*SCENARIOS, "mixed_hard"]):
        raise ValueError("Unexpected benchmark datasets")
    for folder in [root, *(root/name for name in manifest["datasets"])]:
        info = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
        for filename, record in info["files"].items():
            if Path(filename).name != filename or sha256(folder/filename) != record["sha256"]:
                raise ValueError(f"Benchmark CSV changed: {folder.name}/{filename}")
    bundle = json.loads(source_cache.read_text(encoding="utf-8"))
    if bundle["source_audit"] != manifest["source_audit"]:
        raise ValueError("Normal reference archive belongs to a different benchmark")
    artifact = manifest["frozen_policy"]["artifact_path"].replace("\\", "/")
    if sha256(ROOT/artifact) != manifest["frozen_policy"]["artifact_sha256"]:
        raise ValueError("Original frozen-policy artifact changed")
    return manifest, bundle


def source_references(bundle):
    records = [{**row, "source_period": template["source_period"], "source_template_id": template["template_id"]}
               for template in bundle["templates"] for row in template["rows"]]
    frame = prepare(pd.DataFrame(records))
    split = chronological_split(frame, PipelineConfig.from_dict(bundle["policy"]["config"]))
    partitions = {name: frame.loc[split.eq(name)].reset_index(drop=True)
                  for name in ("train", "calibration", "threshold")}
    if not frame.status.eq("S").all():
        raise ValueError("Reference archive must contain only verified successful workflows")
    template_sets = {name: set(p.source_template_id) for name, p in partitions.items()}
    if any(template_sets[a] & template_sets[b] for a,b in (("train","calibration"),("train","threshold"),("calibration","threshold"))):
        raise ValueError("A source execution leaked across reference partitions")
    support = defaultdict(dict)
    for name, part in partitions.items():
        for adapter, group in part.groupby("adapterType", observed=True):
            support[str(adapter)][name] = int(group.source_template_id.nunique())
    train = partitions["train"]
    baselines = {
        "process": train.groupby(["adapterType", "processId"], observed=True).duration_ms.median().to_dict(),
        "adapter": train.groupby("adapterType", observed=True).duration_ms.median().to_dict(),
        "global": float(train.duration_ms.median()),
    }
    return partitions, template_sets, dict(support), baselines


class ReplayCatalog:
    """Only the normal replay methods required by Case; no anomaly fitting."""

    def __init__(self, templates):
        self.templates = templates
        self.groups, self.adapter_templates = defaultdict(list), defaultdict(set)
        for ti, template in enumerate(templates):
            for ri, row in enumerate(template["rows"]):
                key = (row["adapterType"], row["processId"], row["totalCount"])
                self.groups[key].append((ti, ri))
                self.adapter_templates[key[0]].add(ti)
        self.adapter_names = sorted(self.adapter_templates)
        self.adapter_templates = {a: sorted(v) for a,v in self.adapter_templates.items()}

    def choose_key(self, index, rng, relation=False):
        adapter = self.adapter_names[index % len(self.adapter_names)]
        ti = self.adapter_templates[adapter][int(rng.integers(len(self.adapter_templates[adapter])))]
        row = next(r for r in self.templates[ti]["rows"] if r["adapterType"] == adapter)
        return adapter, row["processId"], row["totalCount"]

    def pick(self, key, rng):
        refs = self.groups[key]
        return refs[int(rng.integers(len(refs)))]


def history_lookup(benchmark):
    frame = pd.read_csv(benchmark/"reference_history_windows.csv", encoding="utf-8-sig")
    return {(kind == "weekend", int(hour)): list(g.execution_arrivals)
            for (kind,hour),g in frame.groupby(["day_type","hour"], sort=False)}


def window_features(frame, baselines):
    frame = frame.copy()
    frame["_start"] = pd.to_datetime(frame.window_start, format="ISO8601", errors="raise")
    frame["_end"] = pd.to_datetime(frame.window_end, format="ISO8601", errors="raise")
    frame = frame.sort_values(["case_id", "_start"], kind="stable").reset_index(drop=True)
    arrivals = pd.to_numeric(frame.execution_arrivals)
    past = pd.to_numeric(frame.history_median_arrivals)
    past_p95 = pd.to_numeric(frame.history_p95_arrivals)
    if (arrivals < 0).any() or not np.isfinite(past_p95).all():
        raise ValueError("Invalid arrivals or missing prior history")
    if "execution_arrivals_5m" in frame:
        # Already aggregated from native minute arrivals. Never sum overlapping windows.
        rolling = frame.execution_arrivals_5m
        rolling_past = frame.history_median_arrivals_5m
        rolling_scale = frame.history_p95_arrivals_5m
    else:
        rolling = arrivals.groupby(frame.case_id, sort=False).rolling(5, min_periods=1).sum().reset_index(level=0, drop=True).reindex(frame.index)
        rolling_past = past.groupby(frame.case_id, sort=False).rolling(5, min_periods=1).sum().reset_index(level=0, drop=True).reindex(frame.index)
        rolling_scale = past_p95.groupby(frame.case_id, sort=False).rolling(5, min_periods=1).sum().reset_index(level=0, drop=True).reindex(frame.index)
    reference = np.array([baselines["process"].get((a,p), baselines["adapter"].get(a, baselines["global"]))
                          for a,p in zip(frame.adapterType, frame.processId)], dtype=float)
    p50 = pd.to_numeric(frame.duration_p50_ms)
    p95 = pd.to_numeric(frame.duration_p95_ms)
    frame["log_arrivals"] = np.log1p(arrivals)
    frame["arrival_deviation"] = (arrivals-past)/(past_p95+1)
    frame["log_arrivals_5m"] = np.log1p(rolling)
    frame["arrival_deviation_5m"] = (rolling-rolling_past)/(rolling_scale+1)
    frame["log_p50_duration"] = np.log1p(p50)
    frame["log_p95_duration"] = np.log1p(p95)
    frame["p95_duration_relative"] = np.log1p(p95)-np.log1p(reference)
    frame["missing_duration"] = p95.isna().astype(float)
    for name, value, period in (("hour",frame._start.dt.hour,24),("weekday",frame._start.dt.dayofweek,7)):
        frame[name+"_sin"] = np.sin(2*np.pi*value/period)
        frame[name+"_cos"] = np.cos(2*np.pi*value/period)
    if not set(WINDOW_FEATURES).issubset(frame):
        raise ValueError("Missing feature")
    return frame


def normal_development(bundle, template_sets, benchmark, baselines, config, return_rows=False):
    history = history_lookup(benchmark)
    partitions, metadata, completion_partitions = {}, [], {}
    for split_i, name in enumerate(("train", "calibration", "threshold")):
        templates = [t for t in bundle["templates"] if t["template_id"] in template_sets[name]]
        catalog = ReplayCatalog(templates)
        windows, completed_rows = [], []
        count = len(catalog.adapter_names)*config.replay_cases_per_adapter
        for index in range(count):
            case = Case(catalog,"input_stop",index,config.seed+91_000+split_i*10_000,history)
            # Do NOT call build(): independent normal reference episodes, no injection.
            case.baseline(config.replay_minutes)
            exported = case.export()
            windows.extend(r for r in exported["windows.csv"] if r["scope"] == "TARGET")
            if return_rows:
                completed_rows.extend({"case_id":case.case_id,"end_seconds":r['endTime']/1000,
                                       "duration":r['endTime']-r['startTime']}
                                      for flow in case.observed.values() for r in flow
                                      if (r['adapterType'],r['processId']) == case.key[:2])
        partitions[name] = window_features(pd.DataFrame(windows), baselines)
        if return_rows:
            completion_partitions[name] = pd.DataFrame(completed_rows)
        metadata.append({"split": name, "normal_episodes": count, "windows": len(windows),
                         "independent_source_templates": len(templates), "adapters": len(catalog.adapter_names)})
        print(f"Normal window reference {name}: {count:,} independent episodes, {len(windows):,} windows", flush=True)
    return (partitions, metadata, completion_partitions) if return_rows else (partitions, metadata)


def labels_for_rows(folder):
    columns = KEYS+["case_id", "row_is_anomaly", "collective_context_is_anomaly"]
    frame = pd.read_csv(folder/"process_labels.csv",usecols=columns,encoding="utf-8-sig",dtype={k:"string" for k in KEYS+["case_id"]})
    if frame.duplicated(KEYS).any():
        raise ValueError("Duplicate ground-truth Process key")
    return frame.set_index(KEYS, drop=False)


def observed_windows(folder):
    frame = pd.read_csv(folder/"windows.csv",encoding="utf-8-sig")
    labels = pd.read_csv(folder/"window_labels.csv",encoding="utf-8-sig")
    frame = frame.loc[frame.scope.eq("TARGET")].reset_index(drop=True)
    labels = labels.loc[labels.scope.eq("TARGET")]
    merged = frame.merge(labels[WINDOW_KEYS+["window_is_anomaly","scenario_types"]],on=WINDOW_KEYS,validate="one_to_one",how="left")
    if merged.window_is_anomaly.isna().any():
        raise ValueError("Missing window ground truth")
    return merged


def normal_windows(folder, observed, labels, cases, chunksize=100_000, return_rows=False):
    """Reconstruct true normal statistics, NOT substitute oracle arrival counts."""
    tx_case = labels.reset_index(drop=True)[["transactionId","case_id"]].drop_duplicates()
    if tx_case.transactionId.duplicated().any():
        raise ValueError("Transaction belongs to multiple independent cases")
    mapping = tx_case.set_index("transactionId").case_id.to_dict()
    events = pd.read_csv(folder/"events.csv",encoding="utf-8-sig")
    for e in events.loc[events.scenario.eq("input_stop")].itertuples():
        for tx in json.loads(e.evidence_json)["missing_transaction_ids"]:
            if tx in mapping and mapping[tx] != e.case_id:
                raise ValueError("Ambiguous missing execution")
            mapping[tx] = e.case_id
    targets = cases.set_index("case_id")
    selected = []
    columns = ["transactionId","adapterType","processId","startTime","endTime"]
    for raw in pd.read_csv(folder/"normal_processes.csv",usecols=columns,encoding="utf-8-sig",dtype="string",chunksize=chunksize):
        raw["case_id"] = raw.transactionId.map(mapping)
        if raw.case_id.isna().any():
            raise ValueError("Unmapped normal execution; do not lose deleted input")
        mask = raw.case_id.isin(targets.index) & raw.adapterType.eq(raw.case_id.map(targets.target_adapter)) & raw.processId.eq(raw.case_id.map(targets.target_process_id))
        part = raw.loc[mask].copy()
        part["start"] = pd.to_datetime(part.startTime,format="ISO8601",errors="raise")
        part["end"] = pd.to_datetime(part.endTime,format="ISO8601",errors="raise")
        part["duration"] = (part.end-part.start).dt.total_seconds()*1000
        selected.append(part[["case_id","transactionId","start","end","duration"]])
    rows = pd.concat(selected,ignore_index=True)
    rows["started_minute"] = rows.start.dt.floor("min")
    rows["completed_minute"] = rows.end.dt.floor("min")
    started = rows.groupby(["case_id","started_minute"],sort=False).size()
    first = rows.groupby(["case_id","transactionId"],sort=False).start.min().reset_index()
    first["minute"] = first.start.dt.floor("min")
    arrivals = first.groupby(["case_id","minute"],sort=False).size()
    completed = rows.groupby(["case_id","completed_minute"],sort=False).duration
    done, p50, p95 = completed.size(), completed.median(), completed.quantile(.95)
    # Use only the agreed episode grid and prior-history inputs from the blueprint.
    base = observed[[*WINDOW_KEYS,"adapterType","processId","history_median_arrivals","history_p95_arrivals","history_support_windows"]].copy()
    index = pd.MultiIndex.from_arrays([base.case_id,pd.to_datetime(base.window_start,format="ISO8601")])
    for name, values, fill in (("execution_arrivals",arrivals,0),("process_rows_started",started,0),("completed_rows",done,0),("duration_p50_ms",p50,np.nan),("duration_p95_ms",p95,np.nan)):
        base[name] = values.reindex(index).fillna(fill).to_numpy()
    # Cross-check reconstruction against independent controls, never use it as input.
    audit = pd.read_csv(folder/"window_labels.csv",encoding="utf-8-sig")
    audit = audit.loc[audit.scope.eq("TARGET"),WINDOW_KEYS+["normal_execution_arrivals"]]
    check = base.merge(audit,on=WINDOW_KEYS,validate="one_to_one")
    if not check.execution_arrivals.eq(check.normal_execution_arrivals).all():
        raise ValueError("Counterfactual window arrival reconstruction failed")
    if return_rows:
        completed_rows = rows[["case_id","duration"]].copy()
        completed_rows["end_seconds"] = unix_seconds(rows.end)
        return base, completed_rows
    return base
