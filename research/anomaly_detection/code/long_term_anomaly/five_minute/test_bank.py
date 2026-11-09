"""Public-bank temporal isolation and unchanged raw Process input contract."""
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from .bank import FineBank, validated_frame, combine_evidence
from .model import FineWindowModel
from .benchmark import CHANNELS, inject
from ..common import read_json


def frame(x, o=None, a=None, start="2026-10-01", identity="group"):
    f = pd.DataFrame(x, columns=CHANNELS)
    f["timestamp"] = pd.date_range(start, periods=len(f), freq="5min")
    f["case_id"] = identity
    f["collection_observed"] = np.ones(len(f), bool) if o is None else o
    f["latency_available"] = np.ones(len(f), bool) if a is None else a
    return f


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(710)
    x = np.maximum(3 + rng.normal(0, .1, (8064 + 4032, 3)), .1)
    o = np.ones(len(x), bool)
    model = FineWindowModel()
    ref = model.prepare_reference(x[:8064], o[:8064], o[:8064])
    models, settings = {}, {}
    for h in (1, 4, 8, 12, 24):
        m = FineWindowModel()
        m.fit(m.sequence_features(ref, x[8064:], o[8064:], o[8064:], h))
        models[h] = m
        settings[h] = dict(weights=np.ones(len(m.feature_names)).tolist(), temperature=.5, threshold=5.)
    bank = FineBank(models, settings)
    history = frame(x[:8064])
    reference = bank.prepare_reference(history)
    return bank, reference, x, o


@pytest.mark.parametrize("h", [1, 4, 8, 12, 24])
def test_public_single_window_matches_vectorized_evaluation(data, h):
    bank, reference, x, o = data
    m = bank.models[h]
    f = m.sequence_features(reference.latent, x[8064:], o[8064:], o[8064:], h)
    expected = combine_evidence(m.normal_evidence(f), bank.settings[h]["weights"], .5)
    for i in (0, 1, 336 // h - 1):
        offset = i * h * 12
        start = reference.end_time + pd.Timedelta(minutes=5 * offset)
        batch = frame(x[8064 + offset:8064 + offset + h * 12], start=start)
        result = bank.predict_batch(reference, batch, h)
        np.testing.assert_allclose(result.score, expected[i], rtol=0, atol=1e-12)
        assert result.timestamp.iloc[0] == start
        assert result.available_at.eq(start + pd.Timedelta(hours=h)).all()
        assert not result.monitoring_state_carried.any()


def test_native_bank_does_not_use_other_monitoring_windows(data):
    bank, reference, x, _ = data
    batch = frame(x[8064:8076], start=reference.end_time)
    before = bank.predict_batch(reference, batch, 1)
    bank.predict_batch(reference, frame(np.full((12, 3), 100.), start=reference.end_time + pd.Timedelta(hours=1)), 1)
    pd.testing.assert_frame_equal(before, bank.predict_batch(reference, batch, 1))


def test_masks_preserve_empty_traffic_and_unknown_collection(data):
    bank, reference, _, _ = data
    x = np.zeros((12, 3));x[:, 1:] = np.nan
    known = frame(x, a=np.zeros(12, bool), start=reference.end_time)
    assert bank.predict_batch(reference, known, 1).assessable.all()
    unknown = frame(np.full((12, 3), np.nan), o=np.zeros(12, bool), a=np.zeros(12, bool), start=reference.end_time)
    result = bank.predict_batch(reference, unknown, 1)
    assert not result.assessable.any()
    assert result.score.isna().all()
    assert not result.is_anomaly.any()


@pytest.mark.parametrize("alter", ["reverse", "duplicate", "negative", "group", "offset", "overlap"])
def test_bad_native_batch_rejected(data, alter):
    bank, reference, x, _ = data
    batch = frame(x[8064:8076], start=reference.end_time)
    if alter == "reverse": batch = batch.iloc[::-1]
    elif alter == "duplicate": batch.loc[1, "timestamp"] = batch.timestamp.iloc[0]
    elif alter == "negative": batch.loc[0, "log_requests"] = -1
    elif alter == "group": batch["case_id"] = "different"
    elif alter == "offset": batch["timestamp"] += pd.Timedelta(minutes=1)
    elif alter == "overlap": batch["timestamp"] -= pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        bank.predict_batch(reference, batch, 1)


def test_injection_preserves_all_six_original_event_definitions():
    cfg = read_json(Path(__file__).resolve().parents[1] / "protocol.json")
    for i, task in enumerate(cfg["scenario_definitions"]):
        z = np.zeros((1008 * 12, 3))
        event = inject(z, np.random.default_rng(100 + i), task, cfg)
        spec = cfg["scenario_definitions"][task]
        a, b = (672 + event["event_start_hour"]) * 12, (672 + event["event_end_hour"]) * 12
        assert spec["duration_hours"][0] <= (b - a) // 12 <= spec["duration_hours"][1]
        assert spec["magnitude_sigma"][0] <= event["magnitude_sigma_native_five_minute_scale"] <= spec["magnitude_sigma"][1]
        assert not z[:a].any() and not z[b:].any()
        other = [j for j in range(3) if j != spec["channel"]]
        assert not z[:, other].any()
        assert np.count_nonzero(z[a:b]) > 0


def test_metrics_are_median_of_scenario_metrics_not_harmonic_of_medians():
    from .run import metrics
    y = np.asarray([[1, 0], [1, 0], [1, 0], [1, 0], [1, 0], [1, 0]], bool)
    scores = np.asarray([[2, 0], [0, 0], [2, 2], [2, 0], [0, 2], [2, 0]], float)
    result = metrics(y, scores, 1, np.asarray(list("abcdef")))
    for name in ("accuracy", "precision", "recall", "f1"):
        assert result["median"][name] == np.median([r[name] for r in result["scenarios"].values()])
    assert result["pooled"]["tp"] == 4
    assert result["pooled"]["fp"] == 2


def test_raw_process_service_dispatches_native_internal_bins(data):
    from ..raw_windows.service import ProcessWindowService, ProcessReference
    from ..raw_windows.aggregation import CollectionCoverage, case_id
    bank, ref, x, _ = data
    key = ("DB.READER", "process")
    # Reuse a learned statistical reference with the exact raw group identity.
    from .bank import FineReference
    ref = FineReference(ref.latent, ref.end_time, case_id(key))
    records = []
    for i in range(12):
        start = ref.end_time + pd.Timedelta(minutes=5 * i + 1)
        records.append(dict(transactionId=f"T-{i}", processId=key[1], processHubId="HUB",
            adapterType=key[0], startTime=start.isoformat(), endTime=(start + pd.Timedelta(milliseconds=100)).isoformat(),
            status="S", retryCount=0, totalCount=1, successCount=1, errorCount=0))
    end = ref.end_time + pd.Timedelta(hours=1)
    result = ProcessWindowService(bank).predict_processes(records,
        references={key: ProcessReference(key, ref.end_time, ref)}, window_end=end, analysis_hours=1,
        coverage=CollectionCoverage(((ref.end_time, end),)))
    assert len(result["fine_bins"]) == 12
    assert len(result["decisions"]) == 1
    assert result["decisions"].fine_bin_features_used_for_alarm.all()
    assert not result["diagnostics"]["finer_resolution_retraining_required"]
