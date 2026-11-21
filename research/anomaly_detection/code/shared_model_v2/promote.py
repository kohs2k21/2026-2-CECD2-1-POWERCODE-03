"""Copy a proven frozen candidate into one clear local release directory."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,shutil
from model_evaluation.data import sha256
from model_research_v13.data import read,write


def promote(source):
    source=Path(source).resolve();dest=ROOT/'reports/shared_model_v2_release'
    if dest.exists():raise FileExistsError('Release exists; do not overwrite')
    summary=read(source/'summary.json');old=read(ROOT/'reports/shared_model_target_lof/summary.json');selected=read(source/'selection.json')
    assert sha256(source/'models.joblib')==selected['model_sha256']
    for before,after in zip(old['pooled'],summary['pooled']):
        assert before['method']==after['method']
        assert after['f1']>=.55 and after['normal_false_positive_rate']<=before['normal_false_positive_rate']
    for name in('fresh_confirmation_v2.json','adbench_results.json'):
        assert read(source/name)['model_sha256']==selected['model_sha256']
    names=['models.joblib','selection.json','preparation.json','summary.json','analysis_samples.joblib',
       'fresh_confirmation_v2.json','fresh_confirmation_v2_inputs.json','fresh_confirmation_v2_inputs.joblib',
       'adbench_results.json','adbench_scores.npz']
    names.extend(d['dataset']+'_scores.npz'for d in summary['datasets'])
    assert all((source/n).is_file()for n in names)
    dest.mkdir()
    files=[]
    for name in names:
        shutil.copy2(source/name,dest/name);assert sha256(source/name)==sha256(dest/name);files.append(name)
    (dest/'handoff').mkdir()
    for p in (source/'handoff').iterdir():
        if p.is_file():shutil.copy2(p,dest/'handoff'/p.name)
    write(dest/'release.json',{'source':str(source.relative_to(ROOT)),'model_sha256':selected['model_sha256'],
       'all_three_F1_at_least_55_percent':True,'all_three_FPR_no_increase':True,'originals_preserved':True,
       'files':{name:sha256(dest/name)for name in names},'production_deployed':False,'GitHub_pushed':False})
    print('Created local release with strict F1/FPR requirements',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);promote(p.parse_args().source)
