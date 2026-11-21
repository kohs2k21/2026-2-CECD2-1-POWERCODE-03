"""Batch length, clock alignment, missingness and no cross-batch lookahead."""
from unittest.mock import patch
import io
import joblib
import numpy as np
import pandas as pd
import pytest
from ..benchmark import CHANNELS
from ..persistent import PersistentDetector
from .model import (HORIZONS, HOUR_NS, IntervalModelBank, checked_hours, setting_for,
                    raw_residual, batch_components, sequence_components, score_frame)


def sample(seed=1, monitor=24):
    rng = np.random.default_rng(seed)
    t = np.arange(96 + monitor)
    x = np.column_stack([4 + .3 * np.sin(2 * np.pi * t / 24),
                         6 + .2 * np.cos(2 * np.pi * t / 24), np.full(len(t), 8.)])
    x += rng.normal(0, .06, x.shape)
    f = pd.DataFrame(x, columns=CHANNELS)
    f["case_id"] = "one_group"
    f["timestamp"] = pd.date_range("2030-01-01", periods=len(t), freq="h")
    f["collection_observed"] = True; f["latency_available"] = True
    return f


@pytest.fixture(scope="module")
def bank():
    # Independent hand-generated normal fixtures; no benchmark truths opened.
    models = {}
    for h in HORIZONS:
        s = setting_for(h, 96)
        f = np.random.default_rng(100 + h).normal(0, 1, (192, 3))
        comps = sequence_components(f, np.ones(192, bool), np.ones(192, bool), h, s)
        m = PersistentDetector(s, 1., 1.).fit_reference([comps])
        m.threshold = float(np.nanquantile(m.score_components(comps)[0], .98))
        models[h] = m
    return IntervalModelBank(models, context_hours=96)


@pytest.mark.parametrize("hours", HORIZONS)
def test_exact_selected_horizon_is_returned(bank, hours):
    f = sample(monitor=hours)
    ref = bank.prepare_reference(f.iloc[:96])
    out = bank.predict_batch(ref, f.iloc[96:], hours)
    assert len(out) == hours and out.analysis_hours.eq(hours).all()
    assert out.assessable.all() and np.isfinite(out.score).all()
    assert out.threshold.eq(bank.models[hours].threshold).all()
    assert out.available_at.eq(f.timestamp.iloc[-1] + pd.Timedelta(hours=1)).all()


@pytest.mark.parametrize("bad", [True, False, 0, 2, 3, 48, "4", 4.0, None])
def test_unsupported_or_ambiguous_horizon_rejected(bad):
    with pytest.raises(ValueError): checked_hours(bad)


@pytest.mark.parametrize("hours", HORIZONS)
def test_later_batches_do_not_change_earlier_predictions(bank, hours):
    f = sample(monitor=48)
    first = score_frame(bank, f, hours, split_batches=True)
    altered = f.copy()
    altered.loc[96 + hours:, list(CHANNELS)] += 5
    second = score_frame(bank, altered, hours, split_batches=True)
    np.testing.assert_array_equal(first.score.iloc[:hours], second.score.iloc[:hours])


def test_missing_end_of_batch_does_not_interpolate_from_next_batch():
    s = setting_for(4, 96)
    residual = np.ones((8, 3)); residual[3] = np.nan
    observed = np.ones(8, bool); observed[3] = False
    latency = observed.copy()
    first = sequence_components(residual, observed, latency, 4, s)
    residual[4:] = 100
    second = sequence_components(residual, observed, latency, 4, s)
    np.testing.assert_array_equal(first[:4], second[:4])


def test_clock_phase_not_reset_to_zero_at_every_batch(bank):
    f = sample(monitor=48)
    ref = bank.prepare_reference(f.iloc[:96])
    x = f.iloc[96:][list(CHANNELS)].to_numpy(float)
    observed = latency = np.ones(48, bool)
    whole = raw_residual(ref, x, observed, latency)
    pieces = np.concatenate([raw_residual(ref, x[i:i+4], observed[i:i+4], latency[i:i+4], i)
                             for i in range(0, 48, 4)])
    np.testing.assert_array_equal(whole, pieces)
    with pytest.raises(ValueError): raw_residual(ref, x[:4], observed[:4], latency[:4], -1)


@pytest.mark.parametrize("hours", HORIZONS)
def test_all_missing_batch_abstains(bank, hours):
    f = sample(monitor=hours)
    f.loc[96:, list(CHANNELS)] = np.nan
    f.loc[96:, ["collection_observed", "latency_available"]] = False
    out = score_frame(bank, f, hours)
    assert not out.assessable.any() and not out.is_anomaly.any()
    assert out.score.isna().all()


def test_long_collection_gap_splits_and_does_not_create_zero_requests(bank):
    f = sample(monitor=24)
    f.loc[104:111, list(CHANNELS)] = np.nan
    f.loc[104:111, ["collection_observed", "latency_available"]] = False
    out = score_frame(bank, f, 24)
    assert out.assessable.sum() == 16
    assert out.score.iloc[8:16].isna().all()


def test_reference_is_fixed_and_monitor_never_becomes_training_history(bank):
    f = sample(monitor=24)
    ref = bank.prepare_reference(f.iloc[:96])
    before = ref.next_day_forecast.copy()
    f.loc[96:, "log_duration_ms"] += 2
    bank.predict_batch(ref, f.iloc[96:], 24)
    np.testing.assert_array_equal(before, ref.next_day_forecast)


def test_group_mismatch_and_stale_reference_rejected(bank):
    f = sample(monitor=4)
    ref = bank.prepare_reference(f.iloc[:96])
    other = f.iloc[96:].copy(); other["case_id"] = "wrong_group"
    with pytest.raises(ValueError, match="different case_id"):
        bank.predict_batch(ref, other, 4)
    old = f.iloc[96:].copy(); old["timestamp"] += pd.Timedelta(days=15)
    with pytest.raises(ValueError, match="older than"):
        bank.predict_batch(ref, old, 4)


@pytest.mark.parametrize("kind", ["wrong_rows", "incomplete_stream", "duplicate", "missing_mask", "nonfinite", "negative"])
def test_invalid_api_inputs_rejected(bank, kind):
    f = sample(monitor=4)
    if kind == "wrong_rows": f = f.iloc[:-1]
    elif kind == "incomplete_stream": f = f.iloc[:-1]
    elif kind == "duplicate": f.loc[98, "timestamp"] = f.loc[97, "timestamp"]
    elif kind == "missing_mask": f = f.drop(columns="collection_observed")
    elif kind == "nonfinite": f.loc[99, "log_duration_ms"] = np.inf
    elif kind == "negative": f.loc[99, "log_requests"] = -1
    with pytest.raises(ValueError): score_frame(bank, f, 4, split_batches=(kind == "incomplete_stream"))


def test_serialization_no_file_io_and_no_label_features(bank):
    b = io.BytesIO(); joblib.dump(bank, b); b.seek(0)
    restored = joblib.load(b)
    f = sample(monitor=8)
    a = score_frame(bank, f, 8)
    f["label"] = True; f["scenario"] = "hint"; f["magnitude_sigma"] = 999
    with patch("builtins.open", side_effect=AssertionError("Model performed file I/O")):
        c = score_frame(restored, f, 8)
    np.testing.assert_array_equal(a.score, c.score)


def test_one_hour_is_pointwise_and_pelt_cannot_invent_a_long_segment():
    h1 = setting_for(1)
    r = np.array([[2., -3., .5]])
    f = batch_components(r, np.ones(1, bool), np.ones(1, bool), h1)
    np.testing.assert_array_equal(f[0, :3], abs(r[0]))
    np.testing.assert_array_equal(f[0, 6:9], abs(r[0]))
    assert h1.min_segment == 12


def test_documented_cli_loads_saved_bank_and_honors_length(bank, tmp_path):
    import os
    import subprocess
    import sys
    from ..common import ROOT
    model_path, source, destination = tmp_path / 'bank.joblib', tmp_path / 'input.csv', tmp_path / 'scores.csv'
    joblib.dump(bank, model_path)
    sample(monitor=4).to_csv(source, index=False)
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'code'), PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-m', 'long_term_anomaly.intervals',
        '--model', str(model_path), '--input', str(source), '--output', str(destination),
        '--analysis-hours', '4'], env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    frame = pd.read_csv(destination)
    assert len(frame) == 4 and frame.analysis_hours.eq(4).all()
