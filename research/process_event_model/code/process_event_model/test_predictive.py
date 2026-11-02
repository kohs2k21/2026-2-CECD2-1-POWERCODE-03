import numpy as np
import pandas as pd

from .features import normalize
from .predictive import PredictiveHistory,NEW_FEATURES
from .test_features import rows


def prepared(n=80):
    return normalize(rows(n))


def test_predictive_preserves_rows_and_past_only():
    f=prepared()
    a=PredictiveHistory().transform(f)
    changed=f.copy();changed.loc[50:,'availableTime']+=pd.Timedelta(days=1)
    b=PredictiveHistory().transform(changed)
    assert len(a)==len(f)
    np.testing.assert_array_equal(a.loc[:49,list(NEW_FEATURES)],b.loc[:49,list(NEW_FEATURES)])


def test_predictive_streaming_matches_batch():
    f=prepared()
    a=PredictiveHistory().transform(f)
    processor=PredictiveHistory()
    b=pd.concat([processor.transform(f.iloc[:20]),processor.transform(f.iloc[20:])])
    np.testing.assert_allclose(a[list(NEW_FEATURES)],b[list(NEW_FEATURES)])


def test_baseline_is_disjoint_and_current_event_is_excluded():
    f=prepared(15)
    x=PredictiveHistory().transform(f)
    # At t=9min with a 3min lookback, baseline is [0,6), excluding the founder.
    assert x.predictive_exposure_3m.iloc[9]==6
    assert x.predictive_support_3m.iloc[9]==5
    assert x.predictive_exposure_3m.iloc[0]==0
    assert x.predictive_burst_3m.iloc[0]==0


def test_dense_arrivals_are_more_surprising_than_regular_history():
    f=prepared(20)
    t=f.availableTime.iloc[10]
    f.loc[11:,'availableTime']=[t+pd.Timedelta(seconds=i*3) for i in range(1,10)]
    result=PredictiveHistory().transform(f)
    assert result.predictive_burst_1m.iloc[-1]>result.predictive_burst_1m.iloc[9]+2


def test_timestamp_batches_and_label_poisoning():
    f=prepared();f['availableTime']=f.availableTime.dt.ceil('2min')
    a=PredictiveHistory().transform(f)
    b=PredictiveHistory().transform(f.assign(label=1,event_id='SECRET').iloc[::-1]).sort_values('event_row_id')
    np.testing.assert_allclose(a[list(NEW_FEATURES)],b[list(NEW_FEATURES)])


def test_frozen_core_keeps_cold_scores_and_one_estimator(tmp_path):
    import joblib
    from .calibration import CalibratedEventDetector
    from .row_context import ExactReference,RowContext
    from .models import Detector,Settings
    from .predictive_experiment import FrozenCorePredictiveDetector
    from .inference import load
    raw=rows(80);ref=ExactReference.fit(normalize(raw))
    f=PredictiveHistory().transform(RowContext(ref).transform(raw))
    base=Detector(Settings('if','row_compact')).fit(f)
    parent=CalibratedEventDetector(base,.25).fit_calibration(f)
    parent.cutoffs[:]=1.5
    model=FrozenCorePredictiveDetector(parent,.75).fit_calibration(f)
    assert model.base.estimator is model.parent.base.estimator
    cold=f.history_support.to_numpy(float)<np.log1p(3)
    np.testing.assert_allclose(model.predict(f)[0][cold],parent.predict(f)[0][cold])
    path=tmp_path/'new.joblib'
    joblib.dump(dict(reference=ref,models={'if':model},processor='predictive_row_context'),path)
    actual=load(path,'if').predict(raw)
    np.testing.assert_allclose(actual.score,model.predict(f)[0])
    assert len(actual)==len(raw)
