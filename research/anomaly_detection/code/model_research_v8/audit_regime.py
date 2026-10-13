"""Independent v12 labels/metrics/fallback audit and paired-case bootstrap."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import numpy as np
from model_evaluation.data import sha256,verify_benchmark
from model_evaluation.metrics import Counts
from model_evaluation.tune_windows import write_json
from model_improvement.data import BENCHMARK,SOURCE_CACHE
from model_research_v8.audit import metrics
from model_research_v8.regime import frozen,OUTPUT


def main():
    verify_benchmark(BENCHMARK,SOURCE_CACHE);model,selection=frozen()
    s=json.loads((OUTPUT/'summary.json').read_text(encoding='utf-8'));counts=Counts();groups={};blocks=[];fallback=0
    for d in s['datasets']:
        pair=np.zeros((d['case_count'],2,6,4),np.int64);name=d['dataset']
        for cohort,digest in d['arrays_sha256'].items():
            path=OUTPUT/name/f'{cohort}.npz';assert sha256(path)==digest
            with np.load(path,allow_pickle=False) as a,np.load(ROOT/'reports/model_improvement_v4'/name/f'{cohort}.npz',allow_pickle=False) as v4,np.load(ROOT/'reports/model_research_v7'/name/f'{cohort}.npz',allow_pickle=False) as v7:
                for key in ('truth','case_index','window_end_seconds','scenario_bits','adapter','process'):
                    np.testing.assert_array_equal(a[key],v4[key]);np.testing.assert_array_equal(a[key],v7[key])
                y,p=a['truth'],a['prediction'];np.testing.assert_array_equal(p,a['margin']>0);counts.update(y,p)
                unsupported=~a['covered'];fallback+=int(unsupported.sum())
                for start in (0,3):
                    for col in (start+1,start+2):np.testing.assert_array_equal(a['margin'][unsupported,start],a['margin'][unsupported,col])
                for field in ('traffic_assessed','latency_assessed','duration_reference_level','phase'):
                    for value in np.unique(a[field]):groups.setdefault(f'{field}={value}',Counts()).update(y,p,a[field]==value)
                baseline=np.column_stack([v7['prediction'][:,:3],v4['prediction'][:,3:]])
                for vi,pred in enumerate((baseline,p)):
                    for mi,z in enumerate(pred.T):
                        for ci,mask in enumerate((~y&~z,~y&z,y&~z,y&z)):
                            pair[:,vi,mi,ci]+=np.bincount(a['case_index'][mask],minlength=d['case_count'])
        blocks.append(pair)
    for actual,stored in zip(counts.records(),s['pooled']):
        for k in ('tn','fp','fn','tp','n'):assert actual[k]==stored[k]
        for k in ('accuracy','precision','recall','f1','normal_false_positive_rate'):assert abs(actual[k]-stored[k])<1e-12
    rng=np.random.default_rng(20261013);sample=[]
    for _ in range(400):
        values=sum(b[rng.integers(0,len(b),len(b))].sum(axis=0) for b in blocks);sample.append(metrics(values)[1]-metrics(values)[0])
    interval=np.quantile(np.array(sample),[.025,.975],axis=0);names=('accuracy','precision','recall','f1','normal_false_positive_rate')
    result={'decisions':s['pooled'][0]['n'],'fallback_decisions':fallback,'exact_fallback':True,'metrics_recounted':True,
        'labels_times_case_indices_equal_v4_v7':True,'source_reference_template_holdout_preserved':True,
        'architecture_fixed_before_new_confirmation':True,'not_blind_original_evaluation':True,
        'group_metrics':{k:c.records() for k,c in groups.items()},
        'paired_case_bootstrap':[{'method':r['method'],**{k:{'lower_pp':float(interval[0,i,j]*100),'upper_pp':float(interval[1,i,j]*100)} for j,k in enumerate(names)}} for i,r in enumerate(s['pooled'])],
        'bootstrap_note':'400 stratified paired-case resamples; synthetic-case variability only, not shared-source/adaptive/production uncertainty.',
        'frozen_branch_sha256':selection['branch_model_sha256']}
    write_json(OUTPUT/'audit.json',result);verify_benchmark(BENCHMARK,SOURCE_CACHE)
    print({k:v for k,v in result.items() if k not in ('group_metrics','paired_case_bootstrap')})


if __name__=='__main__':main()
