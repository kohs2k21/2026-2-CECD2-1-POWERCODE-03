"""Read-only source sampling and frozen-policy export for the v2 benchmark."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "code"), str(ROOT / "code" / "analysis")]
from log_analysis_common import PERIODS, canonical_column, discover_source, _read_source
from process_anomaly_pipeline.data import PROCESS_COLUMNS, TRANSACTION_COLUMNS, prepare
from process_anomaly_pipeline.pipeline import ProcessAnomalyPipeline
from process_anomaly_pipeline.rules import evaluate_rules

MODEL = ROOT / "code/process_anomaly_pipeline/artifacts/process_if_v1.joblib"


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def scalar(v):
    if pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if isinstance(v, np.generic):
        return v.item()
    return v


def read_chunks(path, info=None, kind="process"):
    if info and info["format"] == "sql":
        # Lossless SQLite export, read only in an isolated in-memory database.
        # The existing reader denies ATTACH/PRAGMA; data/ESB.SQL is never run.
        for chunk in _read_source(path, info, kind, 200_000):
            yield chunk.rename(columns=canonical_column)
        return
    for chunk in pd.read_csv(path, dtype="string", encoding="utf-8-sig", chunksize=200_000):
        yield chunk.rename(columns=canonical_column)


def topo(rows):
    ids = {r["processId"] for r in rows}
    done, ordered = set(), []
    while len(ordered) < len(rows):
        pending = [r for r in rows if r["processId"] not in done and
                   (r["dependProcessId"] is None or r["dependProcessId"] in done)]
        if not pending:
            return None
        for r in sorted(pending, key=lambda x: x["processId"]):
            if r["dependProcessId"] is not None and r["dependProcessId"] not in ids:
                return None
            ordered.append(r)
            done.add(r["processId"])
    return ordered


def collect(cap=2000):
    model = ProcessAnomalyPipeline.load(MODEL, trusted=True)
    policy = {
        "artifact_sha256": sha256(MODEL), "artifact_path": str(MODEL.relative_to(ROOT)),
        "config": asdict(model.config), "thresholds": model.quantile_book.table().to_dict("records"),
        "expert_adapters": sorted(model.adapter_models),
        "semantics": "adapter threshold when available, else GLOBAL, separately for each metric/quantile; strict >",
    }
    templates, audit, coverage, reasons = [], [], Counter(), Counter()
    for period in PERIODS:
        source, info = discover_source(ROOT / "data" / period, "process")
        parent_source, parent_info = discover_source(ROOT / "data" / period, "transaction")
        if info["format"] != "csv":
            raise ValueError("Process prototypes must be CSV; ESB.SQL is never executed")
        candidates, seen, scanned = [], set(), 0
        for raw in read_chunks(source):
            scanned += len(raw)
            seen.update(raw.adapterType.dropna().unique())
            mask = raw.status.eq("S") & pd.to_numeric(raw.errorCount, errors="coerce").eq(0) & pd.to_numeric(raw.retryCount, errors="coerce").eq(0)
            c = raw.loc[mask, ["transactionId", "adapterType", "totalCount"]].copy()
            c["priority"] = pd.util.hash_pandas_object(c.transactionId + "|20261010|" + period, index=False).to_numpy()
            candidates.append(c.sort_values("priority").groupby(["adapterType", "totalCount"], dropna=False).head(cap))
        c = pd.concat(candidates, ignore_index=True).sort_values("priority").groupby(["adapterType", "totalCount"], dropna=False).head(cap)
        selected = set(c.transactionId.dropna())
        pieces, offset = [], 0
        for raw in read_chunks(source):
            raw["source_row"] = np.arange(offset + 1, offset + len(raw) + 1)
            offset += len(raw)
            pieces.append(raw.loc[raw.transactionId.isin(selected)].copy())
        p = prepare(pd.concat(pieces, ignore_index=True).assign(source_period=period))
        tparts = [c.loc[c.transactionId.isin(selected)].copy() for c in read_chunks(parent_source,parent_info,"transaction")]
        t = prepare(pd.concat(tparts, ignore_index=True).assign(source_period=period), "transaction")
        flags, _ = evaluate_rules(p, model.config, transactions=t, complete_context=True)
        flags["statistical_tail"], _ = model.quantile_book.evaluate(p)
        valid = ~flags.fillna(True).any(axis=1) & p.status.eq("S") & p.retryCount.eq(0)
        valid &= p[["totalCount", "successCount", "errorCount", "retryCount"]].notna().all(axis=1)
        valid &= p.adapterType.notna() & p.duration_ms.ge(0)
        for col, length in {"processId":36, "dependProcessId":36, "processHubId":20,
                            "adapterType":30, "channelId":20, "responseCode":10}.items():
            valid &= p[col].isna() | p[col].str.len().le(length)
        p["_usable"] = valid
        parent_groups = {key: g for key, g in t.groupby("transactionId", sort=False)}
        accepted = 0
        for tx, group in p.groupby("transactionId", sort=True):
            if not group._usable.all():
                reasons["incomplete_or_rule_incompatible_workflow"] += 1
                continue
            parent = parent_groups.get(tx)
            if parent is None or not parent.status.eq("S").all() or not parent.retryCount.eq(0).all():
                reasons["missing_or_non_success_parent"] += 1
                continue
            if parent.duplicated(["processHubId", "retryCount"]).any() or parent.interfaceId.str.len().gt(30).any():
                reasons["parent_key_or_schema"] += 1
                continue
            rows = []
            for _, row in group.iterrows():
                item = {col: scalar(row[col]) for col in PROCESS_COLUMNS}
                for col in ("totalCount", "successCount", "errorCount", "retryCount"):
                    item[col] = int(item[col])
                item.update(duration_ms=float(row.duration_ms), source_row=int(row.source_row))
                rows.append(item)
            ordered = topo(rows)
            if ordered is None:
                reasons["external_or_cyclic_dependency"] += 1
                continue
            template = {
                "template_id": f"T{len(templates):06d}", "source_period":period,
                "source_file":str(source.relative_to(ROOT)).replace("\\", "/"),
                "source_transactionId": str(tx), "rows":ordered,
                "parents":[{col:scalar(row[col]) for col in TRANSACTION_COLUMNS}
                           for _, row in parent.iterrows()],
            }
            templates.append(template)
            accepted += 1
            for row in ordered:
                coverage[(period, row["adapterType"])] += 1
        entry = {"period":period, "process_file":str(source.relative_to(ROOT)).replace("\\", "/"),
                 "transaction_file":str(parent_source.relative_to(ROOT)).replace("\\", "/"),
                 "scanned_process_rows":scanned, "sampled_transaction_ids":len(selected),
                 "accepted_complete_templates":accepted, "source_adapters":sorted(seen),
                 "process_sha256":sha256(source), "transaction_sha256":sha256(parent_source),"transaction_format":parent_info["format"]}
        audit.append(entry)
        print(f"{period}: scanned {scanned:,}; complete usable templates {accepted:,}", flush=True)
    if not templates:
        raise ValueError("No complete successful templates pass the frozen policy")
    untouched = {str(MODEL.relative_to(ROOT)):policy["artifact_sha256"], "data/ESB.SQL":sha256(ROOT / "data/ESB.SQL")}
    for p in (ROOT / "code").rglob("*.ipynb"):
        untouched[str(p.relative_to(ROOT)).replace("\\", "/")] = sha256(p)
    return {"templates":templates, "policy":policy, "source_audit":audit,
            "eligible_adapter_coverage":[{"period":p,"adapterType":a,"prototype_rows":n}
                                         for (p,a),n in sorted(coverage.items())],
            "excluded_workflow_reasons":dict(reasons), "untouched_hashes":untouched,
            "sampling":"stable transaction hash reservoir per period/adapter/count, then retrieve ALL Process rows and original parent Transaction context; not frequency representative"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cap", type=int, default=2000)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bundle = collect(args.cap)
    args.output.write_text(json.dumps(bundle, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(f"Prepared {len(bundle['templates']):,} templates; {len({r['adapterType'] for t in bundle['templates'] for r in t['rows']})} Adapters", flush=True)


if __name__ == "__main__":
    main()
