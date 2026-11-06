import numpy as np
import pandas as pd
import pytest

from .features import EventFeatures, NormalReference, normalize, FEATURES
from .models import Detector, Settings


def rows(n=40):
    times = pd.date_range("2026-01-01 09:00", periods=n, freq="min")
    return pd.DataFrame(dict(transactionId=[f"T{i}" for i in range(n)],
        processId="P", adapterType="A", totalCount=10, successCount=10,
        errorCount=0, retryCount=0, status="S", startTime=times,
        endTime=times+pd.to_timedelta(np.arange(n)%3+100, unit="ms")))


def test_preserves_original_executions_and_identity():
    raw = rows()
    f = EventFeatures(NormalReference.fit(normalize(raw))).transform(raw)
    assert len(f) == len(raw)
    assert f.transactionId.tolist() == raw.transactionId.tolist()
    np.testing.assert_allclose(f.totalCount, raw.totalCount)
    np.testing.assert_allclose(f.duration_ms, np.arange(len(raw))%3+100)
    assert set(FEATURES) <= set(f)


def test_future_changes_do_not_change_prefix():
    raw = rows()
    ref = NormalReference.fit(normalize(raw))
    a = EventFeatures(ref).transform(raw)
    changed = raw.copy()
    changed.loc[30:, "endTime"] += pd.Timedelta(seconds=10)
    b = EventFeatures(ref).transform(changed)
    np.testing.assert_array_equal(a.loc[:29, list(FEATURES)], b.loc[:29, list(FEATURES)])


def test_streaming_and_batch_are_identical():
    raw = rows()
    ref = NormalReference.fit(normalize(raw))
    batch = EventFeatures(ref).transform(raw)
    processor = EventFeatures(ref)
    stream = pd.concat([processor.transform(raw.iloc[:20].assign(event_row_id=np.arange(20))),
                        processor.transform(raw.iloc[20:].assign(event_row_id=np.arange(20,40)))])
    np.testing.assert_array_equal(batch[list(FEATURES)], stream[list(FEATURES)])


def test_labels_and_ids_are_not_estimator_columns():
    raw = rows().assign(label=1, row_is_anomaly=1, scenario_types="secret")
    ref = NormalReference.fit(normalize(raw))
    a = EventFeatures(ref).transform(raw)
    b = EventFeatures(ref).transform(raw.assign(label=0, row_is_anomaly=0, scenario_types="other"))
    np.testing.assert_array_equal(a[list(FEATURES)], b[list(FEATURES)])
    assert not set(FEATURES) & {"status", "label", "transactionId", "processId", "adapterType", "case_id"}


def test_same_timestamp_is_order_independent():
    raw = rows()
    raw["availableTime"] = raw.endTime.dt.ceil("5min")
    ref = NormalReference.fit(normalize(raw))
    a = EventFeatures(ref).transform(raw.assign(event_row_id=np.arange(len(raw))))
    b = EventFeatures(ref).transform(raw.assign(event_row_id=np.arange(len(raw))).iloc[::-1])
    b = b.sort_values("event_row_id")
    np.testing.assert_allclose(a[list(FEATURES)], b[list(FEATURES)])


def test_same_transaction_rows_available_together():
    raw = rows(2)
    raw["transactionId"] = "T"
    raw["processId"] = ["P1", "P2"]
    f = normalize(raw)
    assert f.availableTime.nunique() == 1
    assert f.availableTime.iloc[0] == f.endTime.max()


def test_clock_contract_and_no_gap_fabrication():
    raw = rows(2)
    raw.loc[1, ["startTime", "endTime"]] += pd.Timedelta(days=20)
    ref = NormalReference.fit(normalize(raw), min_support=1)
    f = EventFeatures(ref).transform(raw)
    assert len(f) == 2
    assert f.availableTime.iloc[0].hour == 0  # 09 KST = 00 UTC
    assert f.gap_log_seconds.iloc[1] > 10


def test_reject_future_availability_and_duplicate_row_id():
    raw = rows(2)
    raw["availableTime"] = raw.startTime
    with pytest.raises(ValueError, match="before"):
        normalize(raw)
    raw = rows(2).assign(event_row_id=0)
    with pytest.raises(ValueError, match="uniquely"):
        normalize(raw)


@pytest.mark.parametrize("algorithm", ["if", "kmeans", "lof"])
def test_estimators_save_load_and_unknown_groups(tmp_path, algorithm):
    import joblib
    raw = rows(80)
    ref = NormalReference.fit(normalize(raw))
    f = EventFeatures(ref).transform(raw)
    model = Detector(Settings(algorithm, clusters=4, neighbors=5)).fit(f)
    model.threshold = float(np.quantile(model.raw_score(f), .95))
    file = tmp_path/"model.joblib"
    joblib.dump(model, file)
    np.testing.assert_array_equal(model.predict(f)[0], joblib.load(file).predict(f)[0])
    unknown = raw.assign(adapterType="NEW", processId="NEW")
    u = EventFeatures(ref).transform(unknown)
    assert np.isfinite(model.predict(u)[0]).all()
    assert u.reference_level.eq(4).all()
