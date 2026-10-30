"""Use ADBench's actual installed generator, not a reimplementation.

Its leaf generation method has no torch/tensorflow dependency. Load that exact
AST node without importing unrelated DL/download utilities. The method's body
is neither patched nor rewritten; package/source hashes are recorded.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import ast,argparse,importlib.metadata,random,warnings
from types import SimpleNamespace
import joblib
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from copulas.multivariate import VineCopula
from copulas.univariate import GaussianKDE
from threadpoolctl import threadpool_limits
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v2.run import prepare,OUT,PARENT
from shared_model_v2.features import MEMORY
from shared_model_target.current_features import CURRENT
from adapter_feature_ablation.features import ADAPTER
from shared_model_improvement.run import ALGORITHMS,_stats
from sklearn.metrics import average_precision_score,roc_auc_score

DATA=ROOT/'data/adbench_1000'
COLUMNS=(*CURRENT,*(c for c in ADAPTER if c!='adapter_workload_z'),*MEMORY)
FLAGS=('cadence_ready','traffic_assessed','latency_assessed','temporal_assessed')


class WritableVineCopula(VineCopula):
    """pandas 3 copy-on-write compatibility, no change to copula numerics."""
    def train_vine(self,tree_type):
        self.tau_mat=np.array(self.tau_mat,copy=True)
        return super().train_vine(tree_type)


def official_method():
    dist=importlib.metadata.distribution('adbench')
    path=Path(dist.locate_file('adbench/datasets/data_generator.py'))
    module=ast.parse(path.read_text(encoding='utf-8'))
    cls=next(n for n in module.body if isinstance(n,ast.ClassDef)and n.name=='DataGenerator')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='generate_realistic_synthetic')
    isolated=ast.Module(body=[method],type_ignores=[])
    namespace={'np':np,'pd':pd,'GaussianMixture':GaussianMixture,'VineCopula':WritableVineCopula,'GaussianKDE':GaussianKDE}
    exec(compile(isolated,str(path),'exec'),namespace)
    return namespace[method.name],{'package':'adbench','version':dist.version,'source_sha256':sha256(path),
        'method':method.name,'method_ast_sha256':__import__('hashlib').sha256(ast.dump(method,include_attributes=False).encode()).hexdigest(),
        'source_url':'https://github.com/Minqi824/ADBench/blob/main/adbench/datasets/data_generator.py',
        'unmodified_official_leaf_method':True,'DL_utils_not_imported':True,
        'copulas_compatibility':'copy tau_mat to writable array at train_vine entry for pandas3; unchanged numeric values'}


def generate():
    DATA.mkdir(exist_ok=True)
    if (DATA/'manifest.json').exists():raise FileExistsError('Frozen ADBench data already exists')
    source=prepare()['train']
    # One observed/ready operating context, so categorical availability flags are
    # not made fractional by a continuous distribution generator.
    source=source.loc[source[list(FLAGS)].astype(bool).all(axis=1)].reset_index(drop=True)
    columns=[c for c in COLUMNS if c not in FLAGS]
    x=source[columns].to_numpy(float);median=np.nan_to_num(np.nanmedian(x,axis=0));x=np.where(np.isnan(x),median,x)
    q10,q90=np.quantile(x,[.1,.9],axis=0);scale=np.maximum(q90-q10,.5)
    variable=np.ptp(x,axis=0)>1e-9
    constants={c:float(x[0,i])for i,c in enumerate(columns)if not variable[i]}
    ids=np.random.default_rng(801000).choice(len(x),min(1600,len(x)),replace=False)
    normalized=((x[ids]-median)/scale)[:,variable]
    # VineCopula assumes continuous marginals; deterministic, tiny tie jitter is
    # generator-only. It never alters the actual detector's training data.
    normalized=normalized+np.random.default_rng(801001).normal(0,1e-5,normalized.shape)
    method,provenance=official_method();frames=[];labels=[];info=[]
    for k,mode in enumerate(('global','local','cluster','dependency')):
        seed=801010+k;np.random.seed(seed);random.seed(seed)
        xin=np.concatenate([normalized,np.zeros((400,normalized.shape[1]))])
        yin=np.r_[np.zeros(len(normalized),int),np.ones(400,int)]
        with warnings.catch_warnings(record=True)as captured,threadpool_limits(limits=2):
            warnings.simplefilter('always')
            values,y=method(SimpleNamespace(seed=seed),xin,yin,mode,alpha=3,percentage=.1)
        if not np.isfinite(values).all():raise ValueError(f'Nonfinite official generator output: {mode}')
        rng=np.random.default_rng(seed+1000)
        picked=np.r_[rng.choice(np.flatnonzero(y==0),200,replace=False),rng.choice(np.flatnonzero(y==1),50,replace=False)]
        rng.shuffle(picked);values,y=values[picked],y[picked].astype(bool)
        restored=np.tile(median,(len(picked),1));restored[:,variable]=values*scale[variable]+median[variable]
        frame=pd.DataFrame(restored,columns=columns)
        for c in FLAGS:frame[c]=True
        frame=frame[list(COLUMNS)]
        frames.append(frame);labels.extend([{'sample_id':f'ADBENCH-{mode}-{i:04d}','generation_mode':mode,'is_anomaly':int(v)}for i,v in enumerate(y)])
        info.append({'mode':mode,'seed':seed,'normal':200,'anomaly':50,'alpha':3,'percentage':.1,
            'warnings':list(dict.fromkeys(str(w.message)for w in captured))[:12]})
        print('ADBench generated',mode,'250 samples',flush=True)
    features=pd.concat(frames,ignore_index=True);answers=pd.DataFrame(labels)
    order=np.random.default_rng(801999).permutation(1000);features=features.iloc[order].reset_index(drop=True);answers=answers.iloc[order].reset_index(drop=True)
    assert features.shape==(1000,len(COLUMNS))and answers.is_anomaly.sum()==200
    np.savez_compressed(DATA/'dataset.npz',X=features.to_numpy(float),y=answers.is_anomaly.to_numpy(bool),columns=np.asarray(COLUMNS),
        sample_id=answers.sample_id.to_numpy(str),generation_mode=answers.generation_mode.to_numpy(str))
    write(DATA/'table_payload.json',{'feature_headers':['sample_id',*COLUMNS],
       'feature_rows':[[i,*row]for i,row in zip(answers.sample_id,features.to_numpy(float).tolist())],
       'label_headers':list(answers.columns),'label_rows':answers.values.tolist()})
    write(DATA/'manifest.json',{'samples':1000,'normal':800,'anomaly':200,'columns':list(COLUMNS),
        'generated_continuous_columns':[c for i,c in enumerate(columns)if variable[i]],'fixed_constants':constants,'fixed_flags':{c:True for c in FLAGS},
        'generator_normal_fit_rows':len(normalized),'source':'normal train only; fully observed/ready minutes',
        'source_development_sha256':read(OUT/'preparation.json')['sha256'],'normal_generator_tie_jitter_sigma':1e-5,
        'standardization':'normal train median and max(P90-P10, .5); inverted after generation',
        'provenance':provenance,'modes':info,'npz_sha256':sha256(DATA/'dataset.npz'),
        'purpose':'additional tabular feature-space stress test; not a Process log replay',
        'not_temporal':True,'raw_SQL_or_hard_rules_not_applicable':True,
        'not_used_for_tuning':True,'original_benchmark_modified':False})
    print('ADBench fixed 1000 samples saved',flush=True)


def evaluate(folder=None):
    output=Path(folder).resolve()if folder is not None else OUT
    manifest=read(DATA/'manifest.json');assert sha256(DATA/'dataset.npz')==manifest['npz_sha256']
    selected=read(output/'selection.json');assert sha256(output/'models.joblib')==selected['model_sha256']
    values=np.load(DATA/'dataset.npz');frame=pd.DataFrame(values['X'],columns=values['columns'])
    for c in FLAGS:frame[c]=frame[c].astype(bool)
    frame['adapterType']='ADBENCH_GLOBAL_CONTEXT';frame['case_id']=values['sample_id']
    y=values['y'];results=[];scores={}
    for version,folder in (('previous',PARENT),('improved',output)):
        package=joblib.load(folder/'models.joblib')
        for a in ALGORITHMS:
            model=package['models'][a];s=model.score(frame);scores[version+'_'+a]=s
            r={'version':version,'method':a,**_stats(y,s,model.threshold),
               'average_precision':float(average_precision_score(y,s)),'roc_auc':float(roc_auc_score(y,s)),
               'threshold':model.threshold,'types':{}}
            for mode in ('global','local','cluster','dependency'):
                mask=values['generation_mode']==mode;r['types'][mode]=_stats(y[mask],s[mask],model.threshold)
            results.append(r)
    np.savez_compressed(output/'adbench_scores.npz',y=y,sample_id=values['sample_id'],generation_mode=values['generation_mode'],**scores)
    write(output/'adbench_results.json',{'samples':1000,'normal':800,'anomaly':200,'results':results,
       'model_sha256':selected['model_sha256'],'data_sha256':manifest['npz_sha256'],
       'frozen_models_and_thresholds':True,'retrained_on_ADBench':False,'used_for_tuning':False,
       'limitation':'feature-space distribution shift; generated normal is not guaranteed physically valid Process data'})
    print([(r['version'],r['method'],round(r['f1']*100,2),round(r['normal_false_positive_rate']*100,2))for r in results],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=('generate','evaluate'),required=True);p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.stage=='generate':generate()
    else:evaluate(args.output)
