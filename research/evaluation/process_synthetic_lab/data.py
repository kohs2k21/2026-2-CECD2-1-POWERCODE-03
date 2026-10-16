"""Canonical offline input, parent bounds and source-scoped row identities."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from research.evaluation.trainer_lab.datasets import load_input, _lineage_audit, _availability
from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.data import JOIN_KEYS, PROCESS_PK, prepare
from process_anomaly_pipeline.rules import evaluate_rules, reference_eligible


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_hash(frame):
    """Stable within the recorded pandas environment; includes column order."""
    return hashlib.sha256(frame.to_csv(index=False, lineterminator="\n").encode()).hexdigest()


def row_ids(frame):
    return frame[list(PROCESS_PK)].apply(
        lambda row: hashlib.sha256(json.dumps([None if pd.isna(v) else str(v) for v in row],
                                              ensure_ascii=False).encode()).hexdigest(), axis=1)


def reference_mask(frame):
    flags, _ = evaluate_rules(frame, PipelineConfig())
    return reference_eligible(frame, flags, exclude_statistical=False).fillna(False)


def split_frames(frame, config):
    """Timestamp quantiles chosen before scoring; whole transactions stay together.

    Exclude transactions whose event interval crosses either time boundary.
    This is intentionally distinct from trainer_lab's observed-day split: heavily
    skewed dates otherwise leave almost no test cohort. No model result is used.
    """
    known = frame.startTime.dropna().sort_values()
    if len(known) < 5 or known.nunique() < 3:
        raise ValueError("Insufficient chronological coverage")
    cut_train = known.iloc[max(0, int(len(known) * config.train_fraction) - 1)]
    cut_val = known.iloc[max(1, int(len(known) * (config.train_fraction + config.validation_fraction)) - 1)]
    if cut_train >= cut_val:
        raise ValueError("Time quantile boundaries collapse; select a broader source")
    groups = frame.groupby(["source_period", "transactionId"], observed=True)
    first = groups.startTime.transform("min")
    last_start = groups.startTime.transform("max")
    last_end = groups.endTime.transform("max")
    last = pd.concat([last_start, last_end], axis=1).max(axis=1)
    valid = frame.transactionId.notna() & frame.startTime.notna() & first.notna()
    crossed = ((first.le(cut_train) & last.gt(cut_train)) | (first.le(cut_val) & last.gt(cut_val)))
    split = pd.Series("excluded_missing_time_or_identity", index=frame.index, dtype="string")
    split.loc[valid & crossed] = "excluded_boundary_transaction"
    split.loc[valid & ~crossed & last.le(cut_train)] = "train"
    split.loc[valid & ~crossed & first.gt(cut_train) & last.le(cut_val)] = "threshold"
    split.loc[valid & ~crossed & first.gt(cut_val)] = "test"
    parts = {role: frame.loc[split.eq(role)].reset_index(drop=True) for role in ("train", "threshold", "test")}
    assigned = split.isin(parts)
    identities = frame.loc[assigned, ["source_period", "transactionId"]].copy()
    identities["role"] = split.loc[assigned]
    overlaps = int(identities.groupby(["source_period", "transactionId"]).role.nunique().gt(1).sum())
    report = {"method": "pre_registered_row_timestamp_quantiles_transaction_interval_purge",
              "train_fraction": config.train_fraction, "validation_fraction": config.validation_fraction,
              "train_end": str(cut_train), "validation_end": str(cut_val),
              "transaction_overlap_count": overlaps,
              "lineage": _lineage_audit(frame, split), "time_availability": _availability(frame),
              "excluded": split.loc[~assigned].value_counts().astype(int).to_dict(),
              "fit_roles": {"train": "feature statistics, scaler, IF and existing SR quantiles",
                            "threshold": "IF decision threshold only", "test": "generation/scoring only"}}
    return parts, report


def load_dataset(process_path, transaction_path, config):
    frame = load_input(process_path)
    metadata = dict(frame.attrs)
    # Bounded parent CSV loading; preserve original values and never write inputs.
    if Path(transaction_path).stat().st_size > 512 * 1024 * 1024:
        raise ValueError("Transaction CSV exceeds 512 MiB")
    raw = pd.read_csv(transaction_path, dtype="string", encoding="utf-8-sig", nrows=1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Transaction CSV exceeds row limit")
    parents = prepare(raw, "transaction")
    valid = parents[list(JOIN_KEYS)].notna().all(axis=1) & ~parents.duplicated(list(JOIN_KEYS), keep=False)
    renamed = parents.loc[valid, [*JOIN_KEYS, "startTime", "endTime", "interfaceId"]].rename(
        columns={"startTime": "parentStartTime", "endTime": "parentEndTime", "interfaceId": "parentInterfaceId"})
    # Caller-provided parent metadata must never override the actual parent join.
    for name in ("parentStartTime", "parentEndTime", "parentInterfaceId", "row_id"):
        if name in frame:
            raise ValueError(f"Reserved input column: {name}")
    frame = frame.merge(renamed, how="left", on=list(JOIN_KEYS), validate="many_to_one", sort=False)
    frame.attrs = metadata
    frame["row_id"] = row_ids(frame)
    if frame.row_id.duplicated().any():
        raise ValueError("Duplicate PROCESS identity: resolve extraction before experiment")
    parts, audit = split_frames(frame, config)
    if audit["transaction_overlap_count"] or audit["lineage"]["status"] == "failed" or audit["time_availability"]["status"] == "failed":
        raise ValueError("Known transaction/lineage/observation leakage violation")
    audit["parent_join"] = {"rows": len(parents), "invalid_or_ambiguous_rows": int((~valid).sum()),
                            "process_rows_without_parent_bounds": int(frame[["parentStartTime", "parentEndTime"]].isna().any(axis=1).sum())}
    audit["reference_kind"] = "successful_quality_consistent_proxy_not_verified_normal"
    audit["partitions"] = {
        name: {"rows": len(part), "sha256": frame_hash(part),
               "reference_rows": int(reference_mask(part).sum()),
               "adapter_counts": part.adapterType.value_counts(dropna=False).astype(int).to_dict(),
               "interface_counts": part.parentInterfaceId.value_counts(dropna=False).astype(int).to_dict()}
        for name, part in parts.items()
    }
    return frame, parts, audit
