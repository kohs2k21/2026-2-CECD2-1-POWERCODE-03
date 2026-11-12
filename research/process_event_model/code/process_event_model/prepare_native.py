import joblib
import numpy as np

from .native_history import NativeHistory,NATIVE_FEATURES
from .data import ROOT,write_json,digest
from .experiment_v2 import actual_session_days

OUT=ROOT/'reports/process_event_model/native_event'


def prepare(clock=False):
    output=ROOT/'reports/process_event_model'/('clock_event' if clock else 'native_event')
    output.mkdir(parents=True,exist_ok=True)
    source=ROOT/'reports/process_event_model/predictive_counts'
    path=output/'original_features.joblib'
    if not path.exists():
        package=joblib.load(source/'original_features.joblib')
        raw=joblib.load(source.parent/'original_rows.joblib')
        dates=raw.availableTime.dt.tz_convert('Asia/Seoul').dt.normalize().astype(str)
        context=raw.loc[dates.isin(actual_session_days(raw))].copy()
        context['case_id']=context.source_period.astype(str)+':'+dates.loc[context.index]
        print('Native features actual context',len(context),flush=True)
        extra=NativeHistory(include_empty_reference=clock).transform(context).set_index('event_row_id')
        parts={}
        for name,f in package['partitions'].items():
            x=f.copy()
            for c in NATIVE_FEATURES:x[c]=extra.loc[f.event_row_id,c].to_numpy()
            assert not x.benchmark_source.any()
            parts[name]=x
        joblib.dump(dict(reference=package['reference'],partitions=parts),path,compress=3)
        write_json(output/'provenance.json',dict(actual_context_rows=len(context),new_Process_rows=0,
            summary_training_rows_generated=0,empty_minute_reference_rows_generated=0,
            zero_count_reference_slots=clock,unknown_gaps_above_20min_reset_count_reference=clock,
            prefix_healthy_guarantee=False,code_sha256=digest(ROOT/'code/process_event_model/native_history.py')))
    path=output/'development.joblib'
    if not path.exists():
        bank=joblib.load(source/'development.joblib');out={}
        for t,f in bank.items():
            x=NativeHistory(include_empty_reference=clock).transform(f)
            np.testing.assert_array_equal(x.event_row_id,f.event_row_id)
            out[t]=x
            print('Native development',t,len(x),flush=True)
        joblib.dump(out,path,compress=3)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--clock',action='store_true')
    prepare(parser.parse_args().clock)
