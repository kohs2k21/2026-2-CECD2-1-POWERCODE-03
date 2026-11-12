"""v10: preserve a proven baseline branch and add conditional-duration evidence.

Prior branch: v7 IF and v4 KMeans, fixed BEFORE this experiment's search.
Conditional branch: normal-only IF/KMeans. Joint score maximum is calibrated
on TWO independent normal source partitions, not a naive binary OR.
"""
import argparse,copy,json,sys
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from model_evaluation.data import sha256,verify_benchmark
from model_evaluation.windowing import PriorHistory
from model_evaluation.tune_windows import write_json,selected_references
from model_evaluation.metrics import Counts,metric_record
from model_evaluation.evaluate import event_results
from model_improvement.data import BENCHMARK,SOURCE_CACHE,SEARCH,validation_inputs
from model_improvement.features import build_features
from model_improvement.refine import with_legacy,frozen as frozen_v4,OUTPUT as V4,ALL_FEATURES as OLD_FEATURES
from model_improvement.run import trusted_load
from model_research_v5.features import context_features
from model_research_v5.run_matched import frozen as frozen_v7
from model_research_v5.run import six_metrics,METRIC_NAMES
from model_research_v5.models import Encoder,RawModel
from model_research_v8.features import FEATURES,LATENCY,add_duration_features
from model_research_v8.models import ResearchSettings,ClusterDistance,Suite,METHODS
from model_research_v8.data import load as load_v8,fingerprints
from model_research_v8.run import OUTPUT as V8,frozen as frozen_v8

OUTPUT=ROOT/"reports/model_research_v10";SEED=12_170_042
VIEWS={"tail2":LATENCY[:2],"tail4":LATENCY[:4]}


class Latent:
    def __init__(self,train,calibration,columns,settings):
        tr=train.loc[train.latency_assessed];ca=calibration.loc[calibration.latency_assessed]
        self.encoder=Encoder.fit(tr,columns)
        self.model=RawModel(settings) if settings.algorithm=="if" else ClusterDistance(settings)
        self.model.fit(self.encoder.transform(tr),self.encoder.transform(ca))

    def raw(self,frame):
        out=np.zeros(len(frame));mask=frame.latency_assessed.to_numpy(bool)
        if mask.any():out[mask]=self.model.score(self.encoder.transform(frame.loc[mask]))
        return out


class Bridge:
    def __init__(self,base,columns,settings):
        self.base,self.columns,self.settings=base,tuple(columns),settings
        self.experts={};self.support=base.support

    def fit(self,parts):
        self.global_model=Latent(parts['train'],parts['calibration'],self.columns,self.settings)
        for adapter in self.base.experts:
            tr,ca=(parts[n].loc[parts[n].adapterType.eq(adapter)] for n in ('train','calibration'))
            if tr.latency_assessed.sum()<80 or ca.latency_assessed.sum()<20:raise ValueError('Changed shared expert support')
            self.experts[adapter]=Latent(tr,ca,self.columns,self.settings)
        self.calibration,self.threshold=parts['calibration'],parts['threshold']
        self.normal={n:self.components(parts[n])[0] for n in ('calibration','threshold')}
        base_cal=self.normal['calibration'][:,:3]
        median,p95=np.quantile(base_cal,[.5,.95],axis=0)
        self.base_spread=np.maximum(p95-median,.1)
        self.set_operating_point(.99,.995,.5,1.,'max')
        return self

    def components(self,frame):
        base,_,covered=self.base.score(frame)
        # Stable-pattern baseline is NOT a traffic-only model: it includes its
        # original duration features too. Cadence gating avoids sparse-stream
        # activity assumptions. Unassessed rows remain in evaluation.
        base=np.where(frame.traffic_assessed.to_numpy(bool)[:,None],base,-1.)
        global_score=self.global_model.raw(frame)
        local=global_score.copy()
        for adapter,expert in self.experts.items():
            mask=frame.adapterType.eq(adapter).to_numpy()
            if mask.any():local[mask]=expert.raw(frame.loc[mask])
        return np.column_stack([base,global_score,local]),covered

    def set_operating_point(self,latency_quantile,joint_quantile,weight,gamma,mode):
        self.latency_quantile,self.joint_quantile,self.weight,self.gamma,self.mode=latency_quantile,joint_quantile,weight,gamma,mode
        self.latency_reference={}
        for adapter in ('GLOBAL',*self.experts):
            ca=self.calibration;th=self.threshold
            mca=ca.latency_assessed.to_numpy(bool,copy=True);mth=th.latency_assessed.to_numpy(bool,copy=True)
            col=3 if adapter=='GLOBAL' else 4
            if adapter!='GLOBAL':mca &= ca.adapterType.eq(adapter).to_numpy();mth &= th.adapterType.eq(adapter).to_numpy()
            a,b=self.normal['calibration'][mca,col],self.normal['threshold'][mth,col]
            cut=max(float(np.quantile(a,latency_quantile)),float(np.quantile(b,latency_quantile)))
            spread=max(float(np.quantile(a,.95)-np.median(a)),.01)
            self.latency_reference[adapter]=(cut,spread)
        self.joint={}
        for adapter in ('GLOBAL',*self.experts):
            values=[]
            for name,frame in (('calibration',self.calibration),('threshold',self.threshold)):
                m=np.ones(len(frame),bool) if adapter=='GLOBAL' else frame.adapterType.eq(adapter).to_numpy()
                raw=self.normal[name][m]
                # Global threshold must use the actual GLOBAL route only.
                route=np.repeat('UNSUPPORTED',len(raw)) if adapter=='GLOBAL' else np.repeat(adapter,len(raw))
                values.append(self.evidence(raw,route))
            cut=np.maximum(np.quantile(values[0],joint_quantile,axis=0),np.quantile(values[1],joint_quantile,axis=0))
            self.joint[adapter]=cut

    def evidence(self,raw,adapters):
        base=np.maximum(raw[:,:3]/self.base_spread,0.)
        cutoff,spread=self.latency_reference['GLOBAL']
        glob=np.maximum((raw[:,3]-cutoff)/spread,0.)
        latent=np.repeat(glob[:,None],3,axis=1)
        for adapter in self.experts:
            mask=adapters==adapter
            if not mask.any():continue
            cutoff,spread=self.latency_reference[adapter]
            local=np.maximum((raw[mask,4]-cutoff)/spread,0.)
            latent[mask,1]=local
            latent[mask,2]=(self.weight*glob[mask]+(1-self.weight)*local if self.mode=='mean' else
                            np.maximum(self.weight*glob[mask],(1-self.weight)*local))
        return np.maximum(base,self.gamma*latent)

    def from_components(self,raw,adapters,phases=None):
        evidence=self.evidence(raw,adapters)
        thresholds=np.repeat(self.joint['GLOBAL'][0],len(raw))[:,None]*np.ones((len(raw),3))
        for adapter in self.experts:
            mask=adapters==adapter
            thresholds[mask,1:]=self.joint[adapter][1:]
        return evidence-thresholds

    def score(self,frame):
        raw,covered=self.components(frame)
        margin=self.from_components(raw,frame.adapterType.to_numpy())
        # EXACT fallback, including baseline spread learned per route.
        margin[~covered,1:]=margin[~covered,0,None]
        return margin,margin>0,covered

    def records(self):
        return [{'algorithm':self.settings.algorithm,'settings':asdict(self.settings),'latency_features':list(self.columns),'route':a,
                 'latency_quantile':self.latency_quantile,'joint_quantile':self.joint_quantile,'global_weight':self.weight,'latency_weight':self.gamma,
                 'combination':self.mode,'latency_raw_threshold':self.latency_reference[a][0],'joint_thresholds':self.joint[a].tolist()}
                for a in self.joint]


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/'preparation.json').exists():return load()
    _,bundle=verify_benchmark(BENCHMARK,SOURCE_CACHE);prior=load_v8()
    _,_,state,_=selected_references(SEARCH,BENCHMARK,SOURCE_CACHE)
    native=validation_inputs(bundle,state,count=360,seed=SEED);history=PriorHistory(BENCHMARK/'reference_history_windows.csv')
    bank,legacy={},{}
    forbidden=set().union(*(set(d['observed'].case_id) for d in prior['bank'].values()))
    for name,d in native.items():
        bank[name],legacy[name]={},{}
        for cohort in ('observed','normal'):
            f=context_features(with_legacy(build_features(d[cohort],d[cohort+'_rows'],history,prior['baseline']),prior['legacy_baselines'],history))
            if set(f.case_id)&forbidden:raise ValueError('v10 validation overlap')
            legacy[name][cohort]=f;bank[name][cohort]=add_duration_features(f,d[cohort+'_rows'],prior['reference'])
    payload={**{k:v for k,v in prior.items() if k not in ('bank','legacy_bank')},'bank':bank,'legacy_bank':legacy}
    joblib.dump(payload,OUTPUT/'development_inputs.joblib',compress=3)
    write_json(OUTPUT/'preparation.json',{'inputs':fingerprints(),'data_sha256':sha256(OUTPUT/'development_inputs.joblib'),'code_sha256':sha256(Path(__file__)),
        'fresh_seed':SEED,'search_cases':1080,'confirmation_cases':1080,'normal_sources_same_as_v8':True,
        'fixed_base_branches':{'if':'v7','kmeans':'v4'},'not_blind_original_test':True})
    return payload


def load():
    r=json.loads((OUTPUT/'preparation.json').read_text(encoding='utf-8'));load_v8()
    if r['inputs']!=fingerprints() or r['code_sha256']!=sha256(Path(__file__)):raise ValueError('Changed v10 code/input')
    return trusted_load(OUTPUT/'development_inputs.joblib',r['data_sha256'])


def cache(family,bank,split):
    result={}
    for name,d in bank.items():
        result[name]=[]
        for cohort in ('observed','normal'):
            f=d[cohort].loc[d[cohort].validation_split.eq(split)]
            y=f.window_is_anomaly.to_numpy(bool) if cohort=='observed' else np.zeros(len(f),bool)
            raw,covered=family.components(f)
            result[name].append((raw,f.adapterType.to_numpy(),covered,y))
    return result


def summarize(family,cached):
    scenarios,pooled=[],np.zeros((3,4),int)
    for name,cohorts in cached.items():
        counts=np.zeros((3,4),int)
        for raw,adapters,covered,y in cohorts:
            margins=family.from_components(raw,adapters);margins[~covered,1:]=margins[~covered,0,None]
            for i,p in enumerate((margins>0).T):counts[i]+=[np.sum(~y&~p),np.sum(~y&p),np.sum(y&~p),np.sum(y&p)]
        scenarios.append({'scenario':name,'metrics':[metric_record(*c) for c in counts]});pooled+=counts
    records=[metric_record(*c) for c in pooled]
    return {'scenarios':scenarios,'pooled':records,'objective':float(np.mean([r['f1'] for d in scenarios for r in d['metrics']])),
            'worst_strategy_pooled_fpr':max(r['normal_false_positive_rate'] for r in records),
            'worst_scenario_strategy_fpr':max(r['normal_false_positive_rate'] for d in scenarios for r in d['metrics'])}


def tune():
    data=load()
    if (OUTPUT/'selection.json').exists():raise FileExistsError('v10 frozen')
    v4,_=frozen_v4();v7,_=frozen_v7();chosen,results={},[]
    for algorithm,base in (('if',v7.families['if']),('kmeans',v4.families['kmeans'])):
        best=model=fallback=fallback_model=None
        for view,columns in VIEWS.items():
            for n,k in ([(256,6),(1024,6)] if algorithm=='if' else [(512,6),(512,12)]):
                settings=ResearchSettings(algorithm,samples=n,k=k)
                print(f'v10 fit {algorithm} {view} samples={n} K={k}',flush=True)
                family=Bridge(base,columns,settings).fit(data['partitions']);cached=cache(family,data['bank'],'search')
                for ql in (.975,.99):
                    for qj in (.99,.995):
                        for weight in (.25,.75):
                            for gamma in (.5,1.,2.):
                                for mode in ('mean','max'):
                                    family.set_operating_point(ql,qj,weight,gamma,mode)
                                    r={'algorithm':algorithm,'view':view,'settings':asdict(settings),'latency_quantile':ql,'joint_quantile':qj,
                                       'global_weight':weight,'latency_weight':gamma,'combination':mode,**summarize(family,cached)}
                                    r['within_budget']=r['worst_strategy_pooled_fpr']<=.02 and r['worst_scenario_strategy_fpr']<=.05
                                    results.append(r);key=(r['objective'],-r['worst_strategy_pooled_fpr'],-len(columns),-n,-k)
                                    fk=(-r['worst_strategy_pooled_fpr'],-r['worst_scenario_strategy_fpr'],r['objective'])
                                    if fallback is None or fk>fallback[0]:fallback,fallback_model=(fk,r),copy.deepcopy(family)
                                    if r['within_budget'] and (best is None or key>best[0]):best,model=(key,r),copy.deepcopy(family)
        if best is None:best,model=fallback,fallback_model
        chosen[algorithm]=model;write_json(OUTPUT/f'chosen_{algorithm}.json',best[1]);print(f"v10 chosen {algorithm}: macro F1={best[1]['objective']:.2%}, pooled FPR={best[1]['worst_strategy_pooled_fpr']:.2%}, feasible={best[1]['within_budget']}",flush=True)
    suite=Suite(chosen)
    for attr in ('reference','baseline','legacy_baselines'):setattr(suite,attr,data[attr])
    validation={name:{split:six_metrics(m,bank,split) for split in ('search','confirmation')}
                for name,m,bank in (('baseline_v4',v4,data['legacy_bank']),('baseline_v7',v7,data['legacy_bank']),('tuned_v10',suite,data['bank']))}
    joblib.dump(suite,OUTPUT/'models_v10.joblib',compress=3);write_json(OUTPUT/'search_results.json',results);write_json(OUTPUT/'validation_results.json',validation)
    write_json(OUTPUT/'selection.json',{'inputs':fingerprints(),'model_sha256':sha256(OUTPUT/'models_v10.joblib'),'code_sha256':sha256(Path(__file__)),
        'families':{a:json.loads((OUTPUT/f'chosen_{a}.json').read_text(encoding='utf-8')) for a in chosen},'normal_only_fit':True,'labelled_selection':True,
        'confirmation_used_for_selection':False,'final_used_for_selection':False,'hard_used_for_selection':False,'not_blind_original_benchmark':True,
        'fixed_base_branches':{'if':'v7','kmeans':'v4'},'completed_utc':datetime.now(timezone.utc).isoformat()})


def frozen():
    r=json.loads((OUTPUT/'selection.json').read_text(encoding='utf-8'));load_v8();frozen_v4();frozen_v7()
    if r['inputs']!=fingerprints() or r['code_sha256']!=sha256(Path(__file__)):raise ValueError('Changed frozen v10 code/input')
    return trusted_load(OUTPUT/'models_v10.joblib',r['model_sha256']),r


def evaluate():
    suite,selection=frozen();verify_benchmark(BENCHMARK,SOURCE_CACHE)
    prior=json.loads((V8/'summary.json').read_text(encoding='utf-8'));old=json.loads((V4/'summary.json').read_text(encoding='utf-8'))
    old_by={d['dataset']:d for d in old['datasets']};pooled,support,datasets=Counts(),Counts(),[]
    for previous in prior['datasets']:
        name=previous['dataset'];dest=OUTPUT/name;dest.mkdir(exist_ok=True)
        if (dest/'metrics.json').exists():
            result=json.loads((dest/'metrics.json').read_text(encoding='utf-8'));assert result['model_sha256']==selection['model_sha256']
            for c,digest in result['arrays_sha256'].items():assert sha256(dest/f'{c}.npz')==digest
        else:
            arrays,counts,supported={},Counts(),Counts()
            for cohort,digest in previous['arrays_sha256'].items():
                assert sha256(V8/name/f'{cohort}.npz')==digest
                assert sha256(V4/name/f'{cohort}.npz')==old_by[name]['arrays_sha256'][cohort]
                with np.load(V8/name/f'{cohort}.npz',allow_pickle=False) as z:a={k:z[k] for k in z.files if k not in ('margin','prediction','covered','channel_evidence')}
                with np.load(V4/name/f'{cohort}.npz',allow_pickle=False) as z:
                    for key in ('truth','case_index','window_end_seconds'):np.testing.assert_array_equal(a[key],z[key])
                    f=pd.DataFrame(z['features'],columns=OLD_FEATURES,dtype=float)
                for i,c in enumerate(FEATURES):f[c]=a['features'][:,i]
                f['adapterType'],f['phase']=a['adapter'],a['phase']
                for c in ('traffic_assessed','latency_assessed'):f[c]=a[c]
                margin,pred,covered=[],[],[]
                for start in range(0,len(f),50_000):
                    m,p,c=suite.score(f.iloc[start:start+50_000]);margin.append(m.astype(np.float32));pred.append(p);covered.append(c)
                a['margin'],a['prediction'],a['covered']=np.vstack(margin),np.vstack(pred),np.concatenate(covered)
                counts.update(a['truth'],a['prediction']);supported.update(a['truth'],a['prediction'],a['covered'])
                np.savez_compressed(dest/f'{cohort}.npz',**a);arrays[cohort]=a
            cases=pd.read_csv(BENCHMARK/name/'cases.csv',encoding='utf-8-sig');events=pd.read_csv(BENCHMARK/name/'events.csv',encoding='utf-8-sig')
            result={'dataset':name,'case_count':len(cases),'metrics':counts.records(),'supported_only_metrics':supported.records(),
                    'events':event_results(arrays['observed'],arrays['normal'],events,cases),'coverage':previous['coverage'],
                    'traffic_assessed_fraction':previous['traffic_assessed_fraction'],'latency_assessed_fraction':previous['latency_assessed_fraction'],
                    'model_sha256':selection['model_sha256'],'arrays_sha256':{c:sha256(dest/f'{c}.npz') for c in arrays}}
            write_json(dest/'metrics.json',result)
        pooled.values+=Counts.from_records(result['metrics']).values;support.values+=Counts.from_records(result['supported_only_metrics']).values;datasets.append(result)
        print(f"v10 {name}: IF ensemble={result['metrics'][2]['f1']:.2%}, KMeans ensemble={result['metrics'][5]['f1']:.2%}",flush=True)
    write_json(OUTPUT/'summary.json',{'pooled':pooled.records(),'supported_only':support.records(),'datasets':datasets,
        'basic_macro':[{'method':m,**{k:float(np.mean([d['metrics'][i][k] for d in datasets if d['dataset']!='mixed_hard'])) for k in METRIC_NAMES}} for i,m in enumerate(METHODS)],
        'thresholds':suite.threshold_records(),'support':suite.families['if'].support,'model_sha256':selection['model_sha256'],
        'protocol':{'original_labels_times_unchanged':True,'point_adjustment':False,'not_blind_original_test':True,'normal_only_fit':True,
                    'base_branches_not_refit':True,'previous_test_scores_not_features':True,'no_automatic_deployment':True}})
    verify_benchmark(BENCHMARK,SOURCE_CACHE)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=('prepare','tune','evaluate','all'),default='all');args=p.parse_args()
    if args.stage in ('prepare','all'):prepare()
    if args.stage in ('tune','all'):tune()
    if args.stage in ('evaluate','all'):evaluate()
