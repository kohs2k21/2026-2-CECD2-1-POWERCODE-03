import numpy as np
import pandas as pd

from .features import normalize
from .native_history import NativeHistory,NATIVE_FEATURES
from .test_features import rows


def test_native_summary_features_preserve_process_rows_and_causality():
    f=normalize(rows(30));full=NativeHistory().transform(f)
    prefix=NativeHistory().transform(f.iloc[:20])
    assert len(f)==len(full)
    np.testing.assert_array_equal(full.event_row_id,f.event_row_id)
    np.testing.assert_allclose(full.iloc[:20][list(NATIVE_FEATURES)],prefix[list(NATIVE_FEATURES)])


def test_native_stream_equivalent_without_fabricating_missing_minutes():
    f=normalize(rows(30));full=NativeHistory().transform(f)
    history=NativeHistory()
    streamed=pd.concat([history.transform(f.iloc[:20]),history.transform(f.iloc[20:])])
    np.testing.assert_allclose(full[list(NATIVE_FEATURES)],streamed[list(NATIVE_FEATURES)])
    # Actual observed-minute reference, not 30 invented input observations.
    assert len(history.states)==1
    state=next(iter(history.states.values()))
    assert len(state['latency_prefix'])==6 and len(state['count_prefix'])==8
