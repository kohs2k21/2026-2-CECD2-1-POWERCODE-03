"""One native-resolution backend, five calibrated analysis-window lengths."""
from dataclasses import dataclass
import joblib
import numpy as np
import pandas as pd
from scipy.special import logsumexp

from ..intervals.model import checked_hours, HORIZONS
from ..inference import boolean
from .benchmark import CHANNELS

BIN_NS = pd.Timedelta(minutes=5).value


def combine_evidence(evidence, weights, temperature=0.):
    e = np.asarray(evidence, float)
    w = np.asarray(weights, float)
    if e.ndim != 2 or w.shape != (e.shape[1],) or not np.isfinite(w).all() or (w < 0).any() or not w.any():
        raise ValueError("Nonnegative finite weights matching the feature columns required")
    if not np.isfinite(temperature) or temperature < 0:
        raise ValueError("Nonnegative finite temperature required")
    with np.errstate(divide="ignore"):
        v = e + np.log(w)
    v[~np.isfinite(e)] = -np.inf
    if temperature == 0:
        score = v.max(axis=1)
    else:
        score = temperature * logsumexp(v / temperature, axis=1)
    score[~np.isfinite(v).any(axis=1)] = np.nan
    return score


def validated_frame(frame):
    required = {"case_id", "timestamp", *CHANNELS, "collection_observed", "latency_available"}
    if required - set(frame.columns):
        raise ValueError(f"Missing internal columns: {sorted(required - set(frame.columns))}")
    if frame.empty or frame.case_id.isna().any() or frame.case_id.nunique() != 1:
        raise ValueError("Exactly one non-null monitoring group is required")
    times = pd.DatetimeIndex(pd.to_datetime(frame.timestamp, format="mixed", errors="raise")).as_unit("ns")
    if times.hasnans or not (np.diff(times.asi8) == BIN_NS).all():
        raise ValueError("Internal bins must be ordered, unique and five minutes apart")
    x = frame[list(CHANNELS)].apply(pd.to_numeric, errors="raise").to_numpy(float)
    o = boolean(frame.collection_observed, "collection_observed")
    a = boolean(frame.latency_available, "latency_available")
    if (a & ~o).any() or not np.isfinite(x[o, 0]).all() or not np.isfinite(x[a, 1:]).all():
        raise ValueError("Invalid observed-channel values or masks")
    if (x[np.isfinite(x)] < 0).any() or (a & (x[:, 0] == 0)).any():
        raise ValueError("Negative log aggregates or latency at zero arrivals")
    return x, o, a, times, str(frame.case_id.iloc[0])


@dataclass(frozen=True)
class FineReference:
    latent: object
    end_time: pd.Timestamp
    case_id: str


class FineBank:
    context_hours = 672
    input_frequency_minutes = 5

    def __init__(self, models, settings, provenance=None):
        if set(models) != set(HORIZONS) or set(settings) != set(HORIZONS):
            raise ValueError("All five horizons and calibrations are required")
        self.models, self.settings = models, settings
        self.provenance = dict(provenance or {})
        for h in HORIZONS:
            cfg = settings[h]
            if not np.isfinite(cfg["threshold"]):
                raise ValueError("A frozen threshold is required for every horizon")

    def prepare_reference(self, normal_history):
        x, o, a, times, identity = validated_frame(normal_history)
        if len(x) != self.context_hours * 12:
            raise ValueError("The reviewed normal reference needs exactly 672 hours of five-minute positions")
        latent = self.models[1].prepare_reference(x, o, a)
        return FineReference(latent, times[-1] + pd.Timedelta(minutes=5), identity)

    def predict_batch(self, reference, batch, analysis_hours=4):
        h = checked_hours(analysis_hours)
        x, o, a, times, identity = validated_frame(batch)
        if len(x) != h * 12:
            raise ValueError(f"Exactly {h * 12} internally aggregated bins required")
        if identity != reference.case_id or str(times.tz) != str(reference.end_time.tz):
            raise ValueError("Normal reference and selected batch group/timezone differ")
        offset_ns = times[0].value - reference.end_time.value
        if offset_ns < 0 or offset_ns % BIN_NS or offset_ns // BIN_NS + len(x) > 336 * 12:
            raise ValueError("Selected window must follow the reference within the tested 14 days")
        model, cfg = self.models[h], self.settings[h]
        features = model.window_features(reference.latent, x, o, a, offset_bins=int(offset_ns // BIN_NS))
        evidence = model.normal_evidence(np.asarray(features).reshape(1, -1))
        score = combine_evidence(evidence, cfg["weights"], cfg["temperature"])[0]
        with np.errstate(divide="ignore"):
            contribution = evidence[0] + np.log(cfg["weights"])
        reason = model.feature_names[int(np.argmax(np.nan_to_num(contribution, nan=-np.inf)))] if np.isfinite(score) else "collection_missing"
        observed_hours = o.reshape(h, 12).all(axis=1)
        scores = np.where(observed_hours, score, np.nan)
        return pd.DataFrame({"case_id": identity, "timestamp": times[::12], "analysis_hours": h,
            "batch_start": times[0], "batch_end_exclusive": times[-1] + pd.Timedelta(minutes=5),
            "available_at": times[-1] + pd.Timedelta(minutes=5), "score": scores,
            "threshold": cfg["threshold"], "is_anomaly": np.isfinite(scores) & (scores > cfg["threshold"]),
            "assessable": np.isfinite(scores), "reason": np.where(observed_hours, reason, "collection_missing"),
            "retrospective": True, "score_scope": "selected_batch", "monitoring_state_carried": False,
            "model_type": "native_five_minute_window_normal_evidence"})

    def record(self):
        return {"analysis_hours": list(HORIZONS), "internal_bin_minutes": 5,
            "normal_reference_hours": self.context_hours, "external_process_schema_changed": False,
            "outside_selected_window_monitoring_used": False, "monitoring_state_carried": False,
            "normal_reference_auto_updated": False, "point_adjustment": False,
            "window_score_repeated_over_original_hourly_evaluation_positions": True,
            "settings": self.settings, "provenance": self.provenance}


def load_bank(path):
    value = joblib.load(path)  # Trusted artifacts only: joblib can execute code.
    if not isinstance(value, FineBank):
        raise TypeError("Expected a native five-minute FineBank artifact")
    return value
