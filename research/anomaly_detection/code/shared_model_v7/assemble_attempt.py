"""Combine independently COMPLETED frozen replays, without rescoring or tuning.

The input of each algorithm must have its own source receipt and full replay.
This does not publish a model or change any score/threshold/benchmark label.
The assembled result must subsequently pass audit_attempt under current scope.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,shutil
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v7.protocol import OUT,BASE,TASKS,ALGORITHMS,freeze
from shared_model_v7.acceptance import check,policy


def run(sources,output):
    protocol=freeze();dest=Path(output).resolve()
    if not dest.is_relative_to(OUT.resolve())or dest.name=='release':raise ValueError('Research attempt only')
    if dest.exists()and any(dest.iterdir()):raise FileExistsError('Preserve existing complete or partial attempt')
    records=[]
    for a in ALGORITHMS:
        source=Path(sources[a]).resolve()
        if not source.is_relative_to(OUT.resolve()):raise ValueError('Frozen v7 research source required')
        selection=read(source/(a+'_selection.json'));summary=read(source/(a+'_summary.json'))
        assert summary['all_5000_paired_cases_each_task']
        assert sha256(source/(a+'_model.joblib'))==selection['model_sha256']==summary['model_sha256']
        # Judge only this independently evaluated algorithm against its own baseline.
        candidate={'datasets':summary['datasets'],'pooled':[{'method':a,**summary['pooled']}]}
        baseline={'datasets':[{'dataset':d['dataset'],'metrics':[r for r in d['metrics']if r['method']==a]}
                              for d in protocol['baseline_summary']['datasets']],
                  'pooled':[r for r in protocol['baseline_summary']['pooled']if r['method']==a]}
        receipt=check(candidate,baseline,**policy(protocol))
        if not receipt['all_targets_met']:raise ValueError('Incomplete metric gates for '+a+': '+str(receipt))
        names=[a+'_model.joblib',a+'_selection.json',a+'_summary.json',
               *(a+'_'+t+'_scores.npz'for t in TASKS)]
        for t in TASKS:
            z=np.load(source/(a+'_'+t+'_scores.npz'));old=np.load(BASE/(t+'_scores.npz'))
            for key in ('y','case_index','adapter'):np.testing.assert_array_equal(z[key],old[key])
            assert np.isfinite(z['scores']).all()
        records.append({'algorithm':a,'source':str(source.relative_to(ROOT)),
                        'files':{n:sha256(source/n)for n in names},'gate_receipt':receipt})
    dest.mkdir(parents=True,exist_ok=True)
    for r in records:
        source=ROOT/r['source']
        for name,digest in r['files'].items():
            if sha256(source/name)!=digest:raise RuntimeError('Source changed while assembling')
            shutil.copy2(source/name,dest/name)
            assert sha256(dest/name)==digest
    write(dest/'assembly.json',{'sources':records,'scores_or_thresholds_changed':False,
        'requires_new_independent_audit':True,'published':False,'external_blind_test':False})
    print('Assembled completed frozen replays; audit still required:',dest,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    for a in ALGORITHMS:p.add_argument('--'+a+'-source',type=Path,required=True)
    args=p.parse_args();run({a:getattr(args,a+'_source')for a in ALGORITHMS},args.output)
