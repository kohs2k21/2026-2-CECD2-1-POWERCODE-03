"""Optional event-driven open/clear incident metadata, never fake Process rows."""
import numpy as np
import pandas as pd

from .features import timestamps


class IncidentTracker:
    def __init__(self,ttl_seconds=300,context_min=0.,fast_traffic=False):
        self.ttl_seconds=float(ttl_seconds)
        self.context_min=float(context_min)
        self.fast_traffic=bool(fast_traffic)
        self.states={}

    def update(self,predictions,detector):
        f=predictions.copy().reset_index(drop=True)
        family=detector.state(f)[0]%4
        warm=f.history_support.to_numpy(float)>=np.log1p(3)
        contextual=np.isin(family,[1,2])
        if self.context_min>0:contextual=f.local_upper.to_numpy(float)>=self.context_min
        traffic=family==3
        if self.fast_traffic and traffic.any():
            traffic &= f.traffic_lift_1m.to_numpy(float)>=f.traffic_lift_3m.to_numpy(float)
        holds=f.anomaly.to_numpy(bool)&warm&(contextual|traffic)
        now=f.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/1e9
        actions=np.full(len(f),'none',object);active=np.zeros(len(f),bool)
        expiry=np.full(len(f),np.nan)
        for key,ids in f.groupby(['case_id','adapterType','processId'],observed=True,sort=False).indices.items():
            ids=np.asarray(ids);ids=ids[np.argsort(now[ids],kind='stable')]
            for t in np.unique(now[ids]):
                batch=ids[now[ids]==t]
                old=self.states.get(key)
                was_active=old is not None and old['active'] and t<=old['expires']
                has_context=bool(holds[batch].any()) and self.ttl_seconds>0
                if has_context:
                    candidates=[i for i in batch if holds[i]]
                    i=sorted(candidates,key=lambda i:str(f.transactionId.iloc[i]))[0]
                    state=dict(active=True,started=old['started'] if was_active else t,
                               last_observed=t,expires=t+self.ttl_seconds,
                               sourceTransactionId=str(f.transactionId.iloc[i]),
                               sourceProcessId=str(f.processId.iloc[i]))
                    self.states[key]=state
                    actions[batch]='update' if was_active else 'open'
                    active[batch]=True;expiry[batch]=state['expires']
                else:
                    if old is not None:old['active']=False
                    actions[batch]='close' if was_active else 'point' if f.anomaly.iloc[batch].any() else 'none'
        f['incident_action']=actions
        f['incident_active']=active
        f['incident_expires_at']=pd.to_datetime(expiry,unit='s',utc=True)
        return f

    def snapshot(self,now):
        """Only inspect existing incidents; silence after normal cannot open one."""
        clock=timestamps(pd.Series([now])).iloc[0]
        seconds=clock.value/1e9
        output=[]
        for (case,adapter,process),state in self.states.items():
            if not state['active']:continue
            expired=seconds>state['expires']
            if expired:state['active']=False
            output.append(dict(case_id=case,adapterType=adapter,
                sourceProcessId=process,sourceTransactionId=state['sourceTransactionId'],
                status='expired' if expired else 'active',
                expires_at=pd.to_datetime(state['expires'],unit='s',utc=True),
                observation_kind='existing_incident_state_not_Process_row'))
        return pd.DataFrame(output)
