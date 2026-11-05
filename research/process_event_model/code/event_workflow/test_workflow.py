"""Invented examples only; no original/benchmark inputs are required."""
import numpy as np
import pandas as pd
import pytest

from .__main__ import binary, evaluate_rows, evaluate_minutes, minute_predictions


def invented_rows(n=24, day='2026-01-03'):
    t = pd.date_range(day + ' 09:00', periods=n, freq='min')
    return pd.DataFrame(dict(event_row_id=np.arange(n), transactionId=[f'DEMO_TX_{i}' for i in range(n)],
        processId='DEMO_PROCESS', adapterType='DEMO.ADAPTER', startTime=t,
        endTime=t+pd.to_timedelta(90+np.arange(n)%5,unit='ms'), totalCount=1,
        successCount=1,errorCount=0,retryCount=0,status='S',case_id='invented_example'))


def test_keyed_row_metrics():
    predictions=pd.DataFrame({'event_row_id':[1,2,3,4], 'anomaly':[0,1,0,1]})
    labels=pd.DataFrame({'event_row_id':[4,3,2,1], 'is_anomaly':[1,1,0,0]})
    result=evaluate_rows(predictions,labels)
    assert result['f1']==result['accuracy']==result['false_positive_rate']==.5
    with pytest.raises(ValueError,match='exactly one'):
        evaluate_rows(predictions,labels.iloc[:-1])
    with pytest.raises(ValueError):
        evaluate_rows(predictions,pd.concat([labels,labels.iloc[:1]]))


def test_binary_rejects_strings_and_missing_truth():
    for values in ([0,None],['False','True'],[0,2]):
        with pytest.raises(ValueError):binary(pd.Series(values),'truth')


def test_minute_truth_requires_identity_and_exact_join():
    p=pd.DataFrame({'case_id':['c']*4,'adapterType':['a']*4,'processId':['p']*4,
        'window_start':pd.date_range('2026-01-01',periods=4,freq='min'),'anomaly':[0,1,0,1]})
    y=p.drop(columns='anomaly').assign(is_anomaly=[0,0,1,1])
    assert evaluate_minutes(p,y.iloc[::-1])['f1']==.5
    with pytest.raises(ValueError,match='exactly one'):evaluate_minutes(p,y.iloc[:-1])


def test_portable_cli_row_and_minute_evaluation(tmp_path):
    import json
    from pathlib import Path
    from .__main__ import main
    path=Path(__file__).resolve().parents[2]/'models/latest_process_rows/event_models.joblib'
    if not path.exists():pytest.skip('Runs in the exported package')
    raw=invented_rows()
    source=tmp_path/'process.json'
    raw.to_json(source,orient='records',date_format='iso')
    labels=tmp_path/'truth.json'
    raw[['event_row_id']].assign(is_anomaly=0).to_json(labels,orient='records')
    output=tmp_path/'row.json'
    base=['--model',str(path),'--algorithm','if','--processes',str(source),'--trust-model']
    main(['evaluate',*base,'--labels',str(labels),'--output',str(output)])
    assert json.loads(output.read_text())['metrics']['n']==len(raw)
    minute=tmp_path/'minute.json'
    pd.DataFrame({'case_id':'invented_example','adapterType':'DEMO.ADAPTER','processId':'DEMO_PROCESS',
        'window_start':pd.date_range('2026-01-03 09:00',periods=26,freq='min'),'is_anomaly':0}).to_json(minute,orient='records',date_format='iso')
    out=tmp_path/'minute_eval.json'
    main(['evaluate',*base,'--labels',str(minute),'--unit','minute','--output',str(out)])
    assert json.loads(out.read_text())['metrics']['n']==26
    with pytest.raises(SystemExit):
        main(['predict',*base,'--output',str(output)])


@pytest.mark.parametrize('algorithm',['if','kmeans','lof'])
def test_published_model_raw_stream_and_minute_replay(algorithm):
    from pathlib import Path
    from process_event_model.inference import load
    root=Path(__file__).resolve().parents[2]
    path=root/'models/latest_process_rows/event_models.joblib'
    if not path.exists():pytest.skip('Runs in the exported package')
    raw=invented_rows()
    full=load(path,algorithm,with_incidents=True)
    result=full.predict(raw)
    live=load(path,algorithm,with_incidents=True)
    parts=[live.predict(raw.iloc[:12]),live.predict(raw.iloc[12:])]
    np.testing.assert_allclose(result.score,pd.concat(parts).score)
    assert result.transactionId.tolist()==raw.transactionId.tolist()
    assert len(result)==len(raw) and np.isfinite(result.score).all()
    assert result.reference_level.eq(4).all()  # Entirely unseen Adapter/Process
    windows=pd.DataFrame({'case_id':'invented_example','adapterType':'DEMO.ADAPTER','processId':'DEMO_PROCESS',
        'window_start':pd.date_range('2026-01-03 09:00',periods=28,freq='min')})
    projected=minute_predictions(result,windows,full)
    # Independently replay actual runtime calls and snapshots, not feature-only inference.
    query=load(path,algorithm,with_incidents=True)
    clock=pd.to_datetime(raw.endTime).dt.tz_localize('Asia/Seoul')
    seen=np.zeros(len(raw),bool); actual=[]
    for start in pd.to_datetime(windows.window_start).dt.tz_localize('Asia/Seoul'):
        end=start+pd.Timedelta(minutes=1)
        take=(clock<end).to_numpy()&~seen
        hit=False
        if take.any():
            hit=bool(query.predict(raw.loc[take]).confirmed_anomaly.any());seen|=take
        actual.append(hit or len(query.incident_snapshot(end))>0)
    np.testing.assert_array_equal(projected.anomaly,actual)
