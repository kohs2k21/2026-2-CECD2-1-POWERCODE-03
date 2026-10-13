"""Three-period log loading and reusable anomaly-detection EDA helpers.

No analysis results are written to disk. Naive source timestamps are interpreted
as Asia/Seoul; all timestamps exposed to notebooks are local, timezone-naive KST.
"""

from pathlib import Path
import csv
import re
import sqlite3

import numpy as np
import pandas as pd


PERIODS = (
    "25년12월24일-26년4월6일",
    "26년8월15일-26년9월14일",
    "26년9월30일-26년10월2일",
)
SCHEMAS = {
    "transaction": [
        "transactionId", "interfaceId", "processHubId", "startChannelId",
        "endChannelId", "processCount", "status", "responseCode",
        "interfaceType", "categoryName", "startTime", "endTime",
        "processTime", "retryCount",
    ],
    "process": [
        "transactionId", "processId", "dependProcessId", "processHubId",
        "adapterType", "channelId", "status", "startTime", "endTime",
        "successCount", "errorCount", "responseCode", "totalCount", "retryCount",
    ],
}
REQUIRED = {
    "transaction": {"transactionId", "interfaceId", "processCount", "processTime"},
    "process": {"transactionId", "processId", "dependProcessId", "adapterType", "totalCount"},
}
NUMERIC = {"processCount", "processTime", "retryCount", "successCount", "errorCount", "totalCount"}
CATEGORIES = {
    "source_period", "source_file", "interfaceId", "processId", "dependProcessId",
    "processHubId", "startChannelId", "endChannelId", "adapterType", "channelId",
    "status", "responseCode", "interfaceType", "categoryName",
}
_ALIASES = {
    re.sub(r"[^a-z0-9]", "", col.lower()): col
    for schema in SCHEMAS.values() for col in schema
}


def canonical_column(name):
    return _ALIASES.get(re.sub(r"[^a-z0-9]", "", name.lower()), name)


def data_directory():
    return Path(__file__).resolve().parents[2] / "data"


def inspect_source(path):
    """Inspect content, not the extension: some exports have swapped names."""
    with Path(path).open(encoding="utf-8-sig") as handle:
        first = handle.readline()
        if first.lstrip().startswith("--"):
            prefix = first + handle.read(8192)
            match = re.search(r'CREATE TABLE\s+"?(\w+)"?\s*\(([^;]+)\);', prefix)
            if match is None:
                return None
            columns = re.findall(r'"([A-Z_]+)"\s+TEXT', match[2])
            return {"format": "sql", "table": match[1], "columns": columns}
        columns = next(csv.reader([first]), [])
        if "transactionId" not in {canonical_column(c) for c in columns}:
            return None
        return {"format": "csv", "table": None, "columns": columns}


def discover_source(folder, kind):
    matches = []
    for path in sorted(Path(folder).iterdir()):
        if path.suffix.lower() not in {".csv", ".sql"}:
            continue
        info = inspect_source(path)
        if info and REQUIRED[kind].issubset({canonical_column(c) for c in info["columns"]}):
            matches.append((path, info))
    if not matches:
        raise FileNotFoundError(f"{folder}: {kind} 스키마에 맞는 로그가 없습니다.")
    # Prefer a .csv export. When both .csv and .sql represent the same table,
    # select one source only, even when its extension does not match its format.
    matches.sort(key=lambda item: (item[0].suffix.lower() != ".csv", item[1]["format"] != "csv", item[0].name))
    return matches[0]


def parse_kst(series):
    values = series.astype("string")
    aware = values.str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", na=False)
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    result.loc[~aware] = pd.to_datetime(values.loc[~aware], format="mixed", errors="coerce")
    result.loc[aware] = (
        pd.to_datetime(values.loc[aware], format="mixed", errors="coerce", utc=True)
        .dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    )
    return result


def _read_source(path, info, kind, chunksize):
    columns = [c for c in info["columns"] if canonical_column(c) in SCHEMAS[kind]]
    if info["format"] == "csv":
        chunks = pd.read_csv(
            path, encoding="utf-8-sig", dtype="string", usecols=columns,
            na_values=["\\N", "", "NaN", "nan", "NULL"], keep_default_na=False,
            chunksize=chunksize,
        )
        for chunk in chunks:
            yield chunk
        return
    # SQL exports are imported into an isolated in-memory database. Deny file
    # attachment and PRAGMA so an export cannot write to another database.
    with sqlite3.connect(":memory:") as connection:
        def authorize(action, *_):
            return sqlite3.SQLITE_DENY if action in {
                sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
            } else sqlite3.SQLITE_OK
        connection.set_authorizer(authorize)
        connection.executescript(Path(path).read_text(encoding="utf-8-sig"))
        selected = ",".join(f'"{c}"' for c in columns)
        for chunk in pd.read_sql_query(f'SELECT {selected} FROM "{info["table"]}"', connection, chunksize=chunksize):
            yield chunk.astype("string").replace({"\\N": pd.NA, "": pd.NA})


def load_logs(kind, data_dir=None, periods=PERIODS, chunksize=200_000):
    """Return all periods and a provenance/quality audit, without silent skips."""
    if kind not in SCHEMAS:
        raise ValueError(f"지원하지 않는 로그 단위: {kind}")
    root = Path(data_dir) if data_dir is not None else data_directory()
    frames, audits = [], []
    for period in periods:
        path, info = discover_source(root / period, kind)
        parts, invalid_times, invalid_numbers = [], 0, 0
        for part in _read_source(path, info, kind, chunksize):
            part = part.rename(columns=canonical_column).reindex(columns=SCHEMAS[kind])
            for col in ("startTime", "endTime"):
                parsed = parse_kst(part[col])
                invalid_times += int((part[col].notna() & parsed.isna()).sum())
                part[col] = parsed
            for col in NUMERIC.intersection(part.columns):
                parsed = pd.to_numeric(part[col], errors="coerce").astype("float64")
                invalid_numbers += int((part[col].notna() & parsed.isna()).sum())
                part[col] = parsed
            parts.append(part)
        frame = pd.concat(parts, ignore_index=True)
        raw_rows = len(frame)
        # Remove exact duplicate log records, not conflicting/retried events.
        frame = frame.drop_duplicates().reset_index(drop=True)
        frame["source_period"] = period
        frame["source_file"] = str(path.relative_to(root))
        keys = ["transactionId", "retryCount"] + (["processId"] if kind == "process" else [])
        audits.append({
            "source_period": period, "source_file": str(path.relative_to(root)),
            "format": info["format"], "raw_rows": raw_rows, "rows": len(frame),
            "exact_duplicates_removed": raw_rows - len(frame),
            "duplicate_attempt_keys": int(frame.duplicated(keys).sum()),
            "missing_transaction_id": int(frame["transactionId"].isna().sum()),
            "invalid_timestamp_values": invalid_times,
            "invalid_numeric_values": invalid_numbers,
            "first_start": frame["startTime"].min(), "last_start": frame["startTime"].max(),
            "last_end": frame["endTime"].max(),
            "observed_days": frame["startTime"].dt.normalize().nunique(),
            "file_MB": round(path.stat().st_size / 1_000_000, 2),
        })
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    for col in CATEGORIES.intersection(result.columns):
        result[col] = result[col].astype("category")
    return result, pd.DataFrame(audits)


def text(series):
    return series.astype("string").fillna("MISSING")


def quality_table(frame):
    return pd.DataFrame({
        "dtype": frame.dtypes.astype(str), "missing_count": frame.isna().sum(),
        "missing_pct": frame.isna().mean().mul(100), "unique_values": frame.nunique(),
    }).round(3)


def numeric_summary(frame, columns):
    result = frame[columns].describe(percentiles=[.01, .25, .5, .75, .95, .99, .999]).T
    result["missing_pct"] = frame[columns].isna().mean().mul(100)
    result["zero_pct"] = frame[columns].eq(0).sum().div(frame[columns].count()).mul(100)
    result["negative_count"] = frame[columns].lt(0).sum()
    result["skewness"] = frame[columns].skew()
    return result.round(3)


def add_time_features(frame):
    frame["duration_ms"] = (frame["endTime"] - frame["startTime"]).dt.total_seconds() * 1000
    frame["hour"] = frame["startTime"].dt.hour
    frame["dayofweek"] = frame["startTime"].dt.dayofweek
    frame["negative_duration"] = frame["duration_ms"].lt(0)
    frame["missing_end"] = frame["endTime"].isna()
    return frame


def sample_by_period(frame, n=30_000, random_state=42):
    """Bound rendering costs and ensure that the short period remains visible."""
    groups = list(frame.groupby("source_period", observed=True, sort=False))
    per_period = max(1, n // max(len(groups), 1))
    return pd.concat([g.sample(min(len(g), per_period), random_state=random_state) for _, g in groups])


def time_summary(frame, freq="D"):
    valid = frame.loc[frame["startTime"].notna(), ["source_period", "startTime", "duration_ms", "status"]].copy()
    valid["bin"] = valid["startTime"].dt.floor(freq)
    valid["failure"] = valid["status"].eq("F")
    valid["known_status"] = valid["status"].notna()
    grouped = valid.groupby(["source_period", "bin"], observed=True)
    summary = grouped.agg(
        rows=("status", "size"), known_status=("known_status", "sum"), failures=("failure", "sum"),
        median_ms=("duration_ms", "median"), p95_ms=("duration_ms", lambda s: s.quantile(.95)),
    )
    summary["failure_pct"] = summary["failures"].div(summary["known_status"].replace(0, np.nan)).mul(100)
    return summary.reset_index()


def hourly_history_candidates(hourly):
    """Past-only rolling references; missing bins are unknown, never zero-filled."""
    parts = []
    for period, group in hourly.groupby("source_period", observed=True):
        h = group.set_index("bin").drop(columns="source_period").sort_index().asfreq("h")
        values = np.log1p(h["rows"])
        history = values.shift(1).rolling(24, min_periods=6)
        center = history.median()
        mad = history.apply(lambda x: np.nanmedian(np.abs(x - np.nanmedian(x))), raw=True)
        h["volume_robust_z"] = .6745 * (values - center) / mad.replace(0, np.nan)
        h["reference_available"] = mad.gt(0) & values.notna()
        h["candidate"] = h["reference_available"] & h["volume_robust_z"].abs().gt(3.5)
        h["source_period"] = period
        parts.append(h.reset_index())
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def iqr_summary(frame, columns):
    records, flags = [], pd.DataFrame(index=frame.index)
    for col in columns:
        q1, q3 = frame[col].quantile([.25, .75])
        spread = q3 - q1
        flags[col] = frame[col].lt(q1 - 1.5 * spread) | frame[col].gt(q3 + 1.5 * spread)
        records.append({"feature": col, "q1": q1, "q3": q3, "IQR": spread,
                        "candidate_count": flags[col].sum(), "candidate_pct": flags[col].mean() * 100})
    return pd.DataFrame(records), flags


def contextual_duration(frame, entity, min_support=30):
    """Descriptive, within-period baselines. Do not use these in held-out models."""
    valid = frame["duration_ms"].where(frame["duration_ms"].ge(0))
    work = frame[["source_period", entity]].copy()
    work["log_duration"] = np.log1p(valid)
    profile = work.groupby(["source_period", entity], observed=True)["log_duration"].agg(
        support="count", median="median", MAD=lambda x: (x - x.median()).abs().median(),
    )
    idx = pd.MultiIndex.from_frame(work[["source_period", entity]])
    aligned = profile.reindex(idx).set_axis(frame.index)
    comparable = aligned["support"].ge(min_support) & aligned["MAD"].gt(0) & valid.notna()
    score = (.6745 * (work["log_duration"] - aligned["median"]) / aligned["MAD"].replace(0, np.nan)).where(comparable)
    return profile, pd.DataFrame({"context_z": score, "context_comparable": comparable,
                                  "context_candidate": comparable & score.abs().gt(3.5)}, index=frame.index)


def numeric_drift(frame, columns, reference_period=PERIODS[0]):
    """Population stability index with fixed reference quantile bins and smoothing."""
    records = []
    for col in columns:
        reference = frame.loc[frame["source_period"].eq(reference_period), col].dropna()
        if reference.empty:
            continue
        quantiles = np.unique(reference.quantile(np.linspace(0, 1, 11)).to_numpy())
        internal = quantiles[1:-1]
        if len(quantiles) == 2:
            internal = np.array([(quantiles[0] + quantiles[1]) / 2])
        # Separate values outside the reference range, including a constant
        # reference (e.g. retryCount=0) followed by newly nonzero observations.
        edges = np.unique(np.r_[-np.inf, quantiles[0], internal,
                               np.nextafter(quantiles[-1], np.inf), np.inf])
        def distribution(values):
            counts = np.histogram(values.dropna().to_numpy(dtype=float), bins=edges)[0].astype(float)
            return (counts + .5) / (counts.sum() + .5 * len(counts))
        ref_p = distribution(reference)
        for period, group in frame.groupby("source_period", observed=True):
            current = group[col]
            cur_p = distribution(current)
            records.append({"feature": col, "source_period": period,
                            "reference_rows": len(reference), "current_nonmissing": current.count(),
                            "bins": len(edges) - 1,
                            "PSI": np.sum((cur_p - ref_p) * np.log(cur_p / ref_p)),
                            "median": current.median(), "p99": current.quantile(.99),
                            "missing_pct": current.isna().mean() * 100})
    return pd.DataFrame(records)


def categorical_drift(frame, columns, reference_period=PERIODS[0]):
    records = []
    reference_mask = frame["source_period"].eq(reference_period)
    for col in columns:
        known = set(frame.loc[reference_mask, col].dropna().astype(str))
        for period, group in frame.groupby("source_period", observed=True):
            values = group[col].dropna().astype(str)
            records.append({"feature": col, "source_period": period, "unique_values": values.nunique(),
                            "new_values": values.loc[~values.isin(known)].nunique(),
                            "new_value_row_pct": (~values.isin(known)).mean() * 100 if len(values) else np.nan})
    return pd.DataFrame(records)


def dependency_features(frame):
    keys = ["source_period", "transactionId", "processId"]
    valid = frame.loc[frame["transactionId"].notna() & frame["processId"].notna()]
    parent = valid.groupby(keys, observed=True, dropna=False).agg(
        parent_attempts=("processId", "size"), parent_end=("endTime", "max"),
    )
    lookup = pd.MultiIndex.from_arrays([frame["source_period"], frame["transactionId"], frame["dependProcessId"]], names=keys)
    aligned = parent.reindex(lookup).set_axis(frame.index)
    has_parent = frame["dependProcessId"].notna() & text(frame["dependProcessId"]).ne("NONE")
    found = has_parent & aligned["parent_attempts"].notna()
    comparable = found & aligned["parent_attempts"].eq(1) & aligned["parent_end"].notna() & frame["startTime"].notna()
    gap = (frame["startTime"] - aligned["parent_end"]).dt.total_seconds() * 1000
    scope = pd.MultiIndex.from_arrays([frame["source_period"], frame["dependProcessId"]])
    templates = valid.groupby(["source_period", "processId"], observed=True).size()
    exists_elsewhere = pd.Series(templates.reindex(scope).notna().to_numpy(), index=frame.index)
    return pd.DataFrame({
        "has_predecessor": has_parent, "parent_log_found": found,
        "dependency_missing": has_parent & ~found,
        "dependency_retry_ambiguous": found & aligned["parent_attempts"].gt(1),
        "dependency_time_comparable": comparable,
        "dependency_gap_ms": gap.where(comparable),
        "dependency_overlap_candidate": comparable & gap.lt(-1),
        "template_only_elsewhere": has_parent & ~found & exists_elsewhere,
    }, index=frame.index)


def latest_transaction_state(frame):
    keys = ["source_period", "transactionId"]
    valid = frame.loc[frame["transactionId"].notna()]
    return valid.sort_values(keys + ["retryCount", "endTime", "startTime"], na_position="first").drop_duplicates(keys, keep="last").reset_index(drop=True)


def transaction_process_summary(transactions, processes):
    """Use distinct logical processes for declared counts, attempts for retries."""
    keys = ["source_period", "transactionId"]
    p = processes.loc[processes["transactionId"].notna()]
    agg = p.groupby(keys, observed=True).agg(
        process_attempts=("processId", "size"), distinct_processes=("processId", "nunique"),
        process_first_start=("startTime", "min"), process_last_end=("endTime", "max"),
        bottleneck_duration_ms=("duration_ms", "max"),
    )
    agg["process_duration_sum_ms"] = p.groupby(keys, observed=True)["duration_ms"].sum(min_count=1)
    # Retry snapshots are resolved only for status consistency, not volume EDA.
    latest = p.sort_values(keys + ["processId", "retryCount", "endTime", "startTime"], na_position="first").drop_duplicates(keys + ["processId"], keep="last")
    states = latest[keys].copy()
    states["success"] = latest["status"].eq("S").to_numpy()
    states["failure"] = latest["status"].eq("F").to_numpy()
    status = states.groupby(keys, observed=True).agg(
        logical_processes=("success", "size"), latest_success=("success", "sum"), latest_failure=("failure", "sum"),
    )
    agg = agg.join(status)
    agg["process_derived_status"] = np.select(
        [agg["latest_failure"].gt(0), agg["latest_success"].eq(agg["logical_processes"])],
        ["F", "S"], default="OTHER",
    )
    tx = latest_transaction_state(transactions)
    summary = tx.merge(agg.reset_index(), on=keys, how="left", validate="one_to_one")
    summary["no_process_logs"] = summary["process_attempts"].isna()
    summary["process_count_diff"] = summary["distinct_processes"].fillna(0) - summary["processCount"]
    summary["count_comparable"] = summary["processCount"].notna()
    summary["count_mismatch_candidate"] = summary["count_comparable"] & summary["process_count_diff"].ne(0)
    summary["process_derived_status"] = summary["process_derived_status"].fillna("NO_PROCESS")
    summary["status_comparable"] = summary["status"].isin(["S", "F"]) & summary["process_derived_status"].isin(["S", "F"])
    summary["status_mismatch_candidate"] = summary["status_comparable"] & text(summary["status"]).ne(summary["process_derived_status"])
    summary["start_delta_ms"] = (summary["process_first_start"] - summary["startTime"]).dt.total_seconds() * 1000
    summary["end_delta_ms"] = (summary["endTime"] - summary["process_last_end"]).dt.total_seconds() * 1000
    summary["process_span_ms"] = (summary["process_last_end"] - summary["process_first_start"]).dt.total_seconds() * 1000
    summary["orchestration_overhead_ms"] = summary["duration_ms"] - summary["process_span_ms"]
    summary["parallelism_ratio"] = summary["process_duration_sum_ms"] / summary["duration_ms"].where(summary["duration_ms"].gt(0))
    summary["boundary_candidate"] = summary["start_delta_ms"].lt(-1) | summary["end_delta_ms"].lt(-1)
    return summary


def chronological_split(frame):
    """60/20/20 by observed dates; all events of one transaction stay together."""
    grouped_start = frame.groupby(["source_period", "transactionId"], observed=True)["startTime"].transform("min")
    anchor = grouped_start.dt.normalize()
    days = pd.Index(anchor.dropna().unique()).sort_values()
    if len(days) < 5:
        raise ValueError("시간 순서 검증을 위해 최소 5개의 관측 날짜가 필요합니다.")
    train_end = days[max(0, int(len(days) * .6) - 1)]
    validation_end = days[max(1, int(len(days) * .8) - 1)]
    split = pd.Series("excluded_missing_time_or_id", index=frame.index, dtype="string")
    valid = anchor.notna() & frame["transactionId"].notna() & frame["startTime"].notna()
    # Transactions crossing a cutoff are excluded from all three partitions.
    # This keeps group membership AND strict chronological event order intact.
    group_end = frame.groupby(["source_period", "transactionId"], observed=True)["startTime"].transform("max").dt.normalize()
    crossed = ((anchor.le(train_end) & group_end.gt(train_end)) |
               (anchor.le(validation_end) & group_end.gt(validation_end)))
    split.loc[valid & ~crossed & group_end.le(train_end)] = "train"
    split.loc[valid & ~crossed & anchor.gt(train_end) & group_end.le(validation_end)] = "validation"
    split.loc[valid & ~crossed & anchor.gt(validation_end)] = "test"
    split.loc[valid & crossed] = "excluded_boundary_transaction"
    return split


def signed_log(series):
    return np.sign(series) * np.log1p(series.abs())


def baseline_features(frame, numeric_columns):
    result = pd.DataFrame(index=frame.index)
    for col in numeric_columns:
        result[f"log_{col}"] = signed_log(frame[col])
        result[f"missing_{col}"] = frame[col].isna().astype(float)
    hour = frame["startTime"].dt.hour
    weekday = frame["startTime"].dt.dayofweek
    for name, value, cycle in [("hour", hour, 24), ("weekday", weekday, 7)]:
        result[f"{name}_sin"] = np.sin(2 * np.pi * value / cycle)
        result[f"{name}_cos"] = np.cos(2 * np.pi * value / cycle)
    return result.replace([np.inf, -np.inf], np.nan)


def fit_anomaly_baseline(frame, numeric_columns, categorical_columns, *, max_train=100_000,
                         batch_size=100_000, alert_quantile=.99, random_state=42):
    """Train-only preprocessing; held-out validation threshold; batched scoring."""
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import RobustScaler

    forbidden = {"transactionId", "source_period", "source_file", "status", "responseCode",
                 "errorCount", "successCount"}
    if forbidden.intersection(numeric_columns + categorical_columns):
        raise ValueError("ID/기간/사후 진단 상태는 이 행동 기반 기준 모델의 입력에서 제외합니다.")
    split = chronological_split(frame)
    train = frame.loc[split.eq("train")]
    if len(train) < 20 or not split.eq("validation").any() or not split.eq("test").any():
        raise ValueError("학습/검증/시험 데이터가 충분하지 않습니다.")
    sampled = sample_by_period(train, n=max_train, random_state=random_state)
    frequencies = {col: text(train[col]).value_counts() for col in categorical_columns}
    training_rows = len(train)
    def raw_features(chunk):
        X = baseline_features(chunk, numeric_columns)
        for col, freq in frequencies.items():
            count = text(chunk[col]).map(freq).fillna(0).astype(float)
            X[f"rarity_{col}"] = -np.log((count + 1) / (training_rows + len(freq) + 1))
            X[f"unseen_{col}"] = count.eq(0).astype(float)
        return X
    sample_X = raw_features(sampled)
    medians = sample_X.median().fillna(0)
    train_X = sample_X.fillna(medians)
    # Keep unseen flags even when constant in training: new templates matter.
    kept = [c for c in train_X if train_X[c].nunique() > 1 or c.startswith("unseen_") or c.startswith("missing_")]
    scaler = RobustScaler().fit(train_X[kept])
    def transform(chunk):
        return scaler.transform(raw_features(chunk).fillna(medians)[kept]).astype(np.float32)
    X_train = transform(sampled)
    forest = IsolationForest(n_estimators=64, max_samples=min(512, len(sampled)),
                             contamination="auto", n_jobs=2, random_state=random_state).fit(X_train)
    scores = pd.Series(np.nan, index=frame.index, dtype=float)
    for start in range(0, len(frame), batch_size):
        chunk = frame.iloc[start:start + batch_size]
        scores.iloc[start:start + len(chunk)] = -forest.score_samples(transform(chunk))
    # Invalid/cross-boundary events are audited but excluded from model results.
    eligible = split.isin(["train", "validation", "test"])
    scores.loc[~eligible] = np.nan
    threshold = scores.loc[split.eq("validation")].quantile(alert_quantile)
    result = pd.DataFrame({"split": split, "anomaly_score": scores,
                           "anomaly_candidate": eligible & scores.ge(threshold)}, index=frame.index)
    profile = {
        "training_rows": len(train), "training_sample_rows": len(sampled),
        "validation_rows": int(split.eq("validation").sum()), "test_rows": int(split.eq("test").sum()),
        "feature_count": len(kept), "validation_quantile": alert_quantile, "threshold": threshold,
        "features": kept,
    }
    return result, profile, {"forest": forest, "scaler": scaler, "transform": transform, "X_train": X_train}


def baseline_report(frame, result):
    data = pd.DataFrame({"source_period": frame["source_period"], "split": result["split"],
                         "score": result["anomaly_score"], "candidate": result["anomaly_candidate"]})
    return data.groupby(["source_period", "split"], observed=True).agg(
        rows=("score", "size"), scored_rows=("score", "count"), mean_score=("score", "mean"),
        candidates=("candidate", "sum"), candidate_pct=("candidate", lambda s: s.mean() * 100),
    ).round(4)


def setup_plotting():
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import seaborn as sns
    fonts = {font.name for font in fm.fontManager.ttflist}
    selected = next((name for name in ["Malgun Gothic", "Noto Sans KR", "NanumGothic", "AppleGothic"] if name in fonts), "DejaVu Sans")
    sns.set_theme(style="whitegrid", rc={"font.family": selected, "axes.unicode_minus": False})
    plt.rcParams.update({"figure.figsize": (13, 4), "figure.dpi": 120,
                         "font.size": 11, "axes.titlesize": 12, "axes.labelsize": 11,
                         "xtick.labelsize": 10, "ytick.labelsize": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "grid.alpha": .22})
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 30)


def plot_period_timeseries(summary, value, title):
    import matplotlib.pyplot as plt
    groups = list(summary.groupby("source_period", observed=True))
    fig, axes = plt.subplots(len(groups), 1, figsize=(13, 3 * len(groups)), squeeze=False)
    for ax, (period, group) in zip(axes.flat, groups):
        ax.plot(group["bin"], group[value], marker=".", linewidth=1)
        ax.set(title=f"{title} · {period}", ylabel=value)
    fig.tight_layout()
    plt.show()


def plot_categories(frame, columns, top_n=12):
    import matplotlib.pyplot as plt
    for col in columns:
        counts = text(frame[col]).value_counts().head(top_n).sort_values()
        fig, ax = plt.subplots(figsize=(10, max(3, .3 * len(counts))))
        ax.barh(counts.index.astype(str), counts.values)
        ax.set(title=f"{col} · 상위 {top_n}개", xlabel="로그 행 수")
        fig.tight_layout()
        plt.show()
