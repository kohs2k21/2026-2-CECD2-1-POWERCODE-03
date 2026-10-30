"""Normal-only temporal evidence from one selected native five-minute window.

The caller supplies reviewed normal history and handles aggregation, timestamps,
threshold calibration and persistence. No monitoring samples update a reference.
The batched path reshapes disjoint windows; it never filters across their edges.
"""

from dataclasses import dataclass
from collections.abc import Mapping
import warnings

import numpy as np


HORIZONS = (1, 4, 8, 12, 24)
BINS_PER_HOUR = 12
BINS_PER_DAY = 288
REFERENCE_HOURS = 672
REFERENCE_BINS = REFERENCE_HOURS * BINS_PER_HOUR
MONITOR_HOURS = 336
MONITOR_BINS = MONITOR_HOURS * BINS_PER_HOUR
CHANNELS = ("count", "duration", "work")
STATISTICS = (
    "mean_up", "mean_down", "variability_up", "variability_down", "energy",
    "trend_up", "trend_down", "early_late_up", "early_late_down",
    "innovation_energy",
)
FEATURE_NAMES = tuple(f"{channel}:{stat}" for channel in CHANNELS for stat in STATISTICS)


def _readonly(value):
    # Immutable backing storage also prevents setflags(write=True), unlike a
    # writable owning array with only its WRITEABLE flag cleared.
    value = np.ascontiguousarray(value)
    return np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)


def _median(value, axis=None):
    # Entirely unavailable duration channels/phases are legitimate. Their
    # explicit masks, rather than imputation, decide whether features exist.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(value, axis=axis)


def _arrays(x, o, a):
    x = np.asarray(x, dtype=float)
    o, a = np.asarray(o), np.asarray(a)
    if x.ndim != 2 or x.shape[1] != 3 or not len(x):
        raise ValueError("Expected a nonempty [five-minute bin, 3] array")
    if o.shape != (len(x),) or a.shape != o.shape or o.dtype != bool or a.dtype != bool:
        raise ValueError("o and a must be boolean masks of shape [bin]")
    # A zero-arrival bin is still observed count evidence, but contains no
    # duration or per-process work measurement, even if a is accidentally true.
    duration = o & a & (x[:, 0] > 0)
    valid = np.column_stack((o, duration, duration))
    if not np.isfinite(x[valid]).all():
        raise ValueError("Observed, available channel values must be finite")
    return x, valid


def _integer(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return int(value)


@dataclass(frozen=True)
class FineReference:
    """Fixed, immutable normal parameters; phase zero is reference bin zero."""

    profile: np.ndarray
    center: np.ndarray
    scale: np.ndarray
    rho: np.ndarray
    slope: np.ndarray
    active: np.ndarray
    midpoint: float = (REFERENCE_BINS - 1) / 2
    reference_bins: int = REFERENCE_BINS

    def __post_init__(self):
        for name in ("profile", "center", "scale", "rho", "slope", "active"):
            object.__setattr__(self, name, _readonly(getattr(self, name)))

    def __reduce__(self):
        # Reconstruction through __init__ preserves immutable arrays after a
        # pickle/joblib round trip as well as during an in-process call.
        return type(self), (
            self.profile, self.center, self.scale, self.rho, self.slope,
            self.active, self.midpoint, self.reference_bins,
        )


class FineWindowModel:
    """Per-H normal feature distribution, independent of anomaly annotations.

    ``x`` contains logcount/logduration/logwork; ``o`` marks collection and
    ``a`` marks duration/work availability. Missing features are NaN, including
    a wholly unobserved window. Fit one model per selected H, using normal
    feature rows only. The model deliberately has no decision threshold.

    Weights may be a feature-name mapping or a length-F nonnegative vector.
    Zero disables a feature. ``normal_evidence`` exposes unweighted upper-tail
    evidence for calibration; score_features returns its weighted maximum by
    default, or a smooth maximum when temperature is positive.
    """

    feature_names = FEATURE_NAMES

    def __init__(self, weights=None, *, seasonal_shrink=4.0, linear_trend=True,
                 scale_floor=0.05, rho_cap=0.85, temperature=0.0):
        self.seasonal_shrink = float(seasonal_shrink)
        self.linear_trend = bool(linear_trend)
        self.scale_floor = float(scale_floor)
        self.rho_cap = float(rho_cap)
        self.temperature = float(temperature)
        if not np.isfinite([self.seasonal_shrink, self.scale_floor, self.rho_cap,
                            self.temperature]).all():
            raise ValueError("Configuration must be finite")
        if self.seasonal_shrink < 0 or self.scale_floor <= 0 or not 0 <= self.rho_cap < 1:
            raise ValueError("Invalid shrinkage, scale floor or rho cap")
        if self.temperature < 0:
            raise ValueError("temperature must be nonnegative")
        if isinstance(weights, Mapping):
            unknown = set(weights) - set(self.feature_names)
            if unknown:
                raise ValueError(f"Unknown feature weights: {sorted(unknown)}")
            weights = [weights.get(name, 1.0) for name in self.feature_names]
        self.weights = np.ones(len(self.feature_names)) if weights is None else np.array(weights, float, copy=True)
        self._checked_weights()

    def _checked_weights(self):
        weights = np.asarray(self.weights, float)
        if (weights.shape != (len(self.feature_names),) or not np.isfinite(weights).all()
                or (weights < 0).any() or not (weights > 0).any()):
            raise ValueError("Expected F finite, nonnegative weights with positive support")
        return weights

    def _profile(self, values):
        days = values.reshape(-1, BINS_PER_DAY, 3)
        base = np.nan_to_num(_median(values, axis=0), nan=0.0)
        phase_median = _median(days, axis=0)
        support = np.isfinite(days).sum(axis=0)
        strength = support / np.maximum(support + self.seasonal_shrink, 1.0)
        return base + strength * (np.where(support > 0, phase_median, base) - base)

    def _stable_slope(self, residual):
        daily = _median(residual.reshape(-1, BINS_PER_DAY, 3), axis=1)
        slope = np.zeros(3)
        if not self.linear_trend:
            return slope

        def estimate(t, y):
            left, right = np.triu_indices(len(t), 1)
            return float(np.median((y[right] - y[left]) / (t[right] - t[left])))

        # Only 28 daily summaries enter this robust fit. Requiring consistent
        # slopes in both halves prevents a single level step from being
        # extrapolated indefinitely as a normal linear trend.
        for channel in range(3):
            good = np.isfinite(daily[:, channel])
            t = np.flatnonzero(good)
            y = daily[good, channel]
            first, last = t < 14, t >= 14
            if first.sum() < 6 or last.sum() < 6:
                continue
            whole = estimate(t, y)
            early = estimate(t[first], y[first])
            late = estimate(t[last], y[last])
            noise = 1.4826 * np.median(np.abs(y - whole * t - np.median(y - whole * t)))
            consistent = early * late > 0 and whole * early > 0
            stable = max(abs(early), abs(late)) <= 2 * min(abs(early), abs(late))
            if consistent and stable and abs(whole) * (t[-1] - t[0]) > 3 * max(noise, self.scale_floor):
                slope[channel] = whole / BINS_PER_DAY
        return slope

    def prepare_reference(self, x, o, a):
        """Fit exactly 672 hours (8,064 bins) of reviewed normal history."""
        x, valid = _arrays(x, o, a)
        if len(x) != REFERENCE_BINS:
            raise ValueError("Normal reference requires exactly 672 hours / 8064 bins")
        if not valid[:, 0].any():
            raise ValueError("Normal reference requires observed count bins")
        values = np.where(valid, x, np.nan)
        phase = np.arange(REFERENCE_BINS) % BINS_PER_DAY
        midpoint = (REFERENCE_BINS - 1) / 2
        profile = self._profile(values)
        slope = self._stable_slope(values - profile[phase])
        detrended = values - (np.arange(REFERENCE_BINS) - midpoint)[:, None] * slope
        profile = self._profile(detrended)
        residual = detrended - profile[phase]
        active = valid.any(axis=0)
        center = np.nan_to_num(_median(residual, axis=0), nan=0.0)
        scale = np.maximum(np.nan_to_num(1.4826 * _median(np.abs(residual - center), axis=0), nan=1.0),
                           self.scale_floor)
        # Estimate normal persistence from genuinely adjacent observed bins.
        # Winsorization limits isolated normal outliers; no gap is bridged.
        z = np.clip((residual - center) / scale, -4, 4)
        rho = np.zeros(3)
        for channel in range(3):
            pairs = valid[:-1, channel] & valid[1:, channel]
            if pairs.sum() < 3:
                continue
            left, right = z[:-1, channel][pairs], z[1:, channel][pairs]
            left, right = left - left.mean(), right - right.mean()
            denominator = np.sqrt(np.dot(left, left) * np.dot(right, right))
            if denominator > 1e-12:
                rho[channel] = np.clip(np.dot(left, right) / denominator, 0, self.rho_cap)
        return FineReference(profile, center, scale, rho, slope, active)

    def window_features(self, reference, x, o, a, offset_bins=0):
        """Return F features from exactly one selected 12*H-bin window."""
        x = np.asarray(x)
        if x.ndim != 2 or len(x) not in tuple(h * BINS_PER_HOUR for h in HORIZONS):
            raise ValueError("Selected window requires exactly 12*H bins, H in (1,4,8,12,24)")
        return self.sequence_features(reference, x, o, a, len(x) // BINS_PER_HOUR, offset_bins)[0]

    def sequence_features(self, reference, x, o, a, analysis_hours, offset_bins=0):
        """Vectorized disjoint windows; 4,032 bins produce [336/H, F].

        Any positive whole number of windows is supported for training batches.
        offset_bins is the distance from the end of the fixed reference, not
        from the start of the current batch. It affects phase/trend only.
        """
        h = _integer(analysis_hours, "analysis_hours")
        offset = _integer(offset_bins, "offset_bins")
        if h not in HORIZONS:
            raise ValueError("analysis_hours must be one of (1,4,8,12,24)")
        if not isinstance(reference, FineReference):
            raise TypeError("Expected a FineReference from prepare_reference")
        x, valid = _arrays(x, o, a)
        width = h * BINS_PER_HOUR
        if len(x) % width:
            raise ValueError("Sequence must contain whole selected windows")
        # Bound temporary memory when fitting hundreds of thousands of windows;
        # the only batching loop is over large blocks, never individual windows.
        block = max(1, 65536 // width) * width
        parts = []
        for start in range(0, len(x), block):
            end = min(start + block, len(x))
            parts.append(self._window_block(reference, x[start:end], valid[start:end], width, offset + start))
        return np.concatenate(parts, axis=0)

    @staticmethod
    def _window_block(reference, x, valid, width, offset):
        time = REFERENCE_BINS + offset + np.arange(len(x))
        baseline = (reference.profile[time % BINS_PER_DAY] + reference.center
                    + (time - reference.midpoint)[:, None] * reference.slope)
        valid = valid & reference.active
        z = (np.where(valid, x, 0.0) - baseline) / reference.scale
        z = np.where(valid, z, 0.0).reshape(-1, width, 3)
        valid = valid.reshape(-1, width, 3)
        n = valid.sum(axis=1)
        denominator = np.maximum(n, 1)
        mean = z.sum(axis=1) / denominator
        second = np.square(z).sum(axis=1) / denominator
        std = np.sqrt(np.maximum(second - mean * mean, 0.0))
        # Conservative AR(1) effective sample sizes use normal rho only. Missing
        # bins are not compressed into a fictitious contiguous time series.
        effective = np.minimum(n, np.maximum(1, n * (1 - reference.rho) / (1 + reference.rho)))
        mean_stat = mean * np.sqrt(effective)

        t = np.linspace(-0.5, 0.5, width)[None, :, None]
        sum_t = (valid * t).sum(axis=1)
        spread = (valid * t * t).sum(axis=1) - sum_t * sum_t / denominator
        covariance = (z * t).sum(axis=1) - sum_t * mean
        trend = covariance / np.sqrt(np.maximum(spread, 1e-12)) * np.sqrt(effective / denominator)

        middle = width // 2
        early_n = valid[:, :middle].sum(axis=1)
        late_n = valid[:, middle:].sum(axis=1)
        early = z[:, :middle].sum(axis=1) / np.maximum(early_n, 1)
        late = z[:, middle:].sum(axis=1) / np.maximum(late_n, 1)
        early_late = (late - early) / np.sqrt(
            denominator / np.maximum(effective, 1)
            * (1 / np.maximum(early_n, 1) + 1 / np.maximum(late_n, 1)))

        # Difference only adjacent samples inside each selected window. In
        # particular, its first bin never reads the previous monitoring window.
        pair = valid[:, :-1] & valid[:, 1:]
        pair_n = pair.sum(axis=1)
        innovation = (z[:, 1:] - reference.rho * z[:, :-1]) / np.sqrt(1 - reference.rho ** 2)
        innovation_energy = np.sqrt(np.where(pair, innovation * innovation, 0).sum(axis=1)
                                    / np.maximum(pair_n, 1))
        result = np.stack((
            np.maximum(mean_stat, 0), np.maximum(-mean_stat, 0),
            np.where(n >= 2, np.maximum(std - 1, 0), np.nan),
            np.where(n >= 2, np.maximum(1 - std, 0), np.nan),
            np.sqrt(second),
            np.where(spread > 1e-12, np.maximum(trend, 0), np.nan),
            np.where(spread > 1e-12, np.maximum(-trend, 0), np.nan),
            np.where((early_n > 0) & (late_n > 0), np.maximum(early_late, 0), np.nan),
            np.where((early_n > 0) & (late_n > 0), np.maximum(-early_late, 0), np.nan),
            np.where(pair_n > 0, innovation_energy, np.nan),
        ), axis=-1)
        return np.where(n[:, :, None] > 0, result, np.nan).reshape(-1, len(FEATURE_NAMES))

    def _feature_matrix(self, features):
        features = np.asarray(features, float)
        if features.ndim == 1:
            features = features[None, :]
        if features.ndim != 2 or features.shape[1] != len(self.feature_names):
            raise ValueError("Expected a feature vector or [window, F] feature matrix")
        if np.isinf(features).any() or (features < 0).any():
            raise ValueError("Features must be nonnegative finite values or NaN")
        return features

    def fit(self, normal_featurematrix):
        """Fit empirical upper tails and extrapolation scales on normal rows."""
        features = self._feature_matrix(normal_featurematrix)
        if not np.isfinite(features).any():
            raise ValueError("At least one available normal feature is required")
        cdf, scales = [], []
        for column in features.T:
            values = np.sort(column[np.isfinite(column)])
            cdf.append(_readonly(values))
            if len(values):
                median, q90, q99 = np.quantile(values, [0.5, 0.9, 0.99])
                scales.append(max(q90 - median, q99 - q90, self.scale_floor))
            else:
                scales.append(self.scale_floor)
        self.normal_cdf_ = tuple(cdf)
        self.tail_scales_ = _readonly(np.asarray(scales))
        return self

    def normal_evidence(self, feature_matrix):
        """Return [window, F] unweighted -log upper-tail evidence; NaN disables.

        Left insertion counts all ties in the upper tail. Add-one smoothing
        avoids infinite evidence, and a normal-only scale extrapolates beyond
        the largest training value instead of saturating all larger changes.
        """
        if not hasattr(self, "normal_cdf_"):
            raise RuntimeError("fit(normal_featurematrix) is required before scoring")
        features = self._feature_matrix(feature_matrix)
        result = np.full(features.shape, np.nan)
        for j, values in enumerate(self.normal_cdf_):
            if not len(values):
                continue
            good = np.isfinite(features[:, j])
            query = features[good, j]
            rank = np.searchsorted(values, query, side="left")
            tail = (len(values) - rank + 1) / (len(values) + 1)
            result[good, j] = -np.log(tail) + np.maximum(query - values[-1], 0) / self.tail_scales_[j]
        return result

    def score_features(self, f):
        """Return one score per row; no available enabled evidence yields NaN."""
        evidence = self.normal_evidence(f)
        weights = self._checked_weights()
        if not np.isfinite(self.temperature) or self.temperature < 0:
            raise ValueError("temperature must be finite and nonnegative")
        active = np.isfinite(evidence) & (weights > 0)
        log_weight = np.full(weights.shape, -np.inf)
        np.log(weights, out=log_weight, where=weights > 0)
        weighted = np.where(active, evidence + log_weight, -np.inf)
        if self.temperature == 0:
            score = np.max(weighted, axis=1)
        else:
            # Normalized smooth max retains the same units as tail evidence.
            # This branch is optional; default scoring is exactly max(e+log w).
            numerator = np.logaddexp.reduce(weighted / self.temperature, axis=1)
            denominator = np.logaddexp.reduce(np.where(active, log_weight, -np.inf)
                                             / self.temperature, axis=1)
            with np.errstate(invalid="ignore"):
                score = self.temperature * (numerator - denominator)
        return np.where(active.any(axis=1), score, np.nan)
