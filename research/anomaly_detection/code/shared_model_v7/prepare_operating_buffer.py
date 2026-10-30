"""Freeze a declared conservative operating-point check; never auto-promote."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from model_research_v13.data import write
from model_evaluation.data import sha256
from shared_model_v7.protocol import OUT,ALGORITHMS,freeze,atomic_dump
from shared_model_v7.burst_margin import Reference as BurstReference
from shared_model_v7.gap_passthrough import Reference as GapReference


def run(sources,output,margin):
    freeze();dest=Path(output).resolve()
    if not dest.is_relative_to(OUT.resolve()):raise ValueError('Research output only')
    dest.mkdir(parents=True,exist_ok=True);records=[]
    for a in ALGORITHMS:
        p=Path(sources[a]);before=sha256(p);m=joblib.load(p)
        if before!=sha256(p)or m.algorithm!=a:raise ValueError('Model source changed or wrong algorithm')
        if a=='if':m.reference=GapReference(m.reference,float(m.base.threshold))
        m.reference=BurstReference(m.reference,margin)
        target=dest/(a+'_buffered.joblib');atomic_dump(m,target)
        records.append({'algorithm':a,'source':str(p),'source_sha256':before,'target':target.name,
                        'sha256':sha256(target),'declared_volume_margin':margin,'gap_core_retained':a=='if'})
    write(dest/'predeclared_operating_check.json',{'models':records,'threshold':1.,
        'margin_frozen_before_full_replay':True,'not_blind_benchmark':True,'no_model_release':True})
    print('OPERATING BUFFER FROZEN',margin,dest,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--margin',type=float,default=.025)
    for a in ALGORITHMS:p.add_argument('--'+a+'-model',type=Path,required=True)
    args=p.parse_args();run({a:getattr(args,a+'_model')for a in ALGORITHMS},args.output,args.margin)
