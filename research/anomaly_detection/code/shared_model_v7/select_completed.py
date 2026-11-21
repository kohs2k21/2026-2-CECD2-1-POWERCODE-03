"""Select complete candidates independently per model, never weaken a rival.

No score/threshold tuning here: compare only frozen full-replay summaries.
This is development selection on a repeatedly reused benchmark, not blind.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v7.protocol import OUT,ALGORITHMS,freeze
from shared_model_v7.acceptance import check,policy
from shared_model_v7.role_priority import TARGETS
from shared_model_v7.assemble_attempt import run as assemble


def run(output):
    p=freeze();candidates=[];chosen={}
    for a in ALGORITHMS:
        baseline={'datasets':[{'dataset':d['dataset'],'metrics':[r for r in d['metrics']if r['method']==a]}
                              for d in p['baseline_summary']['datasets']],
                  'pooled':[r for r in p['baseline_summary']['pooled']if r['method']==a]}
        passing=[]
        for source in sorted(OUT.glob('attempt*')):
            path=source/(a+'_summary.json')
            if not path.exists():continue
            s=read(path)
            if not s.get('all_5000_paired_cases_each_task'):continue
            r=check({'datasets':s['datasets'],'pooled':[{'method':a,**s['pooled']}]},baseline,**policy(p))
            target=next(d['metrics'][0]['f1']for d in s['datasets']if d['dataset']==TARGETS[a])
            record={'algorithm':a,'source':str(source.relative_to(ROOT)),'summary_sha256':sha256(path),
                    'passes_latest_metric_policy':r['all_targets_met'],'target_task_F1':target,
                    'pooled_F1':s['pooled']['f1'],'pooled_FPR':s['pooled']['normal_false_positive_rate']}
            candidates.append(record)
            if r['all_targets_met']:passing.append((target,s['pooled']['f1'],-s['pooled']['normal_false_positive_rate'],source))
        if not passing:raise ValueError('No completely passing frozen candidate for '+a)
        chosen[a]=max(passing,key=lambda x:x[:3])[-1]
    plan={'candidates':candidates,'chosen':{a:str(v.relative_to(ROOT))for a,v in chosen.items()},
          'policy':'per-model target-task F1, then pooled F1, then lower pooled FPR, AFTER all metric gates',
          'other_algorithm_scores_used_to_degrade_a_rival':False,'no_new_score_or_cutoff_search':True,
          'external_blind_test':False}
    write(OUT/'completed_selection.json',plan)
    assemble(chosen,output)
    print('Frozen independent selections:',plan['chosen'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);run(p.parse_args().output)
