"""New-data entry points; no archived research inputs or caches are opened.

Published v7 prediction is unchanged. New-data refitting is a NEW normal-only
baseline using the published core settings, NOT a claim to reproduce v7's
development-label-selected fusion/cutoff tables or its benchmark metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix


def read_frame(path):
    path = Path(path)
    if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        else:
            value = [json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
        return pd.DataFrame(value)
    return pd.read_csv(path, encoding="utf-8-sig")


def timestamps(values):
    values = pd.to_datetime(values, format="mixed", errors="raise")
    if values.dt.tz is not None:
        values = values.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    if values.isna().any():
        raise ValueError("Missing timestamps are not an observed empty minute")
    return values.astype("datetime64[ns]")


def inputs(native, completions):
    """Strict one-minute grid and completed Process observations.

    Zero-count rows must come from certified collection, never from guessing
    CSV coverage. Full observed prefix is the caller's explicit responsibility.
    """
    n, c = native.copy(), completions.copy()
    keys = ["case_id", "adapterType", "processId"]
    needed = {*keys, "window_start", "execution_arrivals"}
    if needed - set(n):
        raise ValueError(f"Missing native columns: {sorted(needed - set(n))}")
    required = {*keys, "totalCount", "duration", "end_seconds"}
    if required - set(c):
        raise ValueError(f"Missing completion columns: {sorted(required - set(c))}")
    if n.empty or n[keys].isna().any().any() or c[keys].isna().any().any():
        raise ValueError("Nonempty grid and non-null monitoring identities required")
    for col in keys:
        n[col], c[col] = n[col].astype(str), c[col].astype(str)
    n["window_start"] = timestamps(n.window_start)
    expected_end = n.window_start + pd.Timedelta(minutes=1)
    if "window_end" in n and not timestamps(n.window_end).equals(expected_end):
        raise ValueError("Each window must be exactly one minute")
    n["window_end"] = expected_end
    if n.duplicated(["case_id", "window_start"]).any():
        raise ValueError("Duplicate native minute")
    if n.groupby("case_id")[keys[1:]].nunique().gt(1).any().any():
        raise ValueError("One Adapter+Process per case_id required")
    for _, g in n.groupby("case_id", sort=False):
        if not g.window_start.diff().dropna().eq(pd.Timedelta(minutes=1)).all():
            raise ValueError("Grid must be chronological and contiguous")
    for frame, cols in ((n, ["execution_arrivals"]), (c, ["totalCount", "duration", "end_seconds"])):
        for col in cols:
            frame[col] = pd.to_numeric(frame[col], errors="raise").astype(float)
            if not np.isfinite(frame[col]).all() or (frame[col] < 0).any():
                raise ValueError(f"Invalid {col}")
            if col in {"execution_arrivals", "totalCount"} and (frame[col] % 1 != 0).any():
                raise ValueError(f"{col} must be an integer")
    identities = set(map(tuple, n[keys].drop_duplicates().to_numpy()))
    if set(map(tuple, c[keys].drop_duplicates().to_numpy())) - identities:
        raise ValueError("Completions refer to an unregistered stream")
    # Native quantiles are recomputed from available completions, not trusted
    # from possibly stale/future aggregates supplied by a caller.
    from model_evaluation.data import unix_seconds
    lower = unix_seconds(n.window_start)
    raw_groups = {key: g.sort_values("end_seconds") for key, g in c.groupby("case_id", sort=False)}
    q = np.full((len(n), 2), np.nan)
    for key, ids in n.groupby("case_id", sort=False).indices.items():
        raw = raw_groups.get(key)
        if raw is None:
            continue
        t, d = raw.end_seconds.to_numpy(), raw.duration.to_numpy()
        for i in ids:
            a, b = np.searchsorted(t, [lower[i], lower[i] + 60], side="left")
            if b > a:
                q[i] = np.quantile(d[a:b], [.5, .95])
    n["duration_p50_ms"], n["duration_p95_ms"] = q.T
    return n.reset_index(drop=True), c.reset_index(drop=True)


@dataclass
class CountHistory:
    values: dict
    fallback_strata: list

    @classmethod
    def fit(cls, frame):
        dates = frame.window_start
        values, fallback = {}, []
        for width in range(1, 16):
            counts = frame.groupby("case_id", sort=False).execution_arrivals.transform(
                lambda s: s.rolling(width, min_periods=width).sum())
            pooled = counts.dropna().to_numpy(float)
            if not len(pooled):
                raise ValueError("At least 15 contiguous normal minutes are required")
            for weekend in (False, True):
                for hour in range(24):
                    x = counts.loc[dates.dt.dayofweek.ge(5).eq(weekend) & dates.dt.hour.eq(hour)].dropna().to_numpy(float)
                    if not len(x):
                        x = pooled
                        fallback.append((width, weekend, hour))
                    values[width, weekend, hour] = (float(np.median(x)), float(np.quantile(x, .95)), len(x))
        return cls(values, fallback)

    def lookup(self, dates, widths):
        return np.array([self.values[int(w), bool(d.dayofweek >= 5), int(d.hour)] for d, w in zip(dates, widths)], float)


def rank_history(native):
    dates = native.window_start
    pooled = tuple(native.execution_arrivals.astype(int))
    result = {}
    for weekend in (False, True):
        for hour in range(24):
            x = native.loc[dates.dt.dayofweek.ge(5).eq(weekend) & dates.dt.hour.eq(hour), "execution_arrivals"]
            result[weekend, hour] = tuple(x.astype(int)) if len(x) else pooled
    return result


def fit_processor(native, completions, algorithm):
    from model_improvement.features import ConditionalBaseline
    from model_research_v8.features import ConditionalTails
    from model_research_v8.inference import ProcessResearchMonitor as Base
    from model_research_v13.features import FixedNormalAnchor, matched_precision
    from model_research_v15.inference import ProcessResearchMonitor
    source = completions.rename(columns={"duration": "duration_ms"})
    if len(source) < 30:
        raise ValueError("Need at least 30 reviewed normal completions; this is a minimum, not a statistical guarantee")
    baseline = ConditionalBaseline.fit(source)
    tails = ConditionalTails.fit(source)
    legacy = {"global": float(source.duration_ms.median()),
              "adapter": source.groupby("adapterType").duration_ms.median().to_dict(),
              "process": source.groupby(["adapterType", "processId"]).duration_ms.median().to_dict()}
    history = CountHistory.fit(native)
    base = Base(None, tails, baseline, legacy, history, algorithm)
    anchor = FixedNormalAnchor.fit(matched_precision(base.features(native, completions)))
    context = {"reference": tails, "baseline": baseline, "legacy_baselines": legacy, "history": history, "anchor": anchor}
    return ProcessResearchMonitor(None, context, algorithm)


@dataclass
class RetrainedMonitor:
    processor: object
    reference: object
    detector: object
    threshold: float
    provenance: dict

    def features(self, native, completions):
        native, completions = inputs(native, completions)
        return self.reference.transform(self.processor.features(native, completions))

    def predict(self, native, completions):
        f = self.features(native, completions)
        score = self.detector.score(f)
        out = f[["case_id", "adapterType", "processId", "window_start"]].copy()
        out["score"], out["threshold"], out["anomaly"] = score, self.threshold, score > self.threshold
        return out


def train(normal_native, normal_completions, calibration_native, calibration_completions,
          *, algorithm, setting, quantile=.99):
    """Refit every feature reference and one estimator on caller normal data.

    Validation is later and stream-disjoint. Its normal scores determine one
    quantile cutoff. No anomaly labels select parameters in this entry point.
    """
    from shared_model_target.current_features import CurrentAdapterReference
    from shared_model_v3.features import SequenceReference
    from shared_model_v6.models import Detector, Setting
    if algorithm not in {"if", "kmeans", "lof"} or not 0 < quantile < 1:
        raise ValueError("Invalid algorithm or quantile")
    n, c = inputs(normal_native, normal_completions)
    v, vc = inputs(calibration_native, calibration_completions)
    if set(n.case_id) & set(v.case_id):
        raise ValueError("Normal fit and calibration case IDs must be disjoint")
    if n.window_end.max() > v.window_start.min():
        raise ValueError("Calibration must follow normal fit data chronologically")
    if c.end_seconds.max() >= n.window_end.max().value / 1e9:
        raise ValueError("Future completions cannot fit normal references")
    if len(v) < 30:
        raise ValueError("Need at least 30 normal calibration minutes")
    processor = fit_processor(n, c, algorithm)
    normal = processor.features(n, c)
    reference = SequenceReference(CurrentAdapterReference.fit(normal))
    normal = reference.transform(normal)
    calibration = reference.transform(processor.features(v, vc))
    cfg = dict(setting)
    cfg["algorithm"] = algorithm
    detector = Detector(Setting(**cfg), rank_history(n)).fit(normal)
    threshold = float(np.quantile(detector.score(calibration), quantile, method="higher"))
    provenance = {"kind": "new_data_normal_only_core_refit", "published_v7_metrics_apply": False,
                  "all_feature_references_refit": True, "normal_fit_minutes": len(n),
                  "normal_calibration_minutes": len(v), "normal_quantile": quantile,
                  "missing_time_strata_use_pooled_normal_counts": len(processor.history.fallback_strata),
                  "v7_development_selected_fusion_and_cutoff_tables_reproduced": False,
                  "normal_only_does_not_guarantee_real_data_are_normal": True}
    return RetrainedMonitor(processor, reference, detector, threshold, provenance)


def evaluate(predictions, labels):
    keys = ["case_id", "window_start"]
    p, y = predictions.copy(), labels.copy()
    if set(keys + ["is_anomaly"]) - set(y):
        raise ValueError("Labels require case_id, window_start, is_anomaly")
    p["window_start"], y["window_start"] = timestamps(p.window_start), timestamps(y.window_start)
    p["case_id"], y["case_id"] = p.case_id.astype(str), y.case_id.astype(str)
    if p.duplicated(keys).any() or y.duplicated(keys).any():
        raise ValueError("Duplicate prediction/label key")
    merged = p.merge(y[keys + ["is_anomaly"]], on=keys, how="outer", indicator=True, validate="one_to_one")
    if not merged._merge.eq("both").all():
        raise ValueError("Every prediction needs exactly one label, with no extra labels")
    if merged.empty:
        raise ValueError("Empty evaluation")
    if not merged.is_anomaly.isin([0, 1, False, True]).all():
        raise ValueError("Labels must be boolean or 0/1")
    tn, fp, fn, tp = map(int, confusion_matrix(merged.is_anomaly.astype(bool), merged.anomaly.astype(bool), labels=[False, True]).ravel())
    return {"accuracy": (tn + tp) / len(merged), "precision": tp / max(tp + fp, 1),
            "recall": tp / max(tp + fn, 1), "f1": 2 * tp / max(2 * tp + fp + fn, 1),
            "false_positive_rate": fp / max(fp + tn, 1), "tn": tn, "fp": fp, "fn": fn, "tp": tp,
            "evaluation_unit": "one monitoring stream/minute", "point_adjustment": False}


def load_trusted(path, expected_sha256=None):
    path = Path(path)
    if expected_sha256 is not None:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for b in iter(lambda: f.read(1024 * 1024), b""):
                h.update(b)
        if h.hexdigest() != expected_sha256:
            raise ValueError("Model hash mismatch")
    # An integrity hash does not make an untrusted pickle safe.
    return joblib.load(path)
