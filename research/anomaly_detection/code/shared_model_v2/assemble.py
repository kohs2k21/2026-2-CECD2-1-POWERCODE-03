"""Bundle already frozen single detectors; no fitting or threshold selection."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse
import joblib
from model_evaluation.data import sha256
from model_research_v13.data import read,write


def assemble(output,lof_folder,if_folder):
    output=Path(output).resolve();output.mkdir(exist_ok=True)
    if(output/'selection.json').exists():raise FileExistsError('Frozen bundle exists')
    base=ROOT/'reports/shared_model_v2_reference';selection=read(base/'selection.json')
    assert sha256(base/'models.joblib')==selection['model_sha256']
    pkg=joblib.load(base/'models.joblib');sources={'kmeans':{'folder':str(base.relative_to(ROOT)),'sha256':selection['model_sha256']}}
    for a,folder in(('if',if_folder),('lof',lof_folder)):
        folder=Path(folder).resolve();s=read(folder/'selection.json')
        if(folder/(a+'_selected.joblib')).exists():
            path=folder/(a+'_selected.joblib');assert sha256(path)==s['model_sha256'];model=joblib.load(path);r=s['selected']
        else:
            path=folder/'models.joblib';assert sha256(path)==s['model_sha256'];model=joblib.load(path)['models'][a];r=s['selected'][a]
        pkg['models'][a]=model;selection['selected'][a]=r
        sources[a]={'folder':str(folder.relative_to(ROOT)),'sha256':sha256(path)}
    joblib.dump(pkg,output/'models.joblib',compress=3)
    # Historical search protocols differ between stages; do not inherit an
    # obsolete blanket constraint as a statement about the assembled models.
    selection.pop('FPR_development_tolerance_absolute',None)
    selection.pop('pooled_development_FPR_no_higher_than_original',None)
    selection.pop('LOF_development_FPR_cap_ratio_to_original',None)
    selection['actual_development_FPR_comparison']={a:{
        'previous':selection['baseline_development'][a]['normal_false_positive_rate'],
        'selected':r['normal_false_positive_rate']}for a,r in selection['selected'].items()}
    selection.update({'model_sha256':sha256(output/'models.joblib'),'frozen_model_sources':sources,
       'assembly_performs_no_training':True,'ADBench_not_used_for_selection':True})
    write(output/'selection.json',selection);write(output/'preparation.json',read(base/'preparation.json'))
    print('Frozen bundle assembled',sources,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--lof-folder',type=Path,default=ROOT/'reports/shared_model_v2_geometry')
    p.add_argument('--if-folder',type=Path,default=ROOT/'reports/shared_model_v2_if')
    args=p.parse_args();assemble(args.output,args.lof_folder,args.if_folder)
