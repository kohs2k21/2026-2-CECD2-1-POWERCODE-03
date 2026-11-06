"""Reference-only calibration invariants, independent of benchmark labels."""
import pickle
import numpy as np
import pytest
from .conditional import ConditionalWindowModel
from .model import FineWindowModel


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(42)
    x = 3 + rng.normal(0, .12, (12096, 3))
    o = np.ones(len(x), bool)
    m = ConditionalWindowModel()
    r = m.prepare_reference(x[:8064], o[:8064], o[:8064])
    return m, r, x, o


def test_conditional_reference_arrays_immutable_even_after_pickle(data):
    _, ref, _, _ = data
    for r in (ref, pickle.loads(pickle.dumps(ref))):
        for arr in (r.profile, r.normal_tail_scale, *r.normal_windows):
            assert not arr.flags.writeable
            with pytest.raises(ValueError):
                arr.setflags(write=True)


@pytest.mark.parametrize("h", [1, 4, 8, 12, 24])
def test_conditional_single_window_matches_replay_and_does_not_carry_state(data, h):
    m, ref, x, o = data
    sequence = m.sequence_features(ref, x[8064:], o[8064:], o[8064:], h)
    i = min(2, len(sequence) - 1)
    offset, width = i * h * 12, h * 12
    batch = x[8064 + offset:8064 + offset + width]
    got = m.window_features(ref, batch, o[:width], o[:width], offset)
    np.testing.assert_allclose(got, sequence[i], rtol=0, atol=0, equal_nan=True)
    m.window_features(ref, np.full_like(batch, 100), o[:width], o[:width], offset)
    np.testing.assert_allclose(got, m.window_features(ref, batch, o[:width], o[:width], offset), rtol=0, atol=0, equal_nan=True)


@pytest.mark.parametrize("h", [4, 8, 12, 24])
def test_larger_window_features_unchanged_by_one_hour_calibration(data, h):
    m, ref, x, o = data
    base = FineWindowModel()
    expected = base.sequence_features(ref, x[8064:], o[8064:], o[8064:], h)
    actual = m.sequence_features(ref, x[8064:], o[8064:], o[8064:], h)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=0, equal_nan=True)


def test_no_monitoring_rows_are_stored_in_reference(data):
    m, ref, x, o = data
    before = pickle.dumps(ref)
    m.window_features(ref, x[-12:], o[:12], o[:12], 4020)
    assert before == pickle.dumps(ref)
