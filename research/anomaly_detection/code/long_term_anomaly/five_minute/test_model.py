"""Tests for the native-bin model only; no benchmark inputs or truth are read."""

import pickle
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from .model import (
    BINS_PER_DAY, BINS_PER_HOUR, FEATURE_NAMES, HORIZONS, MONITOR_BINS,
    REFERENCE_BINS, FineReference, FineWindowModel,
)


def sample(n, seed=17):
    rng = np.random.default_rng(seed)
    phase = np.arange(n) * (2 * np.pi / BINS_PER_DAY)
    seasonal = np.column_stack((0.25 * np.sin(phase), 0.18 * np.cos(phase), 0.12 * np.sin(phase)))
    x = np.array([4.0, 5.0, 2.0]) + seasonal + rng.normal(0, 0.15, (n, 3))
    return x, np.ones(n, bool), np.ones(n, bool)


@pytest.fixture(scope="module")
def prepared():
    model = FineWindowModel()
    x, o, a = sample(REFERENCE_BINS)
    return model, model.prepare_reference(x, o, a)


def scalar_features(reference, x, o, a, offset):
    """Independent scalar oracle: no batched feature implementation is called."""
    columns = []
    width = len(x)
    time = REFERENCE_BINS + offset + np.arange(width)
    baseline = (reference.profile[time % BINS_PER_DAY] + reference.center
                + (time - reference.midpoint)[:, None] * reference.slope)
    for channel in range(3):
        valid = o.copy() if channel == 0 else o & a & (x[:, 0] > 0)
        valid &= reference.active[channel]
        n = int(valid.sum())
        if not n:
            columns.extend([np.nan] * 10)
            continue
        z = np.zeros(width)
        z[valid] = (x[valid, channel] - baseline[valid, channel]) / reference.scale[channel]
        effective = min(n, max(1, n * (1 - reference.rho[channel]) / (1 + reference.rho[channel])))
        mean = np.mean(z[valid]) * np.sqrt(effective)
        std = np.std(z[valid])
        t = np.linspace(-0.5, 0.5, width)[valid]
        centered_t = t - t.mean()
        spread = np.dot(centered_t, centered_t)
        trend = (np.dot(z[valid], centered_t) / np.sqrt(spread) * np.sqrt(effective / n)
                 if spread > 1e-12 else np.nan)
        early, late = valid.copy(), valid.copy()
        early[width // 2:] = False
        late[:width // 2] = False
        if early.any() and late.any():
            early_late = ((np.mean(z[late]) - np.mean(z[early]))
                          / np.sqrt(n / effective * (1 / early.sum() + 1 / late.sum())))
        else:
            early_late = np.nan
        pairs = valid[:-1] & valid[1:]
        if pairs.any():
            innovation = ((z[1:][pairs] - reference.rho[channel] * z[:-1][pairs])
                          / np.sqrt(1 - reference.rho[channel] ** 2))
            energy = np.sqrt(np.mean(innovation ** 2))
        else:
            energy = np.nan
        columns.extend((
            max(mean, 0), max(-mean, 0),
            max(std - 1, 0) if n >= 2 else np.nan,
            max(1 - std, 0) if n >= 2 else np.nan,
            np.sqrt(np.mean(z[valid] ** 2)),
            np.maximum(trend, 0), np.maximum(-trend, 0),
            np.maximum(early_late, 0), np.maximum(-early_late, 0), energy,
        ))
    return np.asarray(columns)


@pytest.mark.parametrize("h", HORIZONS)
def test_vectorized_parity_with_standalone_and_scalar_oracle(prepared, h):
    model, reference = prepared
    x, o, a = sample(MONITOR_BINS, seed=28)
    o[::17] = False
    a[::7] = False
    x[::23, 0] = 0
    x[~o] = np.nan
    x[~a, 1:] = np.nan
    offset = 37
    width = h * BINS_PER_HOUR
    actual = model.sequence_features(reference, x, o, a, h, offset)
    assert actual.shape == (336 // h, len(model.feature_names))
    for i in range(len(actual)):
        start, end = i * width, (i + 1) * width
        standalone = model.window_features(reference, x[start:end], o[start:end], a[start:end], offset + start)
        expected = scalar_features(reference, x[start:end], o[start:end], a[start:end], offset + start)
        np.testing.assert_allclose(actual[i], standalone, atol=2e-12, rtol=2e-12, equal_nan=True)
        np.testing.assert_allclose(actual[i], expected, atol=2e-12, rtol=2e-12, equal_nan=True)


@pytest.mark.parametrize("h", HORIZONS)
def test_full_window_isolation_including_masks_and_scores(prepared, h):
    model, reference = prepared
    x, o, a = sample(MONITOR_BINS, seed=64)
    width = h * BINS_PER_HOUR
    index = 3
    selected = slice(index * width, (index + 1) * width)
    before = model.sequence_features(reference, x, o, a, h, 11)
    scorer = FineWindowModel().fit(before)
    scores = scorer.score_features(before)
    changed, changed_o, changed_a = x.copy(), o.copy(), a.copy()
    outside = np.ones(MONITOR_BINS, bool)
    outside[selected] = False
    changed[outside] = np.nan
    changed_o[outside] = False
    changed_a[outside] = False
    after = model.sequence_features(reference, changed, changed_o, changed_a, h, 11)
    np.testing.assert_array_equal(before[index], after[index])
    assert scores[index] == scorer.score_features(after)[index]
    assert np.isnan(after[outside.reshape(-1, width).all(axis=1)]).all()


def test_innovations_do_not_cross_windows_or_collection_gaps(prepared):
    model, reference = prepared
    x, o, a = sample(24)
    features = model.sequence_features(reference, x, o, a, 1)
    x[11] += 100
    changed = model.sequence_features(reference, x, o, a, 1)
    np.testing.assert_array_equal(features[1], changed[1])
    # Alternating collection leaves no valid adjacent pair, although there are
    # six valid samples. Compressing those samples would fabricate innovations.
    o[1::2] = False
    f = model.window_features(reference, x[:12], o[:12], a[:12])
    assert np.isnan(f[[9, 19, 29]]).all()
    assert np.isfinite(f[[0, 10, 20]]).all()


def test_masks_ignore_arbitrary_unavailable_values_and_zero_arrivals(prepared):
    model, reference = prepared
    x, o, a = sample(48)
    o[::4] = False
    a[::3] = False
    x[1::5, 0] = 0
    before = model.window_features(reference, x, o, a)
    changed = x.copy()
    changed[~o] = np.inf
    no_duration = ~o | ~a | (x[:, 0] <= 0)
    changed[no_duration, 1:] = np.nan
    after = model.window_features(reference, changed, o, a)
    np.testing.assert_array_equal(before, after)
    x[:, 0] = 0
    x[:, 1:] = np.nan
    zero = model.window_features(reference, x, np.ones(48, bool), np.ones(48, bool))
    assert np.isfinite(zero[:10]).all()
    assert np.isnan(zero[10:]).all()
    missing = model.window_features(reference, np.full((48, 3), np.nan), np.zeros(48, bool), a)
    assert np.isnan(missing).all()
    scorer = FineWindowModel().fit(before)
    assert np.isnan(scorer.score_features(missing)[0])
    assert np.isfinite(scorer.score_features(zero)[0])


def test_partial_duration_and_single_sample_features(prepared):
    model, reference = prepared
    x, o, a = sample(12)
    a[:] = False
    a[2] = True
    f = model.window_features(reference, x, o, a)
    for start in (10, 20):
        assert np.isfinite(f[start + np.array([0, 1, 4])]).all()
        assert np.isnan(f[start + np.array([2, 3, 5, 6, 7, 8, 9])]).all()
    assert np.isfinite(f[:10]).all()


def test_reference_masks_do_not_impute_or_bridge_missing_channels():
    model = FineWindowModel()
    x, o, a = sample(REFERENCE_BINS)
    o[::5] = False
    a[::3] = False
    original = model.prepare_reference(x, o, a)
    changed = x.copy()
    changed[~o] = -np.inf
    changed[~a, 1:] = np.nan
    repeated = model.prepare_reference(changed, o, a)
    for name in ("profile", "center", "scale", "rho", "slope", "active"):
        np.testing.assert_array_equal(getattr(original, name), getattr(repeated, name))
    unavailable = model.prepare_reference(x, o, np.zeros(REFERENCE_BINS, bool))
    assert unavailable.active.tolist() == [True, False, False]
    query, qo, qa = sample(12)
    assert np.isnan(model.window_features(unavailable, query, qo, qa)[10:]).all()


def test_reference_and_inputs_remain_readonly(prepared):
    model, reference = prepared
    history = sample(REFERENCE_BINS)
    query = sample(MONITOR_BINS, seed=33)
    snapshots = [v.copy() for v in (*history, *query)]
    for value in (*history, *query):
        value.setflags(write=False)
    prepared_again = model.prepare_reference(*history)
    before = pickle.dumps(reference)
    f = model.sequence_features(reference, *query, analysis_hours=1)
    trained = FineWindowModel().fit(f)
    trained.normal_evidence(f)
    trained.score_features(f)
    assert pickle.dumps(reference) == before
    for value, saved in zip((*history, *query), snapshots):
        np.testing.assert_array_equal(value, saved)
    for ref in (reference, prepared_again, pickle.loads(before)):
        for name in ("profile", "center", "scale", "rho", "slope", "active"):
            array = getattr(ref, name)
            assert not array.flags.writeable
            with pytest.raises(ValueError):
                array.setflags(write=True)
            with pytest.raises(ValueError):
                array.flat[0] = 1
        with pytest.raises(FrozenInstanceError):
            ref.reference_bins = 0


def test_seasonal_phase_shrinkage_and_monitor_offset():
    phase = np.arange(REFERENCE_BINS) * (2 * np.pi / BINS_PER_DAY)
    x = np.array([4.0, 5.0, 2.0]) + np.sin(phase[:, None]) * np.array([0.4, 0.2, 0.1])
    mask = np.ones(REFERENCE_BINS, bool)
    plain = FineWindowModel(seasonal_shrink=0, linear_trend=False)
    reference = plain.prepare_reference(x, mask, mask)
    np.testing.assert_allclose(reference.profile, x[:BINS_PER_DAY], atol=2e-14, rtol=0)
    shrunk = FineWindowModel(seasonal_shrink=4, linear_trend=False).prepare_reference(x, mask, mask)
    base = np.median(x, axis=0)
    np.testing.assert_allclose(shrunk.profile, base + (reference.profile - base) * 28 / 32, atol=2e-14)
    offset = 73
    time = REFERENCE_BINS + offset + np.arange(48)
    query = np.array([4.0, 5.0, 2.0]) + np.sin(time[:, None] * (2 * np.pi / BINS_PER_DAY)) * np.array([0.4, 0.2, 0.1])
    features = plain.window_features(reference, query, mask[:48], mask[:48], offset)
    assert np.max(features[[0, 1, 4, 10, 11, 14, 20, 21, 24]]) < 1e-10
    wrong = plain.window_features(reference, query, mask[:48], mask[:48], 0)
    assert wrong[4] > 1
    # Entirely missing phases fall back to the global median, not NaN.
    sparse = mask.copy()
    sparse[17::BINS_PER_DAY] = False
    fallback = plain.prepare_reference(x, sparse, sparse)
    np.testing.assert_allclose(fallback.profile[17], np.median(x[sparse], axis=0))


def test_optional_stable_trend_and_step_rejection():
    model = FineWindowModel(seasonal_shrink=0, linear_trend=True)
    slopes = np.array([0.0001, 0.0002, 0.00005])
    time = np.arange(REFERENCE_BINS)
    x = np.array([4.0, 5.0, 2.0]) + time[:, None] * slopes
    mask = np.ones(REFERENCE_BINS, bool)
    ref = model.prepare_reference(x, mask, mask)
    np.testing.assert_allclose(ref.slope, slopes, atol=1e-15)
    query = np.array([4.0, 5.0, 2.0]) + (REFERENCE_BINS + 109 + np.arange(12))[:, None] * slopes
    f = model.window_features(ref, query, mask[:12], mask[:12], 109)
    assert np.max(f[[0, 1, 4, 10, 11, 14, 20, 21, 24]]) < 1e-10
    disabled = FineWindowModel(linear_trend=False).prepare_reference(x, mask, mask)
    np.testing.assert_array_equal(disabled.slope, np.zeros(3))
    x = np.full((REFERENCE_BINS, 3), 4.0)
    x[REFERENCE_BINS // 2:] += 1
    step = model.prepare_reference(x, mask, mask)
    np.testing.assert_array_equal(step.slope, np.zeros(3))


def test_normal_rho_is_capped_and_uses_adjacent_observed_bins():
    rng = np.random.default_rng(91)
    process = np.zeros((REFERENCE_BINS, 3))
    noise = rng.normal(0, 0.02, process.shape)
    for i in range(1, REFERENCE_BINS):
        process[i] = 0.98 * process[i - 1] + noise[i]
    x = np.array([4.0, 5.0, 2.0]) + process
    mask = np.ones(REFERENCE_BINS, bool)
    model = FineWindowModel(linear_trend=False)
    ref = model.prepare_reference(x, mask, mask)
    np.testing.assert_allclose(ref.rho, 0.85)
    mask[1::2] = False
    ref = model.prepare_reference(x, mask, mask)
    np.testing.assert_array_equal(ref.rho, np.zeros(3))


def test_feature_directions_and_fixed_reference(prepared):
    model, ref = prepared
    x, o, a = sample(288)
    ordinary = model.window_features(ref, x, o, a)
    up = x.copy()
    up[:, 0] += 1
    down = x.copy()
    down[:, 1] -= 1
    ramp = x.copy()
    ramp[:, 0] += np.linspace(-0.8, 0.8, len(x))
    volatile = x.copy()
    volatile[:, 0] += np.where(np.arange(len(x)) % 2, 1, -1)
    assert model.window_features(ref, up, o, a)[0] > ordinary[0] + 5
    assert model.window_features(ref, down, o, a)[11] > ordinary[11] + 5
    assert model.window_features(ref, ramp, o, a)[5] > ordinary[5] + 5
    assert model.window_features(ref, ramp, o, a)[7] > ordinary[7] + 5
    assert model.window_features(ref, volatile, o, a)[9] > ordinary[9] + 2


def test_normal_evidence_empirical_ties_tail_scale_and_nan():
    normal = np.tile(np.array([0.0, 0.0, 1.0, 2.0])[:, None], (1, len(FEATURE_NAMES)))
    normal[:, -1] = np.nan
    saved = normal.copy()
    model = FineWindowModel().fit(normal)
    query = np.tile(np.array([0.0, 1.0, 2.0, 3.0])[:, None], (1, len(FEATURE_NAMES)))
    query[1, 0] = np.nan
    evidence = model.normal_evidence(query)
    assert evidence.shape == query.shape
    median, q90, q99 = np.quantile([0, 0, 1, 2], [0.5, 0.9, 0.99])
    scale = max(q90 - median, q99 - q90, model.scale_floor)
    np.testing.assert_allclose(evidence[:, 1], [0, -np.log(3 / 5), -np.log(2 / 5), np.log(5) + 1 / scale])
    assert np.isnan(evidence[1, 0])
    assert np.isnan(evidence[:, -1]).all()
    assert model.normal_evidence(query[0]).shape == (1, len(FEATURE_NAMES))
    np.testing.assert_array_equal(normal, saved)
    assert all(not column.flags.writeable for column in model.normal_cdf_)
    assert not model.tail_scales_.flags.writeable
    assert np.isfinite(model.tail_scales_).all() and (model.tail_scales_ > 0).all()


def test_score_weights_zero_support_temperature_and_serialization(prepared):
    extractor, reference = prepared
    normal = extractor.sequence_features(reference, *sample(MONITOR_BINS), analysis_hours=1)
    query = normal[:3].copy()
    query[1, 0] += 5
    query[2] = np.nan
    model = FineWindowModel().fit(normal)
    e = model.normal_evidence(query)
    np.testing.assert_allclose(model.score_features(query[:2]), np.max(e[:2], axis=1))
    assert model.score_features(query[0]).shape == (1,)
    assert np.isnan(model.score_features(query)[2])
    model.weights[:] = 0
    model.weights[0] = 2
    np.testing.assert_allclose(model.score_features(query[:2]), e[:2, 0] + np.log(2))
    model.temperature = 0.2
    np.testing.assert_allclose(model.score_features(query[:2]), e[:2, 0])
    assert np.isnan(model.score_features(query)[2])
    model.weights[:] = 1
    expected = 0.2 * (np.logaddexp.reduce(e[:2] / 0.2, axis=1) - np.log(len(FEATURE_NAMES)))
    np.testing.assert_allclose(model.score_features(query[:2]), expected)
    loaded = pickle.loads(pickle.dumps(model))
    np.testing.assert_array_equal(model.normal_evidence(query), loaded.normal_evidence(query))
    np.testing.assert_array_equal(model.score_features(query), loaded.score_features(query))
    mapped = FineWindowModel({FEATURE_NAMES[0]: 0.0})
    assert mapped.weights[0] == 0 and np.all(mapped.weights[1:] == 1)


@pytest.mark.parametrize("length", [288, 4032, 8063, 8065])
def test_reference_requires_exactly_672_hours(length):
    with pytest.raises(ValueError, match="672 hours / 8064"):
        FineWindowModel().prepare_reference(*sample(length))


def test_input_validation(prepared):
    model, reference = prepared
    x, o, a = sample(12)
    for invalid in (0, 2, 3, 13, 24.0, True):
        with pytest.raises(ValueError):
            model.sequence_features(reference, x, o, a, invalid)
    for invalid in (-1, 1.5, True):
        with pytest.raises(ValueError):
            model.window_features(reference, x, o, a, invalid)
    with pytest.raises(ValueError):
        model.window_features(reference, x[:-1], o[:-1], a[:-1])
    with pytest.raises(ValueError, match="whole"):
        model.sequence_features(reference, *sample(13), analysis_hours=1)
    with pytest.raises(ValueError, match="boolean"):
        model.window_features(reference, x, o.astype(int), a)
    with pytest.raises(ValueError, match="boolean"):
        model.window_features(reference, x, o[:-1], a)
    with pytest.raises(TypeError):
        model.window_features(object(), x, o, a)
    broken = x.copy()
    broken[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        model.window_features(reference, broken, o, a)
    with pytest.raises(ValueError, match="observed count"):
        model.prepare_reference(*sample(REFERENCE_BINS)[:1], np.zeros(REFERENCE_BINS, bool), np.zeros(REFERENCE_BINS, bool))
    with pytest.raises(RuntimeError, match="fit"):
        FineWindowModel().normal_evidence(np.zeros(len(FEATURE_NAMES)))
    with pytest.raises(ValueError):
        FineWindowModel().fit(np.full((2, len(FEATURE_NAMES)), np.nan))
    for bad in (np.zeros((2, 3)), np.full((2, len(FEATURE_NAMES)), -1), np.full((2, len(FEATURE_NAMES)), np.inf)):
        with pytest.raises(ValueError):
            FineWindowModel().fit(bad)


@pytest.mark.parametrize("config", [
    {"scale_floor": 0}, {"seasonal_shrink": -1}, {"rho_cap": 1},
    {"rho_cap": -0.1}, {"temperature": -1}, {"temperature": np.nan},
    {"weights": np.zeros(len(FEATURE_NAMES))}, {"weights": [-1] * len(FEATURE_NAMES)},
    {"weights": [1, 2]}, {"weights": {"unknown": 1}},
])
def test_invalid_configuration(config):
    with pytest.raises(ValueError):
        FineWindowModel(**config)


def test_large_vectorized_batch_parity_across_internal_blocks():
    model = FineWindowModel(linear_trend=False)
    normal = np.tile([4.0, 5.0, 2.0], (REFERENCE_BINS, 1))
    mask = np.ones(REFERENCE_BINS, bool)
    reference = model.prepare_reference(normal, mask, mask)
    single = np.array([4.0, 5.0, 2.0]) + np.linspace(-0.15, 0.15, 12)[:, None]
    windows = 200_000
    x = np.tile(single, (windows, 1))
    observed = np.ones(len(x), bool)
    actual = model.sequence_features(reference, x, observed, observed, analysis_hours=1)
    assert actual.shape == (windows, len(model.feature_names))
    expected = scalar_features(reference, single, observed[:12], observed[:12], 0)
    # Include either side of the first internal block boundary (5,461 windows).
    for index in (0, 5460, 5461, 100_000, windows - 1):
        np.testing.assert_allclose(actual[index], expected, atol=2e-12, rtol=2e-12)
    assert isinstance(reference, FineReference)
