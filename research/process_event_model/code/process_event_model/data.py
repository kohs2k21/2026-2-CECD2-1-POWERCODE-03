"""Original, date-preserving fitting data and read-only benchmark row labels."""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .features import normalize

ROOT = Path(__file__).resolve().parents[2]
TASKS = ("individual_delay", "group_delay", "time_burst", "input_stop", "gradual_degradation")
KEYS = ["transactionId", "processId", "retryCount"]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def original(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cache = output/"original_rows.joblib"
    if cache.exists():
        print("Loading unchanged original Process rows", flush=True)
        return joblib.load(cache)
    from process_anomaly_pipeline.data import load_workspace, chronological_split
    from process_anomaly_pipeline.config import PipelineConfig
    raw, _, sources = load_workspace(ROOT/"data")
    print(f"Read {len(raw):,} original rows", flush=True)
    cols = ["source_period", "transactionId", "processId", "adapterType", "startTime", "endTime",
            "totalCount", "successCount", "errorCount", "retryCount", "status", "dependProcessId",
            "processHubId", "channelId", "responseCode"]
    # Context may include failed completions, but never uncompleted/invalid rows.
    valid = (raw.startTime.notna() & raw.endTime.notna() & raw.duration_ms.ge(0) &
             raw.totalCount.notna() & raw.totalCount.ge(0) & raw.totalCount.mod(1).eq(0) &
             raw.transactionId.notna() & raw.processId.notna() & raw.adapterType.notna())
    split = chronological_split(raw, PipelineConfig())
    f = raw.loc[valid, cols].copy()
    f["split"] = split.loc[valid].to_numpy()
    # Uncollected calendar gaps are not fabricated into zero-count minutes.
    f = normalize(f)
    archive = json.loads((ROOT/"reports/evaluation_v2_work/inputs.json").read_text(encoding="utf-8"))
    source_keys = {(t["source_period"], str(t["source_transactionId"]))
                   for t in archive["templates"]}
    f["benchmark_source"] = [(str(p), str(tx)) in source_keys
                              for p, tx in zip(f.source_period, f.transactionId)]
    clean = (f.status.eq("S") & f.errorCount.eq(0) &
             f.totalCount.eq(f.successCount+f.errorCount) &
             ~f.benchmark_source)
    f["normal_fit_eligible"] = clean
    audit = dict(rows_read=len(raw), completed_valid_rows=len(f), original_times_preserved=True,
                 synthetic_fit_rows=0, synthetic_traffic_history_used=False,
                 benchmark_source_excluded_from_fit=True,
                 availability="transaction max endTime proxy, unless availableTime supplied",
                 sources=sources.to_dict("records"), splits={})
    for name, g in f.groupby("split", observed=True):
        normal = g.loc[g.normal_fit_eligible]
        audit["splits"][str(name)] = dict(rows=len(g), normal_candidates=len(normal),
            first=str(g.startTime.min()), last=str(g.startTime.max()), adapters=int(normal.adapterType.nunique()))
    joblib.dump(f, cache, compress=3)
    write_json(output/"original_audit.json", audit)
    print(json.dumps(audit["splits"], ensure_ascii=False), flush=True)
    return f


def benchmark(task, limit=None, offset=0):
    folder = ROOT/"data/evaluation"/task
    labels = pd.read_csv(folder/"process_labels.csv", encoding="utf-8-sig")
    cases = pd.read_csv(folder/"cases.csv", encoding="utf-8-sig")
    cases = cases.iloc[offset:offset+limit if limit else None]
    labels = labels.loc[labels.case_id.isin(cases.case_id)]
    out = {}
    for cohort, filename in (("observed", "processes.csv"), ("normal", "normal_processes.csv")):
        rows = pd.read_csv(folder/filename, encoding="utf-8-sig")
        if cohort == "observed":
            extra = labels[KEYS+["case_id", "row_is_anomaly", "collective_context_is_anomaly"]]
            f = rows.merge(extra, on=KEYS, validate="one_to_one", how="inner")
        else:
            # Removed transactions have no observed Process label. Recover ONLY
            # case mapping from the normal Transaction context, never fault truth.
            mapping = labels[["transactionId", "case_id"]].drop_duplicates()
            if task == "input_stop":
                events = pd.read_csv(folder/"events.csv", encoding="utf-8-sig")
                missing = [{"transactionId": tx, "case_id": e.case_id}
                           for e in events.itertuples()
                           for tx in json.loads(e.evidence_json)["missing_transaction_ids"]]
                mapping = pd.concat([mapping, pd.DataFrame(missing)], ignore_index=True).drop_duplicates()
            f = rows.merge(mapping, on="transactionId", how="inner", validate="many_to_one")
            f = f.loc[f.case_id.isin(cases.case_id)].copy()
            f["row_is_anomaly"] = 0
            f["collective_context_is_anomaly"] = 0
        # Only identity/case metadata may reach the feature processor.
        f = normalize(f)
        f["label"] = (f.row_is_anomaly.astype(bool) | f.collective_context_is_anomaly.astype(bool)).astype(int)
        out[cohort] = f
    return out, cases


def sample_ids(f, cap, seed=20261030):
    """Deterministic, adapter-balanced sample of ACTUAL rows (no duplicates)."""
    if len(f) <= cap:
        return f.index.to_numpy()
    rng = np.random.default_rng(seed)
    buckets = [g.index.to_numpy() for _, g in f.groupby("adapterType", observed=True)]
    selected = []
    # Coverage allocation followed by a proportional remaining allocation.
    quota = max(1, cap//(2*len(buckets)))
    for ids in buckets:
        selected.extend(rng.choice(ids, min(len(ids), quota), replace=False))
    remaining = f.index.difference(selected).to_numpy()
    selected.extend(rng.choice(remaining, cap-len(selected), replace=False))
    return np.asarray(sorted(selected))


if __name__ == "__main__":
    original(ROOT/"reports/process_event_model")
