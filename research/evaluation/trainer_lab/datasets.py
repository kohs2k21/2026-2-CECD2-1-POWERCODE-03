"""Bounded offline input loading and aggregate-only Trainer dataset audits.

No records are removed by this module. Eligibility and exclusions are reported;
callers must explicitly select a training cohort. No raw values/keys are emitted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.data import (
    COUNTS, PROCESS_COLUMNS, PROCESS_PK, chronological_split, prepare,
)
from process_anomaly_pipeline.rules import evaluate_rules, model_eligible, reference_eligible

PARTITIONS = ("train", "calibration", "threshold", "test")
LINEAGE_COLUMNS = ("source_template_id", "parent_template_id", "original_template_id")
OBSERVATION_COLUMNS = ("observed_at", "observedAt", "collected_at", "collectedAt")


def load_input(path, kind="csv", *, max_rows=1_000_000, max_bytes=512 * 1024 * 1024):
    """Load canonical PROCESS CSV or original benchmark ``inputs.json`` archive.

    CSV chunks limit transient parser memory; the returned frame is in memory.
    Limits reject oversized input rather than silently selecting a subset.
    Archive parsing is bounded by file bytes and total template-row count.
    """
    path = Path(path)
    if kind not in ("csv", "archive"):
        raise ValueError("kind must be csv or archive")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (max_rows, max_bytes)):
        raise ValueError("Input limits must be positive integers")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError("Input byte limit exceeded; use a documented offline extraction")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    config = PipelineConfig()
    if kind == "csv":
        parts, rows = [], 0
        with pd.read_csv(path, dtype="string", encoding="utf-8-sig", chunksize=min(50_000, max_rows + 1)) as reader:
            for part in reader:
                rows += len(part)
                if rows > max_rows:
                    raise ValueError("Input row limit exceeded; use a documented offline extraction")
                parts.append(part)
        raw = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    else:
        bundle = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(bundle, dict) or not isinstance(bundle.get("templates"), list):
            raise ValueError("Expected a template archive")
        policy = bundle.get("policy")
        if not isinstance(policy, dict) or not isinstance(policy.get("config"), dict):
            raise ValueError("Archive must contain its original policy.config")
        config = PipelineConfig.from_dict(policy["config"])
        records = []
        for template in bundle["templates"]:
            if not isinstance(template, dict) or not isinstance(template.get("rows"), list):
                raise ValueError("Invalid template structure")
            if not template.get("template_id") or not template.get("source_period"):
                raise ValueError("Template source and lineage are required")
            if len(records) + len(template["rows"]) > max_rows:
                raise ValueError("Archive row limit exceeded; use a documented offline extraction")
            for row in template["rows"]:
                if not isinstance(row, dict):
                    raise ValueError("Invalid template record")
                records.append({**row, "source_period": template["source_period"],
                                "source_template_id": template["template_id"]})
        raw = pd.DataFrame(records)
    columns = list(map(str, raw.columns))
    frame = prepare(raw)
    frame.attrs["trainer_lab_input"] = {
        "kind": kind, "sha256": digest.hexdigest(), "bytes": size,
        "loaded_rows": len(frame), "subset_applied": False,
        "row_limit": max_rows, "byte_limit": max_bytes,
        "supplied_columns": columns,
        "source_scope_supplied": "source_period" in columns or "SOURCE_PERIOD" in columns,
    }
    frame.attrs["trainer_lab_config"] = config.to_dict()
    return frame


def _config(frame, config=None):
    if config is not None:
        return config if isinstance(config, PipelineConfig) else PipelineConfig.from_dict(config)
    return PipelineConfig.from_dict(frame.attrs.get("trainer_lab_config"))


def _times(values):
    """Match source convention: naive timestamps are Asia/Seoul wall time."""
    from process_anomaly_pipeline.data import _timestamp
    return _timestamp(values)


def _availability(frame):
    available = [c for c in OBSERVATION_COLUMNS if c in frame]
    if not available:
        return {"status": "unknown", "reason": "No observation timestamp; offline event time is not availability evidence"}
    if len(available) != 1:
        return {"status": "unknown", "reason": "Multiple observation timestamp columns require an explicit contract"}
    observed = _times(frame[available[0]])
    known = observed.notna()
    future_start = known & frame.startTime.notna() & frame.startTime.gt(observed)
    future_end = known & frame.endTime.notna() & frame.endTime.gt(observed)
    violations = int((future_start | future_end).sum())
    return {
        "status": "failed" if violations else ("checked_timestamp_consistency" if known.all() else "unknown"),
        "observation_column": available[0], "unknown_rows": int((~known).sum()),
        "start_after_observation_rows": int(future_start.sum()),
        "end_after_observation_rows": int(future_end.sum()),
        "limitation": "Timestamp consistency only; field revision/actual source visibility is not proven",
    }


def audit_dataset(frame):
    """Return JSON-safe counts/schema only, retaining invalid and duplicate rows."""
    frame = prepare(frame)
    flags, _ = evaluate_rules(frame, _config(frame))
    numeric = [c for c in COUNTS if c in frame]
    metadata = frame.attrs.get("trainer_lab_input", {})
    return {
        "rows": len(frame), "input": metadata,
        "schema": {str(c): str(t) for c, t in frame.dtypes.items()},
        "canonical_missing_counts": {c: int(frame[c].isna().sum()) for c in PROCESS_COLUMNS},
        "parse_invalid_counts": {c.removeprefix("_invalid_"): int(frame[c].sum())
                                 for c in frame if c.startswith("_invalid_")},
        "negative_counts": {c: int(frame[c].lt(0).sum()) for c in numeric},
        "noninteger_counts": {c: int((frame[c].notna() & frame[c].mod(1).ne(0)).sum()) for c in numeric},
        "negative_duration_rows": int(frame.duration_ms.lt(0).sum()),
        "duplicate_key_rows": int(frame.duplicated(list(PROCESS_PK), keep=False).sum()),
        "source_count": int(frame.source_period.nunique()),
        "observed_start_dates": int(frame.startTime.dt.normalize().nunique()),
        "start_span_seconds": None if frame.startTime.dropna().empty else
            float((frame.startTime.max() - frame.startTime.min()).total_seconds()),
        "model_eligible_rows": int(model_eligible(frame, flags).sum()),
        "successful_reference_eligible_rows": int(reference_eligible(frame, flags, exclude_statistical=False).sum()),
        "eligibility_policy": "Existing completed-event rules; statistical-tail filtering not applied; transaction context unavailable",
        "rules": {str(c): {"hits": int(flags[c].fillna(False).sum()), "unknown": int(flags[c].isna().sum())} for c in flags},
        "time_availability": _availability(frame),
        "automatic_row_removal": False,
    }


def _lineage_audit(frame, split):
    columns = [c for c in LINEAGE_COLUMNS if c in frame]
    if not columns:
        return {"status": "unknown", "reason": "No original/template lineage fields"}
    # Components connect a generated template to its parent/root even when their
    # IDs differ. Source scope prevents unrelated customers' IDs from colliding.
    parents = {}

    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    row_nodes = []
    for values in frame[["source_period", *columns]].itertuples(index=False, name=None):
        nodes = [(str(values[0]), str(v)) for v in values[1:] if pd.notna(v) and str(v).strip()]
        if nodes:
            root = find(nodes[0])
            for node in nodes[1:]:
                parents[find(node)] = root
        row_nodes.append(nodes[0] if nodes else None)
    groups = {}
    missing = 0
    for node, role in zip(row_nodes, split):
        if role not in PARTITIONS:
            continue
        if node is None:
            missing += 1
        else:
            groups.setdefault(find(node), set()).add(str(role))
    overlaps = sum(len(roles) > 1 for roles in groups.values())
    return {"status": "failed" if overlaps else ("unknown" if missing else "passed_for_supplied_lineage"),
            "columns": columns, "cross_partition_components": overlaps,
            "rows_missing_lineage": missing,
            "limitation": "Only supplied source-scoped lineage; missing or falsified provenance cannot be inferred"}


def split_and_audit(frame, config=None):
    """Reuse original chronological/group-hash split and retain held-out test.

    Calibration and threshold are disjoint transaction hashes in the validation
    period, not sequential periods. Returned frames retain all rows assigned to
    their role; callers must explicitly select eligible normal references.
    """
    frame = prepare(frame)
    config = _config(frame, config)
    split = chronological_split(frame, config)
    partitions = {name: frame.loc[split.eq(name)].reset_index(drop=True) for name in PARTITIONS}
    assigned = split.isin(PARTITIONS)
    grouped = frame.loc[assigned, ["source_period", "transactionId"]].copy()
    grouped["role"] = split.loc[assigned].to_numpy()
    overlap = int(grouped.groupby(["source_period", "transactionId"], observed=True).role.nunique().gt(1).sum())
    flags, _ = evaluate_rules(frame, config)
    eligible = reference_eligible(frame, flags, exclude_statistical=False)
    lineage = _lineage_audit(frame, split)
    report = {
        "partition_rows": {k: len(v) for k, v in partitions.items()},
        "reference_eligible_rows": {name: int((eligible & split.eq(name)).sum()) for name in PARTITIONS},
        "excluded_rows": {str(k): int(v) for k, v in split.loc[~assigned].value_counts().items()},
        "transaction_overlap_count": overlap,
        "transaction_separation": "passed" if overlap == 0 else "failed",
        "lineage": lineage, "time_availability": _availability(frame),
        "source_scope": "explicit" if frame.attrs.get("trainer_lab_input", {}).get("source_scope_supplied") else "unverified",
        "fit_role_protocol": {"train": "feature medians/scaler/model fit",
                              "calibration": "score normalization fit",
                              "threshold": "decision threshold fit",
                              "test": "held-out evaluation only"},
        "fit_execution_verified": False,
        "split_config": {"train_fraction": config.train_fraction,
                         "validation_fraction": config.validation_fraction},
        "empty_partitions": [name for name, part in partitions.items() if part.empty],
        "limitations": ["Protocol audit does not prove a model actually used these memberships",
                        "Invalid/duplicate rows retained; eligibility selection required before fitting",
                        "Cross-boundary/missing-time transactions excluded explicitly by original splitter"],
    }
    return partitions, report
