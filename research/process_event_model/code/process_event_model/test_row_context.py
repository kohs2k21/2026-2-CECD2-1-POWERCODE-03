import numpy as np
import pandas as pd
import pytest

from .features import normalize
from .row_context import RowContext, ExactReference, ROW_FEATURES
from .test_features import rows


def test_row_context_preserves_rows_and_is_causal():
    raw=rows(80)
    ref=ExactReference.fit(normalize(raw))
    a=RowContext(ref).transform(raw)
    changed=raw.copy();changed.loc[60:,'endTime']+=pd.Timedelta(seconds=10)
    b=RowContext(ref).transform(changed)
    np.testing.assert_array_equal(a.loc[:59,list(ROW_FEATURES)],b.loc[:59,list(ROW_FEATURES)])
    assert len(a)==len(raw)
    assert a.transactionId.tolist()==raw.transactionId.tolist()


def test_row_context_streaming_equivalence():
    raw=rows(80)
    ref=ExactReference.fit(normalize(raw))
    a=RowContext(ref).transform(raw)
    processor=RowContext(ref)
    b=pd.concat([processor.transform(raw.iloc[:40]),processor.transform(raw.iloc[40:])])
    np.testing.assert_array_equal(a[list(ROW_FEATURES)],b[list(ROW_FEATURES)])


def test_exact_counts_not_count_bins():
    a=rows(40).assign(totalCount=8)
    b=rows(40).assign(totalCount=9,transactionId=[f'B{i}' for i in range(40)])
    b.endTime+=pd.Timedelta(seconds=1)
    train=normalize(pd.concat([a,b],ignore_index=True))
    ref=ExactReference.fit(train)
    query=normalize(a.iloc[:1].assign(totalCount=9))
    r=ref.transform(query)
    assert r[0,0]<-1
    assert r[0,2]==0


def test_unseen_adapter_fallback_finite():
    raw=rows(40)
    ref=ExactReference.fit(normalize(raw))
    f=RowContext(ref).transform(raw.assign(adapterType='NEW'))
    assert f.reference_level.eq(4).all()
    assert np.isfinite(f[list(ROW_FEATURES)].to_numpy()).all()


def test_timestamp_tie_order_invariance():
    raw=rows(40);raw['availableTime']=raw.endTime.dt.ceil('5min')
    ref=ExactReference.fit(normalize(raw))
    a=RowContext(ref).transform(raw)
    b=RowContext(ref).transform(raw.iloc[::-1]).set_index('transactionId').loc[a.transactionId]
    np.testing.assert_allclose(a[list(ROW_FEATURES)],b[list(ROW_FEATURES)])


@pytest.mark.parametrize('algorithm',['if','kmeans','lof'])
def test_backend_row_context_dispatch(tmp_path,algorithm):
    import joblib
    from .inference import load
    from .models import Detector,Settings
    raw=rows(80);ref=ExactReference.fit(normalize(raw))
    f=RowContext(ref).transform(raw)
    model=Detector(Settings(algorithm,'row_context',clusters=4,neighbors=5)).fit(f)
    model.threshold=float(np.quantile(model.raw_score(f),.95))
    path=tmp_path/'event.joblib'
    joblib.dump(dict(reference=ref,models={algorithm:model}),path)
    result=load(path,algorithm).predict(raw)
    assert len(result)==len(raw)
    assert result.transactionId.tolist()==raw.transactionId.tolist()
    np.testing.assert_array_equal(result.score,model.raw_score(f))
