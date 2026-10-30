"""Selected-window isolation, raw schema and independent aggregation checks."""
from dataclasses import dataclass
from pathlib import Path
import copy
import numpy as np
import pandas as pd
import pytest

from .aggregation import (aggregate_processes, hourly_frame, CollectionCoverage,
                          window_features, case_id, kst)
from .service import ProcessWindowService

START = pd.Timestamp("2026-10-01 10:00")
END = START + pd.Timedelta(hours=1)
KEY = ("DB.READER", "IF_A_01")


def record(index=0, minutes=1, duration=100., **updates):
    t = START + pd.Timedelta(minutes=minutes)
    r = dict(transactionId=f"T-{index}", processId=KEY[1], dependProcessId=None,
        processHubId="HUB", adapterType=KEY[0], channelId="CH", status="S",
        startTime=t.isoformat(), endTime=(t + pd.Timedelta(milliseconds=duration)).isoformat(),
        successCount=2, errorCount=0, responseCode=None, totalCount=2, retryCount=0)
    r.update(updates)
    return r


def agg(records, coverage=True, **kw):
    cov = CollectionCoverage(((START, END),)) if coverage is True else (None if coverage is False else coverage)
    return aggregate_processes(records, start=START, end=END, coverage=cov, groups=[KEY], **kw)


def test_external_schema_unchanged_and_original_values_preserved():
    rows = [record(), record(1, 6)]
    before = copy.deepcopy(rows)
    out = agg(rows)
    assert rows == before
    assert len(out.bins) == 12
    assert out.bins.request_count.sum() == 2
    assert out.diagnostics["external_process_schema_changed"] is False


def test_hourly_weighting_is_by_process_not_by_fine_bin():
    rows = [record(0, 1, 9)] + [record(i, 6, 999) for i in range(1, 10)]
    h = hourly_frame(agg(rows)).iloc[0]
    assert np.expm1(h.log_requests) == pytest.approx(10)
    assert h.log_duration_ms == pytest.approx((np.log1p(9) + 9 * np.log1p(999)) / 10)
    assert h.log_duration_ms != pytest.approx((np.log1p(9) + np.log1p(999)) / 2)


def test_half_open_boundaries_and_earlier_later_logs_ignored():
    rows = [record(0, 0), record(1, 59)]
    base = agg(rows)
    changed = agg([*rows, record(2, -1, 99999), record(3, 60, 99999)])
    pd.testing.assert_frame_equal(base.bins, changed.bins)
    assert changed.diagnostics["outside_rows_ignored"] == 2


def test_completion_after_window_end_not_used_as_future_duration():
    a = record(0, 59, duration=120000)
    out = agg([a])
    assert out.bins.received_rows.sum() == 1
    assert out.bins.duration_samples.sum() == 0
    assert out.bins.pending_rows.sum() == 1
    assert not hourly_frame(out).latency_available.any()


def test_unknown_coverage_never_becomes_successful_zero_requests():
    out = agg([], coverage=False)
    assert out.bins.request_count.isna().all()
    assert out.bins.log_requests.isna().all()
    assert not out.bins.collection_observed.any()
    assert not hourly_frame(out).collection_observed.any()


def test_certified_empty_input_is_zero_traffic_not_zero_duration():
    out = agg([])
    assert out.bins.request_count.eq(0).all()
    h = hourly_frame(out).iloc[0]
    assert h.log_requests == 0
    assert h.collection_observed
    assert not h.latency_available
    assert pd.isna(h.log_duration_ms)


def test_partial_coverage_and_adjacent_interval_union():
    cov = CollectionCoverage(((START, START + pd.Timedelta(minutes=30)),
                              (START + pd.Timedelta(minutes=30), END)))
    assert agg([], cov).bins.collection_observed.all()
    cov = CollectionCoverage(((START, END - pd.Timedelta(minutes=1)),))
    out = agg([], cov)
    assert out.bins.collection_observed.sum() == 11
    assert not hourly_frame(out).collection_observed.any()


def test_only_fully_covered_bins_known():
    cov = CollectionCoverage(((START + pd.Timedelta(minutes=1), END),))
    assert agg([], cov).bins.collection_observed.sum() == 11


def test_arbitrary_window_start_retains_twelve_relative_bins_per_hour():
    start = START + pd.Timedelta(minutes=3, seconds=17)
    end = start + pd.Timedelta(hours=1)
    out = aggregate_processes([record(minutes=4)], start=start, end=end,
                             coverage=CollectionCoverage(((start, end),)), groups=[KEY])
    assert len(out.bins) == 12
    assert out.bins.timestamp.iloc[0] == start
    assert out.bins.timestamp.iloc[-1] + pd.Timedelta(minutes=5) == end
    assert out.bins.received_rows.iloc[0] == 1


def test_partial_invalid_work_cannot_create_clean_latency_feature():
    out = agg([record(), record(1, totalCount=-1)])
    assert not hourly_frame(out).latency_available.any()


def test_group_mapping_no_cross_group_collection_leak():
    other = ("SERVICE", "IF_B")
    rows = [record(), record(1, adapterType=other[0], processId=other[1])]
    out = aggregate_processes(rows, start=START, end=END, groups=[KEY, other],
                             coverage={KEY: CollectionCoverage(((START, END),))})
    assert out.bins.loc[out.bins.processId.eq(KEY[1]), "collection_observed"].all()
    assert not out.bins.loc[out.bins.processId.eq(other[1]), "collection_observed"].any()


def test_null_adapter_not_pooled_with_literal_null_text():
    rows = [record(adapterType=None), record(1, adapterType="null-text")]
    out = aggregate_processes(rows, start=START, end=END,
                             coverage=CollectionCoverage(((START, END),)))
    assert out.bins.case_id.nunique() == 2


def test_id_delimiters_not_ambiguous():
    assert case_id(("a|b", "c")) != case_id(("a", "b|c"))
    assert case_id((None, "x")) != case_id(("null", "x"))


def test_sql_alias_and_timezone_equivalence():
    r = record()
    r["startTime"] = "2026-10-01T01:01:00Z"
    r["endTime"] = "2026-10-01T01:01:00.100Z"
    alias = {"".join(("_" + c if c.isupper() else c) for c in k).upper(): v for k, v in r.items()}
    pd.testing.assert_frame_equal(agg([record()]).bins, agg([alias]).bins)
    assert kst("2026-10-01T01:00:00Z") == START


@pytest.mark.parametrize("updates", [dict(retryCount=-1), dict(retryCount=.5), dict(retryCount=True),
                                   dict(transactionId=None), dict(processHubId=None), dict(startTime="bad")])
def test_invalid_key_or_time_rejected(updates):
    with pytest.raises(ValueError):
        agg([record(**updates)])


def test_sql_key_duplicate_rejected_not_silently_counted():
    with pytest.raises(ValueError, match="Duplicate"):
        agg([record(), record(adapterType="OTHER")])


def test_retry_is_part_of_sql_key():
    assert agg([record(), record(retryCount=1)]).bins.received_rows.sum() == 2


@pytest.mark.parametrize("updates", [dict(endTime=None), dict(endTime="bad"),
                                   dict(endTime="2026-10-01 10:00")])
def test_bad_duration_not_clean_latency(updates):
    out = agg([record(**updates)])
    assert out.bins.invalid_duration_rows.sum() == 1
    assert not hourly_frame(out).latency_available.any()


@pytest.mark.parametrize("updates", [dict(totalCount=-1), dict(totalCount=.5), dict(totalCount=True), dict(totalCount=None)])
def test_bad_work_not_clean_latency(updates):
    out = agg([record(**updates)])
    assert out.bins.invalid_work_rows.sum() == 1
    assert not hourly_frame(out).latency_available.any()


def test_new_group_is_not_silently_mapped_to_existing_reference():
    with pytest.raises(ValueError, match="Unregistered"):
        agg([record(processId="NEW")])


def test_selected_interval_slope_is_local_and_in_hour_units():
    rows = [record(i, i * 5 + 1, duration=np.expm1(1 + 2 * i / 12)) for i in range(12)]
    f = window_features(agg(rows)).iloc[0]
    assert f.log_duration_ms_slope_per_hour == pytest.approx(2, abs=1e-4)
    assert f.log_duration_ms_late_minus_early == pytest.approx(1, abs=1e-4)


class CaptureBank:
    context_hours = 2

    def prepare_reference(self, frame):
        return frame.copy()

    def predict_batch(self, ref, batch, analysis_hours):
        assert len(batch) == analysis_hours
        # Deliberately simple test double: assertions concern service isolation,
        # not claims about calibrated model quality.
        return batch.assign(score=batch.log_requests, threshold=3.,
                            is_anomaly=batch.log_requests.gt(3), assessable=batch.collection_observed)


def setup_service():
    svc = ProcessWindowService(CaptureBank())
    normal = [record(-2, -60), record(-1, -1)]
    cov = CollectionCoverage(((START - pd.Timedelta(hours=2), END + pd.Timedelta(hours=25)),))
    refs = svc.prepare_reference(normal, end=START, coverage=cov, groups=[KEY])
    return svc, refs, cov


@pytest.mark.parametrize("h", [1, 4, 8, 12, 24])
def test_all_window_lengths_same_external_raw_input(h):
    svc, refs, cov = setup_service()
    end = START + pd.Timedelta(hours=h)
    out = svc.predict_processes([record()], references=refs, window_end=end, analysis_hours=h, coverage=cov)
    assert len(out["fine_bins"]) == h * 12
    assert len(out["decisions"]) == h
    assert out["diagnostics"]["outside_window_monitoring_used"] is False
    assert out["diagnostics"]["monitoring_state_carried"] is False


def test_service_does_not_mutate_or_carry_monitoring_history():
    svc, refs, cov = setup_service()
    before = refs[KEY].model_reference.copy(deep=True)
    a = svc.predict_processes([record()], references=refs, window_end=END, analysis_hours=1, coverage=cov)
    svc.predict_processes([record(1, 61, 99999)], references=refs, window_end=END + pd.Timedelta(hours=1), analysis_hours=1, coverage=cov)
    b = svc.predict_processes([record(), record(2, -1, 99999), record(3, 61, 99999)],
                             references=refs, window_end=END, analysis_hours=1, coverage=cov)
    pd.testing.assert_frame_equal(a["decisions"], b["decisions"])
    pd.testing.assert_frame_equal(before, refs[KEY].model_reference)


def test_reference_cannot_overlap_monitoring_window():
    svc, refs, cov = setup_service()
    with pytest.raises(ValueError, match="overlaps"):
        svc.predict_processes([], references=refs, window_end=START, analysis_hours=1, coverage=cov)


@pytest.mark.parametrize("h", [True, 0, 2, 1., "1"])
def test_bad_hours_rejected(h):
    svc, refs, cov = setup_service()
    with pytest.raises(ValueError):
        svc.predict_processes([record()], references=refs, window_end=END, analysis_hours=h, coverage=cov)


def test_real_saved_bank_raw_backend_smoke_all_five_lengths():
    from ..strict_alignment.model import load_bank
    root = Path(__file__).resolve().parents[3]
    model = root / "reports/long_term_anomaly/strict_alignment/models.joblib"
    if not model.is_file():
        pytest.skip("Local frozen artifact is not part of source-only distribution")
    svc = ProcessWindowService(load_bank(model))
    normal = [record(-i, -60 * i + 1, 100 + i % 7) for i in range(1, 673)]
    cov = CollectionCoverage(((START - pd.Timedelta(hours=672), START + pd.Timedelta(hours=24)),))
    refs = svc.prepare_reference(normal, end=START, coverage=cov, groups=[KEY])
    for h in (1, 4, 8, 12, 24):
        result = svc.predict_processes([record(i, 60 * i + 1) for i in range(h)], references=refs,
                                      window_end=START + pd.Timedelta(hours=h), analysis_hours=h, coverage=cov)
        assert len(result["decisions"]) == h
        assert np.isfinite(result["decisions"].score).all()
        assert result["decisions"].assessable.all()
