import numpy as np
import pandas as pd
import pytest

from .adaptive_event import AdaptiveHistory,ADAPTIVE_FEATURES
from .features import normalize
from .test_features import rows


def test_adaptive_keeps_real_rows_and_past_prefix_causal():
    f=normalize(rows(35))
    all_features=AdaptiveHistory().transform(f)
    before=AdaptiveHistory().transform(f.iloc[:20])
    assert len(all_features)==len(f)
    np.testing.assert_allclose(before[list(ADAPTIVE_FEATURES)],all_features.iloc[:20][list(ADAPTIVE_FEATURES)])
    np.testing.assert_array_equal(all_features.event_row_id,f.event_row_id)


def test_adaptive_streaming_is_batch_equivalent():
    f=normalize(rows(35))
    expected=AdaptiveHistory().transform(f)
    processor=AdaptiveHistory()
    actual=pd.concat([processor.transform(f.iloc[:20]),processor.transform(f.iloc[20:])])
    np.testing.assert_allclose(actual[list(ADAPTIVE_FEATURES)],expected[list(ADAPTIVE_FEATURES)])


def test_adaptive_rejects_replayed_old_arrival():
    f=normalize(rows(10));state=AdaptiveHistory();state.transform(f)
    with pytest.raises(ValueError,match='Out-of-order'):
        state.transform(f)
