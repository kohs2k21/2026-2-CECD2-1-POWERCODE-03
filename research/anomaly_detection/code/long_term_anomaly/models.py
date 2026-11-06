"""Interpretable batch time-series detectors, trained without anomaly labels.

STL is fit to the known/reference normal context, NEVER to the monitoring data:
otherwise it can absorb a lasting anomaly into its fitted trend. PELT uses the
unlabeled monitoring residuals retrospectively; no alarm propagation/point
adjustment and no ground-truth event boundaries are used.
"""
from dataclasses import dataclass, asdict
import numpy as np
from scipy.ndimage import uniform_filter1d
from statsmodels.tsa.seasonal import STL
import ruptures as rpt

CHANNEL_NAMES = ("requests", "duration", "items_per_process")


def fill_short_gaps(values, maximum=6):
    values = np.array(values, dtype=float, copy=True)
    for j in range(values.shape[1]):
        good = np.isfinite(values[:, j])
        if not good.any():
            raise ValueError("A channel has no observations")
        missing = ~good
        # Fail explicitly on long collection gaps instead of inventing continuity.
        bounds = np.flatnonzero(np.diff(np.r_[False, missing, False]))
        if any(b - a > maximum for a, b in bounds.reshape(-1, 2)):
            raise ValueError("Long missing interval; split batch or supply collection coverage")
        values[:, j] = np.interp(np.arange(len(values)), np.flatnonzero(good), values[good, j])
    return values


def rolling_mean(values, width):
    # Centered, offline statistics. Edge denominators are the number of actual
    # observations; reflection/padding does not create fictitious values.
    num = uniform_filter1d(values, size=width, axis=0, mode="constant", cval=0) * width
    den = uniform_filter1d(np.ones_like(values), size=width, axis=0, mode="constant", cval=0) * width
    return num / np.maximum(den, 1)


def long_collection_gaps(observed, maximum=6):
    bounds = np.flatnonzero(np.diff(np.r_[False, ~np.asarray(observed, bool), False])).reshape(-1, 2)
    return [(int(a), int(b)) for a, b in bounds if b - a > maximum]


@dataclass(frozen=True)
class Setting:
    method: str = "stl_pelt"
    context_hours: int = 672
    period: int = 24
    window: int = 12
    energy_window: int = 24
    penalty_factor: float = 2.0
    min_segment: int = 12
    jump: int = 3
    workload_conditioning: bool = True


def forecast_context(context, horizon, setting):
    c = fill_short_gaps(context)
    n, d = c.shape
    if n != setting.context_hours or n < 4 * setting.period:
        raise ValueError("Incorrect normal-reference context length")
    if setting.method not in ("seasonal_naive", "stl_window", "stl_pelt"):
        raise ValueError("Unknown method")
    beta = 0.0
    work_center = np.median(c[:, 2])
    if setting.workload_conditioning and np.std(c[:, 2]) > .04:
        work = c[:, 2] - work_center
        dur = c[:, 1] - np.median(c[:, 1])
        # Ridge-regularized one-variable context relation. This coefficient is
        # normal-context learned, not a rule claiming a particular ID's behavior.
        beta = float(np.clip(np.dot(work, dur) / (np.dot(work, work) + 5), -2, 2))
        c[:, 1] -= beta * work
    predicted, residual, seasonals, trends = [], [], [], []
    for j in range(d):
        if setting.method == "seasonal_naive":
            hour = np.arange(n) % setting.period
            seasonal = np.asarray([np.median(c[hour == h, j]) for h in range(setting.period)])
            fit = seasonal[hour]
            pred = seasonal[(n + np.arange(horizon)) % setting.period]
            s = fit - np.median(fit)
            trend = np.full(n, np.median(fit))
        else:
            result = STL(c[:, j], period=setting.period, seasonal=13,
                         trend=2 * setting.period + 1, robust=True,
                         seasonal_jump=2, trend_jump=2).fit()
            s, trend = np.asarray(result.seasonal), np.asarray(result.trend)
            last = min(n, setting.period * 7)
            hour = np.arange(n) % setting.period
            seasonal = np.asarray([np.median(s[-last:][hour[-last:] == h]) for h in range(setting.period)])
            # A skewed, bursty innovation distribution has different median and
            # mean. Long-window mean shifts must be compared to the normal MEAN,
            # not the median of an aggressively smoothed, noisy tail trend.
            deseasonalized = c[:, j] - s
            low, high = np.quantile(deseasonalized[-168:], [.005, .995])
            level = float(np.mean(np.clip(deseasonalized[-168:], low, high)))
            pred = level + seasonal[(n + np.arange(horizon)) % setting.period]
            # Keep normal low-frequency variation in the uncertainty estimate.
            # Fitted STL trend+seasonal residual MAD can be much too small for
            # strongly autocorrelated/heavy-tailed logs and spuriously rescale
            # ordinary future fluctuations into huge anomaly residuals.
            fit = np.mean(deseasonalized) + s
        residual.append(c[:, j] - fit)
        predicted.append(pred); seasonals.append(s); trends.append(trend)
    residual = np.column_stack(residual)
    lo, hi = np.quantile(residual, [.005, .995], axis=0)
    scale = np.maximum(np.std(np.clip(residual, lo, hi), axis=0), [.03, .03, .02])
    return np.column_stack(predicted), scale, {
        "workload_beta": beta, "work_center": float(work_center),
        "context_seasonal": np.column_stack(seasonals), "context_trend": np.column_stack(trends)}


def prepare_case(x, observed, latency_available, setting):
    x = np.asarray(x, float)
    observed = np.asarray(observed, bool)
    latency_available = np.asarray(latency_available, bool)
    c = setting.context_hours
    if x.ndim != 2 or x.shape[1] != 3 or len(x) <= c:
        raise ValueError("Expected [hours,3] with normal context and monitoring horizon")
    if observed.shape != (len(x),) or latency_available.shape != (len(x),):
        raise ValueError("Invalid observation masks")
    if (latency_available & ~observed).any():
        raise ValueError("Latency cannot be available when collection is missing")
    context = x[:c].copy()
    context[~observed[:c]] = np.nan
    # Long periods with zero executions cannot train a latency channel; explicit
    # abstention is safer than filling a week's nonexistent durations.
    context[~latency_available[:c], 1:] = np.nan
    pred, scale, info = forecast_context(context, len(x) - c, setting)
    monitor = x[c:].copy()
    monitor[~observed[c:]] = np.nan
    latency = latency_available[c:]
    beta = info["workload_beta"]
    if beta:
        monitor[:, 1] -= beta * (monitor[:, 2] - info["work_center"])
    residual = (monitor - pred) / scale
    # No latency at zero arrivals is not duration=0 and not a latency failure.
    residual[~latency, 1:] = 0.0
    # Long monitoring gaps are not interpolated as operating history. Set a
    # temporary placeholder only; components_from_residual splits at these gaps
    # before any rolling statistics or PELT can use it. Context gaps still fail
    # explicitly because an unobserved normal reference cannot be certified.
    for a, b in long_collection_gaps(observed[c:]):
        residual[a:b] = 0.0
    residual = fill_short_gaps(residual)
    return residual, pred, scale, info


def feature_names(setting):
    blocks = ("local_level", "local_energy")
    if setting.method == "stl_pelt":
        blocks += ("segment_level", "segment_energy")
    return [f"{b}:{c}" for b in blocks for c in CHANNEL_NAMES]


def case_components(x, observed, latency_available, setting):
    residual, predicted, scale, info = prepare_case(x, observed, latency_available, setting)
    components, breaks = components_from_residual(
        residual, observed[setting.context_hours:], latency_available[setting.context_hours:], setting)
    return components, {"breakpoints_monitor_hour": breaks, "residual": residual,
                        "forecast": predicted, "residual_scale": scale, **info}


def components_from_residual(residual, observed, latency_available, setting):
    gaps = long_collection_gaps(observed)
    if gaps:
        result = np.full((len(residual), len(feature_names(setting))), np.nan)
        breaks, start = [], 0
        for a, b in [*gaps, (len(residual), len(residual))]:
            if a > start:
                fragment, ends = components_from_residual(residual[start:a], observed[start:a],
                                                          latency_available[start:a], setting)
                result[start:a] = fragment
                breaks.extend(start + end for end in ends)
            start = b
        return result, breaks
    r = np.clip(residual, -20, 20)
    n, d = r.shape
    width = 1 if setting.method == "seasonal_naive" else setting.window
    energy_width = 1 if setting.method == "seasonal_naive" else setting.energy_window
    parts = [abs(rolling_mean(r, width)), np.sqrt(np.maximum(rolling_mean(r * r, energy_width), 0))]
    breaks = [n]
    if setting.method == "stl_pelt":
        # Joint mean and energy changes; multivariate L2 PELT with a penalized
        # number of segments. Times are inferred from observations, not labels.
        signal = np.column_stack([np.clip(r, -6, 6), np.clip((r * r - 1) / 3, -1, 8)])
        penalty = setting.penalty_factor * signal.shape[1] * np.log(n)
        if n >= setting.min_segment:
            breaks = rpt.Pelt(model="l2", min_size=setting.min_segment,
                              jump=setting.jump).fit(signal).predict(pen=penalty)
        level, energy = np.empty_like(r), np.empty_like(r)
        start = 0
        for end in breaks:
            level[start:end] = abs(np.mean(r[start:end], axis=0))
            energy[start:end] = np.sqrt(np.mean(r[start:end] ** 2, axis=0))
            start = end
        parts += [level, energy]
    components = np.column_stack(parts)
    # Only collection-observed hours are assessable. They remain NaN and cannot
    # silently count as true negatives in metric calculation.
    valid = np.asarray(observed, bool)
    components[~valid] = np.nan
    # Latency/workload components do not raise an alarm at zero requests.
    for block in range(len(parts)):
        indices = np.flatnonzero(~latency_available & valid)
        components[indices, block * d + 1] = 0
        components[indices, block * d + 2] = 0
    return components, breaks


class LongTermDetector:
    """Serializable batch scorer; no truth/metadata files or I/O at inference."""
    def __init__(self, setting, energy_weight=1.0):
        self.setting = setting
        self.energy_weight = float(energy_weight)
        self.threshold = None

    def fit_reference(self, components):
        f = np.concatenate(components)
        self.center_ = np.nanmedian(f, axis=0)
        self.spread_ = np.maximum(np.nanquantile(f, .95, axis=0) - self.center_, .05)
        return self

    def score_components(self, components):
        if not hasattr(self, "center_"):
            raise RuntimeError("Fit normal reference first")
        values = (components - self.center_) / self.spread_
        # Work volume is a conditioning covariate, not itself an anomaly target:
        # legitimate large batches must not dominate the shared alert threshold.
        weights = np.asarray([0.0 if s.endswith(":items_per_process") else
                              self.energy_weight if "energy" in s else 1.0
                              for s in feature_names(self.setting)])
        values = values * weights
        valid = np.isfinite(values).any(axis=1)
        score = np.full(len(components), np.nan)
        reason = np.full(len(components), "collection_missing", dtype=object)
        index = np.argmax(np.nan_to_num(values[valid], nan=-np.inf), axis=1)
        score[valid] = values[valid, index]
        reason[valid] = np.asarray(feature_names(self.setting), dtype=object)[index]
        return score, reason

    def predict(self, x, collection_observed, latency_available):
        if self.threshold is None:
            raise RuntimeError("Threshold has not been calibrated")
        components, details = case_components(x, collection_observed, latency_available, self.setting)
        score, reason = self.score_components(components)
        return {"score": score, "is_anomaly": np.isfinite(score) & (score > self.threshold),
                "assessable": np.isfinite(score), "reason": reason,
                "threshold": self.threshold, "details": details}

    def record(self):
        return {"setting": asdict(self.setting), "energy_weight": self.energy_weight,
                "threshold": self.threshold, "normal_component_center": self.center_.tolist(),
                "normal_component_spread": self.spread_.tolist(),
                "feature_names": feature_names(self.setting),
                "retrospective": True, "point_adjustment": False,
                "normal_reference_context_required": True}
