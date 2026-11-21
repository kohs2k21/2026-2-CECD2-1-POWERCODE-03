"""Long-window context must not propagate an alarm to a quiet local hour."""
import numpy as np
import pytest
from .test_bank import data, frame
from .localized import LocalizedBank
from .bank import combine_evidence


@pytest.mark.parametrize("h", [4, 8, 12, 24])
def test_localized_gate_and_false_positive_subset_bound(data, h):
    ordinary, ref, x, o = data
    bank = LocalizedBank(ordinary.models, ordinary.settings)
    batch = frame(x[8064:8064 + h * 12], start=ref.end_time)
    result = bank.predict_batch(ref, batch, h)
    model = bank.models[1]
    local_features = model.sequence_features(ref.latent, x[8064:8064 + h * 12], o[:h * 12], o[:h * 12], 1)
    e = model.normal_evidence(local_features)
    cfg = bank.settings[1]
    local = combine_evidence(e, cfg["weights"], cfg["temperature"])
    expected = (result.context_score > result.context_threshold) & (local > cfg["threshold"])
    np.testing.assert_array_equal(result.is_anomaly, expected)
    assert not (result.is_anomaly & ~(local > cfg["threshold"])).any()
    np.testing.assert_array_equal(result.threshold, np.zeros(h))
    assert result.score_scope.eq("hour_localized_inside_selected_batch").all()


def test_localized_one_hour_path_is_exactly_original_one_hour(data):
    ordinary, ref, x, _ = data
    bank = LocalizedBank(ordinary.models, ordinary.settings)
    batch = frame(x[8064:8076], start=ref.end_time)
    np.testing.assert_array_equal(bank.predict_batch(ref, batch, 1).score, ordinary.predict_batch(ref, batch, 1).score)
