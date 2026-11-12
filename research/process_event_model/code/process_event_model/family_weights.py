"""Evidence-family fusion tuning around ONE normal-fitted sklearn estimator."""
import argparse
import copy
import json
from pathlib import Path

import joblib
import numpy as np

from .adaptive_models import ConfidenceNativeDetector
from .data import ROOT,write_json,digest
from .minute_refinement import MinuteReplay,EventPolicy,quality,results,ACTIVE_TASKS,ALGORITHMS


class FamilyConfidenceDetector(ConfidenceNativeDetector):
    def __init__(self,base,model_weight=.15):
        super().__init__(base,model_weight)
        self.family_weights=np.full(8,model_weight)
        self.columns=tuple(dict.fromkeys([*self.columns,'reference_level']))

    def components(self,f,raw=None):
        group,stat=self.state(f)
        if raw is None:raw=self.base.raw_score(f)
        learned=np.zeros(len(f))
        for g in range(8):
            mask=group==g
            center,scale=self.model_stats.get(g,self.default_stats)
            learned[mask]=np.log1p(np.maximum((raw[mask]-center)/scale,0.))
        w=self.family_weights[group]
        return group,w*learned+(1-w)*stat

    def record(self):
        return {**super().record(),'family_weights':self.family_weights.tolist(),
                'one_shared_estimator_not_eight_experts':True}


def run(source,output):
    output.mkdir(exist_ok=True,parents=True)
    package=joblib.load(source/'event_models.joblib')
    old=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib')
    f=data['frame'];replay=MinuteReplay(f,data['windows'])
    cal=joblib.load(ROOT/'reports/process_event_model/native_event/original_features.joblib')['partitions']['calibration']
    models={};chosen={}
    for a in ALGORITHMS:
        parent=package['models'][a]
        model=FamilyConfidenceDetector(parent.base,parent.model_weight).fit_calibration(cal)
        model.cutoffs=parent.cutoffs.copy()
        if hasattr(parent,'family_weights'):model.family_weights=parent.family_weights.copy()
        raw=parent.base.raw_score(f);cr=parent.base.raw_score(cal)
        g,stat=model.state(f);cg,cstat=model.state(cal)
        def learned(values,groups):
            out=np.zeros(len(values))
            for k in range(8):
                center,scale=model.model_stats.get(k,model.default_stats)
                mask=groups==k;out[mask]=np.log1p(np.maximum((values[mask]-center)/scale,0.))
            return out
        learn=learned(raw,g);clearn=learned(cr,cg)
        params=dict(old['models'][a]['policy'])
        def evaluate(w,c,p):
            score=w[g]*learn+(1-w[g])*stat
            rows=results(data,replay.project(score/np.maximum(c[g],1e-8),EventPolicy(**p)))
            return quality(rows,{t:data['legacy'][t][a] for t in ACTIVE_TASKS}),rows
        weights=model.family_weights.copy();cuts=model.cutoffs.copy()
        value,rows=evaluate(weights,cuts,params)
        clean=~f.label.to_numpy(bool)
        for sweep in range(3):
            changed=False
            for mode in (3,0,1,2):
                for weight in (.03,.08,.15,.25,.35,.5,.65,.8,1.):
                    w=weights.copy();w[mode]=weight
                    values=(weight*learn+(1-weight)*stat)[(g==mode)&clean]
                    orig=(weight*clearn+(1-weight)*cstat)[cg==mode]
                    choices=np.maximum(np.unique(np.r_[cuts[mode],
                        np.quantile(values,[.5,.7,.85,.9,.94,.97,.98,.99,.995,.999]) if len(values) else [],
                        np.quantile(orig,[.85,.94,.98,.995]) if len(orig) else []]),1e-6)
                    for cutoff in choices:
                        c=cuts.copy();c[mode]=cutoff;q,r=evaluate(w,c,params)
                        if q>value+1e-10:weights=w.copy();cuts=c;value=q;rows=r;changed=True
            policies=dict(traffic_excess_gate=(0.,.1,.3,.5,1.,2.),traffic_ttl=(0.,30.,60.,90.,120.,180.,300.),
                traffic_quiet_clear=(0,1,2,3),point_tail_gate=(1e9,2.,3.,4.6,6.),point_shift_gate=(1.,3.,6.,10.))
            for key,values in policies.items():
                for option in values:
                    p=dict(params);p[key]=option;q,r=evaluate(weights,cuts,p)
                    if q>value+1e-10:params=p;value=q;rows=r;changed=True
            print('FAMILY FUSION',a,sweep,{t:round(m['f1'],4) for t,m in rows.items()},flush=True)
            if not changed:break
        model.family_weights=weights;model.cutoffs=cuts;models[a]=model
        chosen[a]=dict(policy=params,weights=weights.tolist(),cutoffs=cuts.tolist(),tasks=rows,objective=value)
        write_json(output/'progress.json',dict(models=chosen))
    joblib.dump({**package,'models':models},output/'event_models.joblib',compress=3)
    write_json(output/'selection.json',dict(models=chosen,source_sha256=digest(source/'event_models.joblib'),
        development_first400_cases_only=True,one_estimator_per_algorithm=True,no_task_routing=True))
    joblib.dump(data,output/'minute_development.joblib',compress=3)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=ROOT/'reports/process_event_model/corroborated_event')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/process_event_model/family_event')
    from process_event_model.family_weights import run as canonical_run
    args=parser.parse_args();canonical_run(args.source,args.output)
