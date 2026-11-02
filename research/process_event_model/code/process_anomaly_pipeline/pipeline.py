"""Fit once offline; reuse read-only models for backend Process inference."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import platform
import math
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest

from .config import PipelineConfig
from .data import ALLOWED_STATUSES, COUNTS, PROCESS_PK, chronological_split, prepare
from .features import FeatureEncoder, ScoreNormalizer, feature_profile
from .quantiles import QuantileBook
from .rules import (CONSISTENCY_RULES, QUALITY_RULES, RULE_DESCRIPTIONS, RULE_NAMES,
                    any_hits, decode_rule_bits, evaluate_rules, model_eligible,
                    pack_rule_bits, reference_eligible)

LOG = logging.getLogger(__name__)


@dataclass
class ModelBundle:
    name: str
    forest: IsolationForest
    encoder: FeatureEncoder
    normalizer: ScoreNormalizer
    raw_threshold: float
    profile: dict
    support: dict

    def raw_score(self, frame):
        return -self.forest.score_samples(self.encoder.transform(frame))

    def score(self, frame):
        raw = self.raw_score(frame)
        return raw, self.normalizer.transform(raw)


class ProcessAnomalyPipeline:
    """Transaction metadata is optional rule context, NEVER an IF training sample.

    predict returns one row per input Process, including invalid/unscored rows.
    A caller must explicitly declare complete_context for relation rules; absence
    or ambiguity yields nullable unknown flags, not a false normal assessment.
    """

    def __init__(self, config=None):
        self.config = config if isinstance(config, PipelineConfig) else PipelineConfig.from_dict(config)
        self.adapter_models = {}
        self.global_model = None
        self.route_thresholds = {}
        self.quantile_book = None
        self.training_summary = {}
        self.artifact_version = 1

    def _fit_one(self, name, train, calibration, threshold):
        c = self.config
        sampled = (_balanced_sample(train, c.max_train_rows, c.random_state) if name == "GLOBAL" else
                   train.sample(n=min(len(train), c.max_train_rows), random_state=c.random_state))
        encoder = FeatureEncoder.fit(sampled)
        forest = IsolationForest(n_estimators=c.n_estimators, max_samples=min(c.max_samples, len(sampled)),
                                 max_features=c.max_features, bootstrap=c.bootstrap,
                                 contamination="auto", n_jobs=c.n_jobs, random_state=c.random_state)
        forest.fit(encoder.transform(sampled))
        cal = calibration.sample(n=min(len(calibration), c.max_calibration_rows), random_state=c.random_state)
        cal_scores = -forest.score_samples(encoder.transform(cal))
        normalizer = ScoreNormalizer.fit(cal_scores)
        scores = self._raw_batches(forest, encoder, threshold)
        support = {"train_rows": len(train), "train_sample_rows": len(sampled), "train_days": int(train.startTime.dt.normalize().nunique()),
                   "calibration_rows": len(calibration), "calibration_sample_rows": len(cal), "threshold_rows": len(threshold),
                   "first_train": train.startTime.min().isoformat(), "last_train": train.startTime.max().isoformat(),
                   "effective_max_samples": int(forest.max_samples_),
                   "sample_adapter_counts": {str(k): int(v) for k, v in sampled.adapterType.value_counts(dropna=False).items()},
                   "sampling": "adapter_balanced" if name == "GLOBAL" else "uniform_without_replacement"}
        LOG.info("Trained %s: train=%s calibration=%s threshold=%s", name, len(train), len(calibration), len(threshold))
        return ModelBundle(name, forest, encoder, normalizer, float(np.quantile(scores, c.score_quantile)), feature_profile(train), support)

    def _raw_batches(self, forest, encoder, frame):
        values = np.empty(len(frame), dtype=float)
        for start in range(0, len(frame), self.config.score_batch_rows):
            chunk = frame.iloc[start:start + self.config.score_batch_rows]
            values[start:start + len(chunk)] = -forest.score_samples(encoder.transform(chunk))
        return values

    def fit(self, processes, *, transactions=None, complete_context=False):
        if complete_context and transactions is None:
            raise ValueError("complete_context=True requires Transaction context")
        self.global_model = None
        self.route_thresholds = {}
        self.adapter_models = {}
        self.training_summary = {}
        p = prepare(processes)
        if p.empty:
            raise ValueError("Empty training data")
        LOG.info("Evaluating rules and temporal split for %s Process rows", len(p))
        flags, _ = evaluate_rules(p, self.config, transactions=transactions, complete_context=complete_context)
        split = chronological_split(p, self.config)
        base = reference_eligible(p, flags)
        train_reference = p.loc[split.eq("train") & base]
        if len(train_reference) < self.config.min_global_train:
            raise ValueError("Insufficient eligible S logs in the past training partition")
        self.quantile_book = QuantileBook.fit(train_reference, self.config)
        flags["statistical_tail"], _ = self.quantile_book.evaluate(p)
        reference = reference_eligible(p, flags, exclude_statistical=self.config.exclude_statistical_from_training)
        train = p.loc[split.eq("train") & reference]
        calibration = p.loc[split.eq("calibration") & reference]
        threshold = p.loc[split.eq("threshold") & reference]
        minima = ((train, self.config.min_global_train, "train"),
                  (calibration, self.config.min_global_calibration, "calibration"),
                  (threshold, self.config.min_global_threshold, "threshold"))
        for frame, minimum, name in minima:
            if len(frame) < minimum:
                raise ValueError(f"Insufficient global {name} support: {len(frame)} < {minimum}")
        # Adapter experts first; the global model is the fallback and ensemble peer.
        self.adapter_models = {}
        skipped = []
        names = sorted(p.adapterType.dropna().astype("string").unique())
        observed_in_train = sorted(p.loc[split.eq("train"), "adapterType"].dropna().astype("string").unique())
        for adapter in names:
            t = train.loc[train.adapterType.eq(adapter)]
            ca = calibration.loc[calibration.adapterType.eq(adapter)]
            th = threshold.loc[threshold.adapterType.eq(adapter)]
            days = t.startTime.dt.normalize().nunique()
            reasons = []
            if len(t) < self.config.min_adapter_train: reasons.append("insufficient_train")
            if len(ca) < self.config.min_adapter_calibration: reasons.append("insufficient_calibration")
            if len(th) < self.config.min_adapter_threshold: reasons.append("insufficient_threshold")
            if days < self.config.min_adapter_days: reasons.append("insufficient_days")
            if reasons:
                skipped.append({"adapter": str(adapter), "train_rows": len(t), "calibration_rows": len(ca),
                                "threshold_rows": len(th), "train_days": int(days), "reasons": reasons})
                continue
            try:
                self.adapter_models[str(adapter)] = self._fit_one(str(adapter), t, ca, th)
            except ValueError as exc:
                skipped.append({"adapter": str(adapter), "train_rows": len(t), "calibration_rows": len(ca),
                                "threshold_rows": len(th), "train_days": int(days), "reasons": [str(exc)]})
        self.global_model = self._fit_one("GLOBAL", train, calibration, threshold)
        global_threshold_scores = self.global_model.normalizer.transform(
            self._raw_batches(self.global_model.forest, self.global_model.encoder, threshold))
        self.route_thresholds = {"GLOBAL": float(np.quantile(global_threshold_scores, self.config.score_quantile))}
        for adapter, bundle in self.adapter_models.items():
            mask = threshold.adapterType.eq(adapter).to_numpy()
            _, local_scores = bundle.score(threshold.loc[mask])
            ensemble = self.config.global_weight * global_threshold_scores[mask] + (1 - self.config.global_weight) * local_scores
            self.route_thresholds[adapter] = float(np.quantile(ensemble, self.config.score_quantile))
        support = []
        for name, idx in split.groupby(split).groups.items():
            group = p.loc[idx]
            support.append({"split": str(name), "rows": len(idx), "reference_rows": int(reference.loc[idx].sum()),
                            "first": None if group.startTime.notna().sum() == 0 else group.startTime.min().isoformat(),
                            "last": None if group.startTime.notna().sum() == 0 else group.startTime.max().isoformat()})
        self.training_summary = {
            "created_utc": datetime.now(timezone.utc).isoformat(), "config": self.config.to_dict(),
            "input_unit": "one Process execution", "rows": len(p), "reference_train_before_tail_filter": len(train_reference),
            "splits": support, "adapter_models": list(self.adapter_models), "skipped_adapters": skipped,
            "observed_adapters": observed_in_train, "export_adapters_for_post_analysis": names,
            "complete_context_declared": bool(complete_context),
            "global_support": self.global_model.support, "route_thresholds": self.route_thresholds,
            "feature_columns": list(self.global_model.encoder.columns),
            "versions": {"python": platform.python_version(), "numpy": np.__version__,
                         "pandas": pd.__version__, "sklearn": sklearn.__version__},
        }
        self.split_ = split
        LOG.info("Fitted global model and %s adapter experts", len(self.adapter_models))
        return self

    def _require_fitted(self):
        if self.global_model is None or self.quantile_book is None or "GLOBAL" not in self.route_thresholds:
            raise RuntimeError("Call fit() or load() before inference")

    def predict(self, processes, *, transactions=None, complete_context=False):
        self._require_fitted()
        if complete_context and transactions is None:
            raise ValueError("complete_context=True requires Transaction context")
        p = prepare(processes)
        flags, context = evaluate_rules(p, self.config, transactions=transactions, complete_context=complete_context)
        flags["statistical_tail"], quantiles = self.quantile_book.evaluate(p)
        result = pd.DataFrame(index=p.index)
        for c in ("source_period", "transactionId", "processId", "processHubId", "retryCount", "adapterType", "status"):
            result[c] = p[c]
        result = pd.concat([result, flags.add_prefix("rule_"), context, quantiles], axis=1)
        eligible = model_eligible(p, flags)
        result["model_eligible"] = eligible
        result["global_raw_score"] = np.nan
        result["global_score"] = np.nan
        result["adapter_raw_score"] = np.nan
        result["adapter_score"] = np.nan
        result["ensemble_score"] = np.nan
        result["model_threshold"] = np.nan
        routes = pd.Series("unscored", index=p.index, dtype="string")
        fallback = pd.Series("not_model_eligible", index=p.index, dtype="string")
        observed = set(self.training_summary.get("observed_adapters", []))
        for start in range(0, len(p), self.config.score_batch_rows):
            part = p.iloc[start:start + self.config.score_batch_rows]
            chunk = part.loc[eligible.iloc[start:start + len(part)].to_numpy()]
            if chunk.empty:
                continue
            raw, global_scores = self.global_model.score(chunk)
            result.loc[chunk.index, "global_raw_score"] = raw
            result.loc[chunk.index, "global_score"] = global_scores
            result.loc[chunk.index, "ensemble_score"] = global_scores
            result.loc[chunk.index, "model_threshold"] = self.route_thresholds["GLOBAL"]
            routes.loc[chunk.index] = "global_only"
            fallback.loc[chunk.index] = "unsupported_adapter"
            fallback.loc[chunk.index[~chunk.adapterType.astype("string").isin(observed)]] = "new_adapter"
            fallback.loc[chunk.index[chunk.adapterType.isna()]] = "missing_adapter"
            for adapter, bundle in self.adapter_models.items():
                local = chunk.loc[chunk.adapterType.eq(adapter)]
                if local.empty:
                    continue
                local_raw, local_scores = bundle.score(local)
                result.loc[local.index, "adapter_raw_score"] = local_raw
                result.loc[local.index, "adapter_score"] = local_scores
                result.loc[local.index, "ensemble_score"] = (self.config.global_weight * result.loc[local.index, "global_score"] +
                                                             (1 - self.config.global_weight) * local_scores)
                result.loc[local.index, "model_threshold"] = self.route_thresholds[adapter]
                routes.loc[local.index] = "global+adapter"
                fallback.loc[local.index] = "none"
        result["model_route"] = routes.astype("category")
        result["fallback_reason"] = fallback.astype("category")
        result["score_margin"] = result.ensemble_score - result.model_threshold
        result["model_candidate"] = eligible & result.ensemble_score.gt(result.model_threshold)
        result["rule_bits"] = pack_rule_bits(flags)
        result["rule_candidate"] = any_hits(flags, RULE_NAMES)
        result["reported_failure"] = flags.status_failed.fillna(False)
        result["quality_error"] = any_hits(flags, QUALITY_RULES)
        result["consistency_candidate"] = any_hits(flags, CONSISTENCY_RULES)
        result["statistical_candidate"] = flags.statistical_tail.fillna(False)
        result["anomaly_candidate"] = result.rule_candidate | result.model_candidate
        decision = pd.Series("not_assessed", index=p.index, dtype="string")
        decision.loc[eligible] = "no_candidate"
        decision.loc[result.model_candidate] = "model_candidate"
        decision.loc[result.statistical_candidate] = "statistical_candidate"
        decision.loc[result.consistency_candidate] = "consistency_candidate"
        decision.loc[result.reported_failure] = "reported_failure"
        decision.loc[result.quality_error] = "quality_error"
        result["decision"] = decision.astype("category")
        return result

    def explain(self, processes, *, transactions=None, complete_context=False, result=None):
        """JSON-safe evidence, not causal explanations or confirmed anomaly labels.

        Call on bounded samples, not millions of rows. IF feature evidence is a
        comparison with training distributions; it is NOT a feature attribution.
        """
        p = prepare(processes)
        if result is None:
            result = self.predict(p, transactions=transactions, complete_context=complete_context)
        else:
            if len(result) != len(p):
                raise ValueError("Explanation result must match input row count")
            result = result.copy()
            result.index = p.index
            for c in ("source_period", "transactionId", "processId", "processHubId", "retryCount", "adapterType", "status"):
                if not result[c].astype("string").fillna("<NA>").equals(p[c].astype("string").fillna("<NA>")):
                    raise ValueError("Explanation result identity/order mismatch")
        rows = []
        for i, r in result.iterrows():
            source = p.loc[i]
            rules = decode_rule_bits(r.rule_bits)
            evidence = []
            for rule in rules:
                item = {"rule": rule, "meaning": RULE_DESCRIPTIONS[rule]}
                if rule == "status_failed": item["observed_status"] = source.status
                if rule == "success_with_error": item.update(status=source.status, errorCount=source.errorCount)
                if rule == "count_conservation": item.update(total=source.totalCount, success=source.successCount, error=source.errorCount, expected_sum=source.successCount + source.errorCount)
                if rule == "negative_duration": item.update(duration_ms=source.duration_ms, tolerance_ms=self.config.time_tolerance_ms)
                if rule in ("process_count_mismatch", "tx_success_all_process_failed"):
                    item.update(transaction_status=r.transactionStatus, declared=r.declared_process_count, observed=r.observed_process_count, context_state=r.context_state)
                if rule == "missing_sql_required":
                    item["missing_columns"] = [c for c in ("transactionId", "processId", "processHubId", "retryCount") if pd.isna(source[c])]
                if rule == "invalid_count_domain":
                    item["invalid_values"] = {c: source[c] for c in COUNTS if c in source and pd.notna(source[c]) and (source[c] < 0 or source[c] % 1 != 0)}
                if rule == "final_missing_end": item.update(status=source.status, endTime=None)
                if rule == "statistical_tail":
                    item["exceedances"] = []
                    for metric in self.quantile_book.columns:
                        for q in self.quantile_book.quantiles:
                            prefix = ("p99" if q == .99 else "p99_9") + "_" + metric
                            flag = r[prefix + "_exceed"]
                            if pd.notna(flag) and bool(flag):
                                item["exceedances"].append({"metric": metric, "quantile": q, "value": source[metric],
                                                          "limit": r[prefix + "_threshold"], "basis": r[prefix + "_basis"]})
                if rule == "invalid_input":
                    diagnostic_columns = [f"_invalid_{c}" for c in (*COUNTS, "startTime", "endTime") if f"_invalid_{c}" in p]
                    item["diagnostics"] = {c: bool(source[c]) for c in diagnostic_columns if bool(source[c])}
                    item["observed_status"] = source.status
                    item["undefined_status"] = bool(pd.notna(source.status) and source.status not in ALLOWED_STATUSES)
                    item["duplicate_process_key"] = bool(r.duplicate_process_key)
                    item["process_key"] = {c: source[c] for c in PROCESS_PK}
                    item["note"] = "Parse error, undefined status, or duplicate Process SQL key in the evaluated batch"
                evidence.append(item)
            bundle = self.adapter_models.get(str(source.adapterType), self.global_model)
            feature_evidence = [{"feature": c, "value": source[c], "reference_group": bundle.name,
                                 "training_p01": stats["p01"], "training_median": stats["p50"], "training_p99": stats["p99"],
                                 "outside_p01_p99": bool(pd.notna(source[c]) and (source[c] < stats["p01"] or source[c] > stats["p99"]))}
                                for c, stats in bundle.profile.items()]
            rows.append({"source_period": source.source_period, "transactionId": source.transactionId,
                         "processId": source.processId, "processHubId": source.processHubId,
                         "retryCount": source.retryCount, "adapterType": source.adapterType,
                         "decision": r.decision, "anomaly_candidate": bool(r.anomaly_candidate), "rule_evidence": evidence,
                         "unevaluated_rules": [name for name in RULE_NAMES if pd.isna(r["rule_" + name])],
                         "model": {"route": r.model_route, "fallback": r.fallback_reason, "global_raw_score": r.global_raw_score,
                                   "adapter_raw_score": r.adapter_raw_score, "global_normalized_score": r.global_score,
                                   "adapter_normalized_score": r.adapter_score, "ensemble_score": r.ensemble_score,
                                   "threshold": r.model_threshold, "margin": r.score_margin,
                                   "criterion": "ensemble_score > route threshold from held-out threshold S reference",
                                   "is_probability": False},
                         "feature_evidence": feature_evidence, "feature_evidence_is_attribution": False})
        return _json_safe(rows)

    def predict_records(self, records, **kwargs):
        return self.explain(records, **kwargs)

    def model_table(self):
        self._require_fitted()
        bundles = {"GLOBAL": self.global_model, **self.adapter_models}
        return pd.DataFrame([{"model": name, **b.support, "raw_score_threshold": b.raw_threshold,
                              "calibration_center": b.normalizer.center, "calibration_spread": b.normalizer.spread,
                              "ensemble_threshold": self.route_thresholds[name]}
                             for name, b in bundles.items()])

    def save(self, path, *, overwrite=False):
        self._require_fitted()
        import joblib
        path = Path(path)
        manifest = path.with_suffix(".manifest.json")
        if not overwrite and (path.exists() or manifest.exists()):
            raise FileExistsError(f"Refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Split membership belongs to the experiment, not the serving artifact.
        state = self.__dict__.copy()
        state.pop("split_", None)
        serving = ProcessAnomalyPipeline(self.config)
        serving.__dict__.update(state)
        joblib.dump(serving, path, compress=3)
        manifest.write_text(json.dumps(self.training_summary, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")

    @classmethod
    def load(cls, path, *, trusted=False):
        if not trusted:
            raise ValueError("joblib/pickle can execute code. Only load your own trusted artifacts with trusted=True")
        import joblib
        model = joblib.load(path)
        if not isinstance(model, cls) or model.artifact_version != 1:
            raise ValueError("Unexpected model artifact type/version")
        versions = model.training_summary["versions"]
        if versions["sklearn"] != sklearn.__version__:
            raise ValueError("Use the same scikit-learn version as the training artifact")
        model._require_fitted()
        return model


def _json_default(value):
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, np.floating):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Not JSON serializable: {type(value)}")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _balanced_sample(frame, maximum, random_state):
    """Water-fill adapter quotas so DB volume does not monopolize global training."""
    if len(frame) <= maximum:
        return frame
    groups = list(frame.groupby("adapterType", observed=True, dropna=False))
    quotas = [0] * len(groups)
    remaining, active = maximum, list(range(len(groups)))
    while remaining and active:
        share = max(1, remaining // len(active))
        next_active = []
        for i in active:
            take = min(share, len(groups[i][1]) - quotas[i], remaining)
            quotas[i] += take
            remaining -= take
            if quotas[i] < len(groups[i][1]):
                next_active.append(i)
        active = next_active
    return pd.concat([group.sample(n=quota, random_state=random_state) for (_, group), quota in zip(groups, quotas) if quota])
