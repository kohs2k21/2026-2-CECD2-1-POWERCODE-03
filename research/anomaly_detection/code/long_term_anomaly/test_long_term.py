"""Fast contract/statistics tests. Never open held-out benchmark truths."""
import io
import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import confusion_matrix
from .models import (Setting, LongTermDetector, fill_short_gaps, rolling_mean,
                     prepare_case, case_components, feature_names)
from .inference import score_frame, boolean
from .experiment import metrics
from .benchmark import CHANNELS
from .persistent import PersistentDetector


def sample(seed=0, context=96, horizon=96):
    rng = np.random.default_rng(seed)
    t = np.arange(context + horizon)
    x = np.column_stack([4 + .2 * np.sin(2 * np.pi * t / 24),
                         6 + .1 * np.cos(2 * np.pi * t / 24), np.full(len(t), 8.)])
    x += rng.normal(0, .08, x.shape)
    return x, np.ones(len(t), bool), np.ones(len(t), bool)


def fitted(method="stl_pelt"):
    setting = Setting(method=method, context_hours=96)
    features = [case_components(*sample(i), setting)[0] for i in range(4)]
    model = LongTermDetector(setting).fit_reference(features)
    model.threshold = float(np.nanquantile(np.concatenate([model.score_components(f)[0] for f in features]), .99))
    return model


def frame_of(x, observed, latency):
    f = pd.DataFrame(x, columns=CHANNELS)
    f["case_id"] = "example"
    f["timestamp"] = pd.date_range("2030-01-01", periods=len(x), freq="h")
    f["collection_observed"] = observed
    f["latency_available"] = latency
    return f


def test_short_gap_is_interpolated_not_zero():
    x = np.array([[1., 3.], [np.nan, np.nan], [3., 5.]])
    np.testing.assert_allclose(fill_short_gaps(x)[1], [2, 4])
    assert np.isnan(x[1]).all()


def test_long_gap_requires_explicit_batch_split():
    x = np.ones((30, 3)); x[3:12] = np.nan
    with pytest.raises(ValueError, match="Long missing"):
        fill_short_gaps(x)


@pytest.mark.parametrize("w", [1, 6, 12, 24])
def test_rolling_edges_do_not_pad_zeros(w):
    np.testing.assert_allclose(rolling_mean(np.ones((50, 3)), w), 1)


@pytest.mark.parametrize("method", ["seasonal_naive", "stl_window", "stl_pelt"])
def test_score_shape_finiteness_and_no_mutation(method):
    model = fitted(method)
    x, o, l = sample(10); before = x.copy()
    result = model.predict(x, o, l)
    np.testing.assert_array_equal(x, before)
    assert result["score"].shape == (96,)
    assert np.isfinite(result["score"]).all()
    assert len(feature_names(model.setting)) == len(model.center_)


def test_monitor_changes_do_not_refit_normal_forecast():
    s = Setting(context_hours=96)
    x, o, l = sample(1)
    first = prepare_case(x, o, l, s)
    x[96:, 1] += 2
    second = prepare_case(x, o, l, s)
    np.testing.assert_array_equal(first[1], second[1])
    np.testing.assert_array_equal(first[2], second[2])
    assert np.mean(second[0][:, 1] - first[0][:, 1]) > 1


def test_missing_collection_is_abstained_not_true_negative():
    model = fitted()
    x, o, l = sample(6)
    o[130:134] = False; l[130:134] = False; x[130:134] = np.nan
    r = model.predict(x, o, l)
    assert np.isnan(r["score"][34:38]).all()
    assert not r["assessable"][34:38].any()
    assert not r["is_anomaly"][34:38].any()


def test_serialization_is_exact_and_no_file_dependency():
    model = fitted()
    buffer = io.BytesIO(); joblib.dump(model, buffer); buffer.seek(0)
    restored = joblib.load(buffer)
    np.testing.assert_array_equal(model.predict(*sample(8))["score"], restored.predict(*sample(8))["score"])


def test_extra_labels_ids_and_scenario_columns_are_ignored():
    model = fitted()
    f = frame_of(*sample(8))
    a = score_frame(model, f)
    f["label"] = True; f["scenario"] = "malicious_hint"; f["magnitude_sigma"] = 999
    f["case_id"] = "entirely_new_adapter_and_process"
    b = score_frame(model, f)
    np.testing.assert_array_equal(a.score, b.score)


@pytest.mark.parametrize("values", [["FALSE", "TRUE"], [0, 1], [False, True]])
def test_boolean_parsing_is_not_python_truthiness(values):
    np.testing.assert_array_equal(boolean(pd.Series(values), "mask"), [False, True])


def test_ambiguous_boolean_rejected():
    with pytest.raises(ValueError):
        boolean(pd.Series(["unknown"]), "mask")


@pytest.mark.parametrize("kind", ["order", "duplicate", "gap", "negative", "observed_nan", "bad_mask"])
def test_invalid_input_is_rejected(kind):
    model = fitted()
    f = frame_of(*sample(4))
    if kind == "order": f = f.iloc[::-1]
    elif kind == "duplicate": f.loc[1, "timestamp"] = f.loc[0, "timestamp"]
    elif kind == "gap": f.loc[50:, "timestamp"] += pd.Timedelta(hours=1)
    elif kind == "negative": f.loc[100, "log_requests"] = -1
    elif kind == "observed_nan": f.loc[100, "log_duration_ms"] = np.nan
    elif kind == "bad_mask": f.loc[100, "collection_observed"] = False
    with pytest.raises(ValueError): score_frame(model, f)


def test_confusion_metrics_and_nan_exclusion_independent():
    y = np.array([0, 0, 1, 1, 0, 1], bool)
    scores = np.array([0., 2., 2., 0., np.nan, 3.])
    r = metrics(y, scores, 1)
    assert (r["tn"], r["fp"], r["fn"], r["tp"]) == (1, 1, 1, 2)
    assert r["false_positive_rate"] == .5
    assert r["precision"] == r["recall"] == r["f1"] == 2 / 3
    assert r["unassessable_hours"] == 1


def test_no_ground_truth_point_adjustment():
    y = np.ones(10, bool)
    # Include a separate normal point to define FPR. One detected event hour
    # must not cause the remaining nine positive hours to be marked detected.
    y = np.r_[False, y]; score = np.r_[0., 2., np.zeros(9)]
    r = metrics(y, score, 1)
    assert r["recall"] == .1


def test_clear_sustained_duration_shift_increases_score():
    model = fitted()
    x, o, l = sample(8)
    normal = model.predict(x, o, l)
    x[120:168, 1] += 1.5
    changed = model.predict(x, o, l)
    assert np.median(changed["score"][30:60]) > np.median(normal["score"][30:60]) + 1


def test_zero_requests_does_not_create_fake_zero_duration():
    model = fitted()
    x, o, l = sample(12)
    x[120:125, 0] = 0; x[120:125, 1:] = np.nan; l[120:125] = False
    out = score_frame(model, frame_of(x, o, l))
    assert out.assessable.all()
    assert np.isfinite(out.score).all()


def test_persistent_weights_do_not_depend_on_scenario_or_truth():
    base = fitted()
    m = PersistentDetector(base.setting, energy_weight=1, local_weight=0)
    m.center_, m.spread_, m.threshold = base.center_, base.spread_, base.threshold
    components, _ = case_components(*sample(8), m.setting)
    before = m.score_components(components)[0]
    # Disabling local evidence must really remove arbitrarily large instantaneous
    # deviations, rather than leaving a hidden pointwise detector in the score.
    components[:, :6] += 10000
    np.testing.assert_array_equal(m.score_components(components)[0], before)


def test_segment_cache_is_exact_across_window_widths(tmp_path, monkeypatch):
    from . import experiment
    monkeypatch.setattr(experiment, "REPORT", tmp_path)
    (tmp_path / "cache").mkdir()
    x, o, l = sample(8)
    s = Setting(context_hours=96)
    r = prepare_case(x, o, l, s)[0][None]
    inputs = {"collection_observed": o[None], "latency_available": l[None]}
    first = experiment.component_bank(r, inputs, s)
    from dataclasses import replace
    changed = replace(s, window=24, energy_window=48)
    cached = experiment.component_bank(r, inputs, changed)
    direct = case_components(x, o, l, changed)[0]
    np.testing.assert_allclose(cached[0], direct, equal_nan=True)
    np.testing.assert_array_equal(first[0, :, 6:], cached[0, :, 6:])


def test_long_monitor_gap_splits_statistics_without_inventing_traffic():
    model = fitted()
    x, o, l = sample(10)
    x[130:139] = np.nan; o[130:139] = False; l[130:139] = False
    result = model.predict(x, o, l)
    assert np.isnan(result['score'][34:43]).all()
    assert result['assessable'].sum() == 96 - 9
    assert np.isfinite(result['score'][:34]).all()
    assert np.isfinite(result['score'][43:]).all()
    changed = x.copy(); changed[139:, 0] += 4
    second = model.predict(changed, o, l)
    np.testing.assert_array_equal(result['score'][:34], second['score'][:34])
