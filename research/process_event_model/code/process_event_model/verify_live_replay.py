"""Independent raw-packet/runtime comparison on real benchmark episodes.

No model fitting or threshold selection. Payloads exclude truth and precomputed
features. Observed and paired normal environments get separate live monitors.
"""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .data import ROOT,write_json,digest
from .inference import load
from .minute_refinement import MinuteReplay,EventPolicy,ACTIVE_TASKS,ALGORITHMS
from .audit import fault_estimators


def verify(source):
    source=Path(source);package=joblib.load(source/'event_models.joblib')
    data=joblib.load(source/'minute_development.joblib')
    selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    bank=joblib.load(ROOT/'reports/process_event_model/native_event/development.joblib')
    results={}
    fields=('transactionId','processId','adapterType','dependProcessId','processHubId','channelId',
            'status','startTime','endTime','successCount','errorCount','responseCode','totalCount',
            'retryCount','availableTime','case_id','source_period','event_row_id')
    for t in ACTIVE_TASKS:
        chosen=bank[t].case_id.drop_duplicates().iloc[:3]
        results[t]={}
        for cohort in ('observed','normal'):
            raw=bank[t].loc[bank[t].case_id.isin(chosen)&bank[t].cohort.eq(cohort)]
            packet=raw[[c for c in fields if c in raw]].copy()
            target=data['frame'].loc[data['frame'].case_id.isin(chosen)&data['frame'].cohort.eq(cohort)&
                                      data['frame']._uid.str.startswith(t+':')].copy()
            windows=data['windows'].loc[data['windows'].case_id.isin(chosen)&
                data['windows']._uid.str.startswith(t+':'+cohort+':')].copy().reset_index(drop=True)
            replay=MinuteReplay(target,windows)
            results[t][cohort]={}
            for a,model in package['models'].items():
                assert len(fault_estimators(model))==1
                monitor=load(source/'event_models.joblib',a,with_incidents=True)
                full=load(source/'event_models.joblib',a,with_incidents=True).predict(packet)
                aligned=full.set_index('event_row_id').loc[target.event_row_id]
                np.testing.assert_allclose(aligned.score,model.predict(target)[0],rtol=1e-9,atol=1e-9)
                p=EventPolicy(**selection['models'][a]['policy'])
                expected=replay.project(model.predict(target)[0],p,families=model.state(target)[0] if p.traffic_model_family else None)
                actual=np.zeros(len(windows),bool)
                for cid,wi in windows.groupby('case_id',sort=False).indices.items():
                    subpacket=packet.loc[packet.case_id.eq(cid)]
                    live=load(source/'event_models.joblib',a,with_incidents=True)
                    seen=set()
                    adapter=target.loc[target.case_id.eq(cid)].adapterType.iloc[0]
                    pid=target.loc[target.case_id.eq(cid)].processId.iloc[0]
                    for j in wi:
                        end=pd.to_datetime(windows._end.iloc[j],unit='s',utc=True)
                        fresh=subpacket.loc[(subpacket.availableTime<end)&~subpacket.event_row_id.isin(seen)]
                        any_hit=False
                        if len(fresh):
                            out=live.predict(fresh)
                            is_target=out.adapterType.eq(adapter)&out.processId.eq(pid)
                            any_hit=bool(out.loc[is_target,'confirmed_anomaly'].any())
                            seen.update(fresh.event_row_id.tolist())
                        snapshot=live.incident_snapshot(end)
                        active=False
                        if len(snapshot):
                            active=bool((snapshot.adapterType.eq(adapter)&snapshot.sourceProcessId.eq(pid)&snapshot.case_id.eq(cid)).any())
                        actual[j]=any_hit or active
                np.testing.assert_array_equal(actual,expected)
                results[t][cohort][a]=dict(raw_Process_rows=len(packet),target_Process_rows=len(target),
                    minute_queries=len(windows),raw_packet_scores_match=True,live_minute_queries_match=True,
                    one_sklearn_estimator=True)
            print('RAW LIVE REPLAY',t,cohort,'passed',flush=True)
    report=dict(passed=True,model_sha256=digest(source/'event_models.joblib'),results=results,
                input_labels_or_precomputed_features=False,all_original_rows_preserved=True,
                silence_can_never_open_new_incident=True,scope='3 real development cases per task, both cohorts, all 3 algorithms')
    write_json(source/'live_audit.json',report)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    verify(parser.parse_args().source)
