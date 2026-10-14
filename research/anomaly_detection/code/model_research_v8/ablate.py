"""Fixed-setting branch ablations on NEW confirmation cases, no reselection."""
import copy,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
from model_evaluation.tune_windows import write_json
from model_research_v8.recalibrate import frozen,load,OUTPUT
from model_research_v8.bridge import cache,summarize


def main():
    suite,_=frozen();data=load();result={}
    for algorithm,original in suite.families.items():
        result[algorithm]={}
        cached=cache(original,data['bank'],'confirmation')
        result[algorithm]['complete']=summarize(original,cached)
        for mode in ('baseline_only','conditional_only','no_cadence_gate','no_joint_calibration'):
            family=copy.deepcopy(original)
            values=copy.deepcopy(cached)
            if mode=='no_cadence_gate':
                for name,frame in (('calibration',family.calibration),('threshold',family.threshold)):
                    frame=frame.copy();frame['traffic_assessed']=True;setattr(family,name,frame)
                    family.normal[name]=family.components(frame)[0]
                bank={n:{c:f.copy() for c,f in d.items()} for n,d in data['bank'].items()}
                for d in bank.values():
                    for f in d.values():f['traffic_assessed']=True
                values=cache(family,bank,'confirmation')
            elif mode=='no_joint_calibration':
                for route in family.joint:family.joint[route]=np.zeros(3)
            else:
                section=slice(3,5) if mode=='baseline_only' else slice(0,3)
                for name in family.normal:family.normal[name][:,section]=0.
                for pairs in values.values():
                    for raw,adapters,covered,y in pairs:raw[:,section]=0.
            if mode!='no_joint_calibration':
                # Recalibrate only on normal references, using identical q and
                # weights. No tuning or confirmation-label fitting.
                family.set_operating_point(original.latency_quantile,original.joint_quantile,original.weight,original.gamma,original.mode)
            result[algorithm][mode]=summarize(family,values)
    write_json(OUTPUT/'ablations.json',{'results':result,'confirmation_only':True,'settings_fixed':True,
                                     'normal_threshold_recalibration_only':True,'not_used_for_model_selection':True})
    print({a:{k:[round(m['f1']*100,2) for m in v['pooled']] for k,v in modes.items()} for a,modes in result.items()})


if __name__=='__main__':main()
