"""One API, five length-specific calibrations, one reviewed normal reference.

Only the supplied batch is inspected during scoring. Later batches cannot revise
an earlier batch. Daily reference forecasts preserve clock phase across batches.
"""
from dataclasses import dataclass
from pathlib import Path
import argparse
import joblib
import numpy as np
import pandas as pd
from ..benchmark import CHANNELS
from ..inference import boolean
from ..models import Setting, forecast_context, components_from_residual, long_collection_gaps, feature_names
from ..persistent import PersistentDetector

HORIZONS = (1, 4, 8, 12, 24)
HOUR_NS = 3_600_000_000_000


def checked_hours(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value not in HORIZONS:
        raise ValueError(f"analysis_hours must be one of {HORIZONS}")
    return int(value)


def setting_for(hours, context_hours=672):
    hours = checked_hours(hours)
    return Setting(method="stl_pelt", context_hours=context_hours, period=24,
                   window=min(12, hours), energy_window=min(24, hours),
                   penalty_factor=2.0, min_segment=12, jump=3)


@dataclass(frozen=True)
class NormalReference:
    next_day_forecast: np.ndarray
    residual_scale: np.ndarray
    workload_beta: float
    work_center: float
    end_time: pd.Timestamp
    case_id: str
    max_forecast_hours: int = 336


def prepare_reference_arrays(x, observed, latency, setting, end_time, case_id):
    x = np.array(x, dtype=float, copy=True)
    if x.shape != (setting.context_hours, 3):
        raise ValueError(f"Normal reference requires exactly {setting.context_hours} hourly rows")
    x[~observed] = np.nan
    x[~latency, 1:] = np.nan
    forecast, scale, info = forecast_context(x, setting.period, setting)
    return NormalReference(forecast, scale, info["workload_beta"], info["work_center"],
                           pd.Timestamp(end_time), str(case_id))


def raw_residual(reference, x, observed, latency, offset_hours=0):
    """Pointwise reference difference; no monitoring interpolation/lookahead."""
    x = np.array(x, dtype=float, copy=True)
    if not isinstance(offset_hours, (int, np.integer)) or offset_hours < 0:
        raise ValueError("Monitoring must start at an integer hour after the reference")
    if offset_hours + len(x) > reference.max_forecast_hours:
        raise ValueError("Reference is older than the tested 14-day range; review/update the normal reference")
    predicted = reference.next_day_forecast[(offset_hours + np.arange(len(x))) % len(reference.next_day_forecast)]
    x[:, 1] -= reference.workload_beta * (x[:, 2] - reference.work_center)
    r = (x - predicted) / reference.residual_scale
    r[observed & ~latency, 1:] = 0.0
    r[~observed] = np.nan
    return r


def batch_components(residual, observed, latency, setting):
    """Fill within this batch only; long collection gaps split computations."""
    residual = np.array(residual, dtype=float, copy=True)
    observed, latency = np.asarray(observed, bool), np.asarray(latency, bool)
    output = np.full((len(residual), len(feature_names(setting))), np.nan)
    start = 0
    for a, b in [*long_collection_gaps(observed), (len(residual), len(residual))]:
        if a > start and observed[start:a].any():
            fragment = residual[start:a].copy()
            for j in range(fragment.shape[1]):
                good = np.isfinite(fragment[:, j])
                if not good.any():
                    raise ValueError("An observed batch channel has no finite measurements")
                fragment[:, j] = np.interp(np.arange(len(fragment)), np.flatnonzero(good), fragment[good, j])
            f, _ = components_from_residual(fragment, observed[start:a], latency[start:a], setting)
            output[start:a] = f
        start = b
    return output


def sequence_components(residual, observed, latency, hours, setting):
    """Disjoint batch replay. No future block and no monitoring-history carry."""
    hours = checked_hours(hours)
    if len(residual) % hours:
        raise ValueError("Monitoring sequence must contain a whole number of selected batches")
    return np.concatenate([batch_components(residual[i:i + hours], observed[i:i + hours],
                                            latency[i:i + hours], setting)
                           for i in range(0, len(residual), hours)])


def validated_frame(frame):
    required = {"case_id", "timestamp", *CHANNELS, "collection_observed", "latency_available"}
    if required.difference(frame.columns):
        raise ValueError(f"Missing columns: {sorted(required.difference(frame.columns))}")
    if frame.empty or frame.case_id.isna().any() or frame.case_id.nunique() != 1:
        raise ValueError("One non-null case_id (one Adapter+Process) is required")
    try:
        times = pd.DatetimeIndex(pd.to_datetime(frame.timestamp, format="mixed", errors="raise")).as_unit("ns")
    except (ValueError, TypeError) as exc:
        raise ValueError("Use valid timestamps with one consistent timezone") from exc
    if times.hasnans or not np.all(np.diff(times.asi8) == HOUR_NS):
        raise ValueError("Timestamps must be ordered, unique and exactly one hour apart")
    x = frame[list(CHANNELS)].apply(pd.to_numeric, errors="raise").to_numpy(float)
    observed = boolean(frame.collection_observed, "collection_observed")
    latency = boolean(frame.latency_available, "latency_available")
    if (latency & ~observed).any():
        raise ValueError("Latency unavailable when collection is missing")
    if not np.isfinite(x[observed, 0]).all() or not np.isfinite(x[latency, 1:]).all():
        raise ValueError("Observed channels must contain finite measurements")
    if (x[np.isfinite(x)] < 0).any() or (latency & (x[:, 0] == 0)).any():
        raise ValueError("Invalid log aggregate or latency at zero executions")
    return x, observed, latency, times, str(frame.case_id.iloc[0])


class IntervalModelBank:
    """Shared implementation, separate normal statistics/threshold per horizon."""
    def __init__(self, models, context_hours=672):
        if set(models) != set(HORIZONS):
            raise ValueError("All five calibrated horizons are required")
        if any(m.threshold is None for m in models.values()):
            raise ValueError("Every horizon needs its own calibrated threshold")
        self.models = dict(models)
        self.context_hours = int(context_hours)

    def prepare_reference(self, normal_history):
        x, observed, latency, times, case_id = validated_frame(normal_history)
        return prepare_reference_arrays(x, observed, latency, setting_for(24, self.context_hours), times[-1], case_id)

    def predict_batch(self, reference, batch, analysis_hours=4):
        hours = checked_hours(analysis_hours)
        x, observed, latency, times, case_id = validated_frame(batch)
        if len(x) != hours:
            raise ValueError(f"analysis_hours={hours} requires exactly {hours} monitoring rows, got {len(x)}")
        if case_id != reference.case_id:
            raise ValueError("Batch and normal reference belong to different case_id/groups")
        if str(times.tz) != str(reference.end_time.tz):
            raise ValueError("Reference and batch must use the same timezone")
        delta = times[0].value - reference.end_time.value - HOUR_NS
        if delta < 0 or delta % HOUR_NS:
            raise ValueError("Batch must follow the reference at a whole-hour offset")
        r = raw_residual(reference, x, observed, latency, delta // HOUR_NS)
        model = self.models[hours]
        components = batch_components(r, observed, latency, model.setting)
        score, reasons = model.score_components(components)
        return pd.DataFrame({"case_id": case_id, "timestamp": times,
            "analysis_hours": hours, "batch_start": times[0],
            "batch_end_exclusive": times[-1] + pd.Timedelta(hours=1),
            "available_at": times[-1] + pd.Timedelta(hours=1),
            "score": score, "threshold": model.threshold,
            "is_anomaly": np.isfinite(score) & (score > model.threshold),
            "assessable": np.isfinite(score), "reason": reasons, "retrospective": True})

    def record(self):
        return {"analysis_hours": list(HORIZONS), "normal_reference_hours": self.context_hours,
                "input_frequency_hours": 1, "model_per_group": False,
                "normal_statistics_and_thresholds_separate_per_horizon": True,
                "monitoring_added_to_normal_reference": False,
                "later_batches_used_for_earlier_scores": False,
                "models": {str(h): m.record() for h, m in self.models.items()}}


def score_frame(bank, frame, analysis_hours=4, split_batches=False):
    hours = checked_hours(analysis_hours)
    outputs = []
    if "case_id" not in frame:
        raise ValueError("Missing case_id")
    for _, part in frame.groupby("case_id", sort=False, dropna=False):
        validated_frame(part)
        if len(part) <= bank.context_hours:
            raise ValueError("Normal reference plus a monitoring batch are required")
        reference = bank.prepare_reference(part.iloc[:bank.context_hours])
        monitor = part.iloc[bank.context_hours:]
        if (not split_batches and len(monitor) != hours) or len(monitor) % hours:
            raise ValueError("Supply exactly one selected batch, or use split_batches=True for whole disjoint batches")
        for i in range(0, len(monitor), hours):
            outputs.append(bank.predict_batch(reference, monitor.iloc[i:i + hours], hours))
    if not outputs:
        raise ValueError("No input cases")
    return pd.concat(outputs, ignore_index=True)


def load_bank(path):
    # Pickle/joblib executes code; only load trusted local artifacts.
    bank = joblib.load(path)
    if not isinstance(bank, IntervalModelBank):
        raise TypeError("Expected an IntervalModelBank artifact")
    return bank


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--analysis-hours", type=int, choices=HORIZONS, required=True)
    p.add_argument("--split-batches", action="store_true")
    args = p.parse_args()
    if args.output.resolve() == args.input.resolve() or args.output.exists():
        raise FileExistsError("Do not overwrite an input or existing output")
    result = score_frame(load_bank(args.model), pd.read_csv(args.input), args.analysis_hours, args.split_batches)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"{args.analysis_hours}h analysis: {len(result)} hourly decisions", flush=True)


if __name__ == "__main__":
    main()
