"""Provisional global IF plus frozen adapter context; existing rule code reuse."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from process_anomaly_pipeline.config import PipelineConfig
from process_anomaly_pipeline.features import raw_features
from process_anomaly_pipeline.quantiles import QuantileBook
from process_anomaly_pipeline.rules import evaluate_rules, model_eligible
from .data import reference_mask

HR_NAMES = ("status_failed", "success_with_error", "count_conservation", "final_missing_end",
            "missing_sql_required", "negative_duration", "invalid_count_domain", "invalid_input")


@dataclass
class FrozenModel:
    adapter_stats: dict
    global_stats: tuple
    medians: pd.Series
    scaler: RobustScaler
    forest: IsolationForest
    threshold: float
    quantiles: QuantileBook
    manifest: dict

    @staticmethod
    def feature_frame(frame, stats, global_stats):
        values = raw_features(frame).copy()
        adapters = frame.adapterType.astype("string")
        med = adapters.map({key: val[0] for key, val in stats.items()}).fillna(global_stats[0]).astype(float)
        spread = adapters.map({key: val[1] for key, val in stats.items()}).fillna(global_stats[1]).astype(float)
        values["adapter_duration_robust_z"] = (frame.duration_ms - med) / spread
        values["adapter_frequency"] = adapters.map({key: val[2] for key, val in stats.items()}).fillna(0).astype(float)
        values["adapter_baseline_missing"] = (~adapters.isin(stats)).astype(float)
        return values.replace([np.inf, -np.inf], np.nan)

    @classmethod
    def fit(cls, train, threshold_frame, config):
        reference = train.loc[reference_mask(train)].copy()
        validation = threshold_frame.loc[reference_mask(threshold_frame)].copy()
        if len(reference) < config.min_train_rows or len(validation) < config.min_threshold_rows:
            raise ValueError("Insufficient successful-reference train/threshold support")

        def center_scale(group):
            center = float(group.duration_ms.median())
            mad = float((group.duration_ms - center).abs().median())
            return center, max(1.0, 1.4826 * mad)

        global_stats = center_scale(reference)
        stats = {}
        for adapter, group in reference.groupby("adapterType", observed=True, dropna=True):
            if len(group) >= config.min_adapter_rows:
                stats[str(adapter)] = (*center_scale(group), len(group) / len(reference))
        raw = cls.feature_frame(reference, stats, global_stats)
        medians = raw.median().fillna(0)
        filled = raw.fillna(medians)
        if not filled.nunique().gt(1).any():
            raise ValueError("Constant training features")
        scaler = RobustScaler().fit(filled)
        forest = IsolationForest(n_estimators=128, max_samples=512, random_state=config.model_seed, n_jobs=1)
        # Existing IF hyperparameters, one global model; no adapter routing/ensemble.
        forest.fit(scaler.transform(filled))
        validation_x = scaler.transform(cls.feature_frame(validation, stats, global_stats).fillna(medians))
        threshold = float(np.quantile(-forest.score_samples(validation_x), config.threshold_quantile))
        quantiles = QuantileBook.fit(reference, PipelineConfig())
        manifest = {
            "model_status": "provisional_new_global_IF_not_final_teammate_model",
            "basis": "existing PROCESS raw_features/IsolationForest hyperparameters plus experimental adapter context",
            "feature_names": list(filled.columns), "adapter_baseline_count": len(stats),
            "adapter_stats_fit_role": "train_only", "train_reference_rows": len(reference),
            "threshold_reference_rows": len(validation), "threshold_role": "validation_only",
            "threshold": threshold, "threshold_quantile": config.threshold_quantile,
            "normal_reference": "success_and_quality_proxy_not_verified_normal",
            "global_fallback": "unseen_or_low_support_adapter uses train global median/MAD and frequency0",
            "HR": {"source": "process_anomaly_pipeline.rules.evaluate_rules", "rules": list(HR_NAMES),
                   "meaning": "existing_research_process_logical_rules_not_production_HR_policy"},
            "SR": {"source": "process_anomaly_pipeline.quantiles.QuantileBook",
                   "meaning": "existing_research_statistical_tail_not_production_SR_policy",
                   "minimum_p99_support": 1000, "minimum_p999_support": 10000},
            "composite_policy": "unsupported_not_supplied",
        }
        return cls(stats, global_stats, medians, scaler, forest, threshold, quantiles, manifest)

    def transform(self, frame):
        values = self.feature_frame(frame, self.adapter_stats, self.global_stats).reindex(columns=self.medians.index).fillna(self.medians)
        return self.scaler.transform(values)

    def predict_matrix(self, matrix):
        return -self.forest.score_samples(matrix) > self.threshold

    def predict(self, frame):
        flags, _ = evaluate_rules(frame, PipelineConfig())
        logical = flags[list(HR_NAMES)]
        hr = pd.Series(pd.NA, index=frame.index, dtype="boolean")
        hr.loc[logical.notna().all(axis=1)] = False
        hr.loc[logical.fillna(False).any(axis=1)] = True
        sr, _ = self.quantiles.evaluate(frame)
        ml = pd.Series(pd.NA, index=frame.index, dtype="boolean")
        score = pd.Series(np.nan, index=frame.index)
        eligible = model_eligible(frame, flags).fillna(False)
        if eligible.any():
            values = -self.forest.score_samples(self.transform(frame.loc[eligible]))
            finite = np.isfinite(values)
            assessed = frame.index[eligible][finite]
            score.loc[assessed] = values[finite]
            ml.loc[assessed] = values[finite] > self.threshold
        return pd.DataFrame({"row_id": frame.row_id, "HR_existing": hr, "SR_existing": sr,
                             "ML_provisional": ml, "ml_score": score}, index=frame.index)
