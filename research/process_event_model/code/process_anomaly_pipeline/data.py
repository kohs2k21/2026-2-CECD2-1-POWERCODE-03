"""Backend record normalization and optional adapters for existing CSV exports."""
from pathlib import Path
import re
import sys
import numpy as np
import pandas as pd

PROCESS_COLUMNS = ("transactionId", "processId", "dependProcessId", "processHubId",
                   "adapterType", "channelId", "status", "startTime", "endTime",
                   "successCount", "errorCount", "responseCode", "totalCount", "retryCount")
TRANSACTION_COLUMNS = ("transactionId", "processHubId", "retryCount", "interfaceId",
                       "status", "processCount", "startTime", "endTime")
PROCESS_REQUIRED = ("transactionId", "processId", "processHubId", "retryCount")
PROCESS_PK = ("source_period", "transactionId", "processId", "retryCount")
JOIN_KEYS = ("source_period", "transactionId", "processHubId", "retryCount")
COUNTS = ("totalCount", "successCount", "errorCount", "retryCount", "processCount")
FINAL_STATUSES = ("S", "F", "H", "Z")
ALLOWED_STATUSES = ("S", "F", "P", "N", "H", "Z")
_ALIASES = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in
            (*PROCESS_COLUMNS, *TRANSACTION_COLUMNS, "source_period", "source_file")}
_NULL_WORDS = ("", "null", "none", "nan", "\\n")


def _timestamp(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        if getattr(series.dt, "tz", None) is not None:
            return series.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        return series
    s = series.astype("string")
    aware = s.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False)
    out = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    out.loc[~aware] = pd.to_datetime(s.loc[~aware], format="mixed", errors="coerce")
    out.loc[aware] = pd.to_datetime(s.loc[aware], format="mixed", errors="coerce", utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    return out


def prepare(records, kind="process"):
    if kind not in ("process", "transaction"):
        raise ValueError("kind must be process or transaction")
    if isinstance(records, pd.DataFrame) and records.attrs.get("esb_prepared") == kind:
        return records
    if isinstance(records, dict):
        records = [records]
    frame = pd.DataFrame(records).copy().reset_index(drop=True)
    frame.columns = [_ALIASES.get(re.sub(r"[^a-z0-9]", "", str(c).lower()), c) for c in frame.columns]
    if frame.columns.duplicated().any():
        raise ValueError("Ambiguous duplicate/alias columns")
    columns = PROCESS_COLUMNS if kind == "process" else TRANSACTION_COLUMNS
    for c in columns:
        if c not in frame:
            frame[c] = pd.NA
    if "source_period" not in frame:
        frame["source_period"] = "runtime"
    else:
        frame["source_period"] = frame.source_period.astype("string").fillna("runtime")
    parse_columns = list(columns) + [c for c in COUNTS if c in frame and c not in columns]
    for c in parse_columns:
        if c in ("startTime", "endTime"):
            raw = frame[c].astype("string")
            absent = raw.isna() | raw.str.strip().str.lower().isin(_NULL_WORDS)
            parsed = _timestamp(frame[c].where(~absent))
            frame[f"_invalid_{c}"] = (~absent & parsed.isna()).to_numpy()
            frame[c] = parsed
        elif c in COUNTS:
            raw = frame[c]
            text = raw.astype("string").str.strip()
            absent = text.isna() | text.str.lower().isin(_NULL_WORDS)
            value = pd.to_numeric(raw.where(~absent), errors="coerce").astype("float64")
            invalid = ~absent & (~np.isfinite(value) | raw.map(lambda v: isinstance(v, (bool, np.bool_))))
            frame[f"_invalid_{c}"] = invalid.fillna(False).to_numpy(dtype=bool)
            frame[c] = value.where(np.isfinite(value))
        else:
            s = frame[c].astype("string").str.strip()
            frame[c] = s.mask(s.str.lower().isin(_NULL_WORDS))
    frame["duration_ms"] = (frame.endTime - frame.startTime).dt.total_seconds() * 1000
    frame.attrs["esb_prepared"] = kind
    return frame


def chronological_split(frame, config):
    # Keep all Process events of a transaction in one partition, including retries.
    group = frame.groupby(["source_period", "transactionId"], observed=True)
    first = group.startTime.transform("min").dt.normalize()
    last = group.startTime.transform("max").dt.normalize()
    days = pd.Index(first.dropna().unique()).sort_values()
    if len(days) < 5:
        raise ValueError("At least five observed dates are required for temporal evaluation")
    train_end = days[max(0, int(len(days) * config.train_fraction) - 1)]
    val_end = days[max(1, int(len(days) * (config.train_fraction + config.validation_fraction)) - 1)]
    crossed = ((first.le(train_end) & last.gt(train_end)) | (first.le(val_end) & last.gt(val_end)))
    valid = first.notna() & frame.startTime.notna() & frame.transactionId.notna()
    split = pd.Series("excluded_missing_time_or_id", index=frame.index, dtype="string")
    split.loc[valid & crossed] = "excluded_boundary_transaction"
    split.loc[valid & ~crossed & last.le(train_end)] = "train"
    validation = valid & ~crossed & first.gt(train_end) & last.le(val_end)
    # Separate score normalization and threshold fitting WITHOUT splitting a transaction.
    hashes = pd.util.hash_pandas_object(frame[["source_period", "transactionId"]], index=False).to_numpy()
    split.loc[validation & (hashes % 2 == 0)] = "calibration"
    split.loc[validation & (hashes % 2 == 1)] = "threshold"
    split.loc[valid & ~crossed & first.gt(val_end)] = "test"
    return split


def load_workspace(data_dir, periods=None, *, with_transactions=False):
    """Offline convenience only; core inference never imports notebook helpers.

    Reuses content-aware source discovery (recent filenames are swapped). Reads
    raw chunks to retain invalid-value diagnostics. Never runs data/ESB.SQL.
    """
    code_dir = Path(__file__).resolve().parent.parent
    helper_dir = next((p for p in (code_dir / "analysis", code_dir)
                       if (p / "log_analysis_common.py").is_file()), None)
    if helper_dir is None:
        raise FileNotFoundError("Offline source loader requires code/analysis/log_analysis_common.py; "
                                "backend record inference does not require this file")
    if str(helper_dir) not in sys.path:
        sys.path.insert(0, str(helper_dir))
    import log_analysis_common as legacy
    periods = tuple(periods or legacy.PERIODS)
    results, audit = {}, []
    kinds = ("process", "transaction") if with_transactions else ("process",)
    for kind in kinds:
        parts = []
        for period in periods:
            path, info = legacy.discover_source(Path(data_dir) / period, kind)
            rows = 0
            for raw in legacy._read_source(path, info, kind, 200_000):
                raw = raw.rename(columns=legacy.canonical_column)
                raw["source_period"] = period
                part = prepare(raw, kind)
                rows += len(part)
                parts.append(part)
            audit.append({"kind": kind, "period": period, "source": str(path), "rows": rows, "format": info["format"]})
        frame = pd.concat(parts, ignore_index=True)
        # Do not silently discard conflicting or exact duplicates: rules audit them.
        for c in ("source_period", "status", "adapterType", "processHubId", "channelId", "dependProcessId"):
            if c in frame:
                frame[c] = frame[c].astype("category")
        frame.attrs["esb_prepared"] = kind
        results[kind] = frame
    return results["process"], results.get("transaction"), pd.DataFrame(audit)
