"""v9: smaller latency views and conservative normal-domain calibration.

v8 validation drift motivated this new experiment. New search/confirmation
episodes, same disjoint normal sources. No original-test labels select settings.
The previously inspected benchmark still is NOT blind external validation.
"""
import argparse
import copy
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"code"))
import joblib
import numpy as np
import pandas as pd
from process_anomaly_pipeline.features import ScoreNormalizer
from model_evaluation.data import sha256,verify_benchmark
from model_evaluation.windowing import PriorHistory
from model_evaluation.tune_windows import write_json,selected_references
from model_evaluation.metrics import Counts
from model_evaluation.evaluate import event_results
from model_improvement.data import BENCHMARK,SOURCE_CACHE,SEARCH,validation_inputs
from model_improvement.features import build_features
from model_improvement.refine import with_legacy,frozen as frozen_v4
from model_improvement.run import trusted_load
from model_research_v5.features import context_features
from model_research_v5.run_matched import frozen as frozen_v7
from model_research_v5.run import components,summarize,six_metrics,METRIC_NAMES
from model_research_v5.models import Encoder,RawModel,normalize
from model_research_v8.features import TRAFFIC,LATENCY,FEATURES,add_duration_features
from model_research_v8.models import ResearchSettings,ClusterDistance,ChannelDetector as OriginalDetector,ChannelFamily as OriginalFamily,Suite,METHODS
from model_research_v8.data import load as load_v8,fingerprints
from model_research_v8.run import frozen as frozen_v8,OUTPUT as V8

OUTPUT=ROOT/"reports/model_research_v9"
SEED=11_170_042
LATENCY_VIEWS={"tail2":LATENCY[:2],"temporal8":LATENCY}


@dataclass(frozen=True)
class RefinedSettings(ResearchSettings):
    latency_view:str="tail2"


class Detector(OriginalDetector):
    @classmethod
    def fit(cls,train,calibration,threshold,columns,settings):
        result=cls(None,None,None,None,calibration.phase.to_numpy(),threshold.phase.to_numpy())
        result.channels=[]
        for cols,eligible in ((TRAFFIC,"traffic_assessed"),(LATENCY_VIEWS[settings.latency_view],"latency_assessed")):
            tr,ca=train.loc[train[eligible]],calibration.loc[calibration[eligible]]
            if len(tr)<80 or len(ca)<20:raise ValueError("Insufficient normal assessed channel")
            encoder=Encoder.fit(tr,cols)
            model=RawModel(settings) if settings.algorithm=="if" else ClusterDistance(settings)
            model.fit(encoder.transform(tr),encoder.transform(ca))
            result.channels.append((encoder,model,ScoreNormalizer.fit(model.score(encoder.transform(ca))),eligible))
        result.calibration_channels=result.explain(calibration);result.threshold_channels=result.explain(threshold)
        result.latency_weight=1.;result.refresh();return result


class Family(OriginalFamily):
    def fit(self,partitions,source_support):
        self.global_model=Detector.fit(*(partitions[n] for n in ("train","calibration","threshold")),FEATURES,self.settings)
        self.threshold_channels,self.calibration_channels={},{}
        for adapter in sorted(set().union(*(set(p.adapterType) for p in partitions.values()))):
            parts={n:p.loc[p.adapterType.eq(adapter)] for n,p in partitions.items()}
            reasons=[f"{n}<{minimum}" for n,minimum in (("train",500),("calibration",100),("threshold",100)) if len(parts[n])<minimum]
            reasons += [f"{n}_source_workflows<20" for n in parts if source_support.get(adapter,{}).get(n,0)<20]
            reasons += [f"{n}_{channel}_observations<{minimum}" for n in parts for channel in ("traffic_assessed","latency_assessed")
                        for minimum in [80 if n=='train' else 20] if int(parts[n][channel].sum())<minimum]
            if not reasons:
                expert=Detector.fit(*(parts[n] for n in ("train","calibration","threshold")),FEATURES,self.settings)
                self.experts[adapter]=expert
                self.threshold_channels[adapter]=np.column_stack([self.global_model.explain(parts["threshold"]),expert.threshold_channels])
                self.calibration_channels[adapter]=np.column_stack([self.global_model.explain(parts["calibration"]),expert.calibration_channels])
            self.support.append({"adapter":adapter,**{n:len(p) for n,p in parts.items()},"source_workflows":source_support.get(adapter,{}),
                                 "expert_available":not reasons,"reason":"; ".join(reasons) or "supported"})
        self.set_operating_point(.995,.5,False,1.,"mean");return self

    def set_operating_point(self,q,weight,aware,beta=1.,mode="mean"):
        super().set_operating_point(q,weight,aware,beta,mode)
        # Conservative envelope of two normal source domains, not a promised
        # false-positive guarantee. Calibration also fitted score scaling.
        phase=self.global_model.calibration_phase
        g=normalize(self.global_model.calibration_raw,phase,self.norms["GLOBAL"])
        for p in ("startup","steady"):
            mask=phase==p if aware else np.ones(len(phase),bool)
            if mask.sum()<50:mask=np.ones(len(phase),bool)
            self.thresholds["GLOBAL"][p][:]=max(self.thresholds["GLOBAL"][p][0],float(np.quantile(g[mask],q)))
        for adapter,expert in self.experts.items():
            raw=self.calibration_channels[adapter];phase=expert.calibration_phase
            g=normalize(self.global_model.combine(raw[:,:2]),phase,self.norms["GLOBAL"])
            local=normalize(expert.combine(raw[:,2:]),phase,self.norms[adapter])
            combined=weight*g+(1-weight)*local if mode=="mean" else np.maximum(weight*g,(1-weight)*local)
            for p in ("startup","steady"):
                mask=phase==p if aware else np.ones(len(phase),bool)
                if mask.sum()<50:mask=np.ones(len(phase),bool)
                t=self.thresholds[adapter][p];t[0]=self.thresholds["GLOBAL"][p][0]
                t[1]=max(t[1],float(np.quantile(local[mask],q)));t[2]=max(t[2],float(np.quantile(combined[mask],q)))

    def records(self):return [{**r,"normal_domain_envelope":True,"latency_view":self.settings.latency_view} for r in super().records()]


def prepare():
    OUTPUT.mkdir(exist_ok=True)
    if (OUTPUT/"preparation.json").exists():return load()
    _,bundle=verify_benchmark(BENCHMARK,SOURCE_CACHE);previous=load_v8()
    _,_,state,_=selected_references(SEARCH,BENCHMARK,SOURCE_CACHE)
    native=validation_inputs(bundle,state,count=360,seed=SEED)
    history=PriorHistory(BENCHMARK/"reference_history_windows.csv")
    forbidden=set().union(*(set(d["observed"].case_id) for d in previous["bank"].values()))
    bank,legacy={},{}
    for name,d in native.items():
        bank[name],legacy[name]={},{}
        for cohort in ("observed","normal"):
            f=context_features(with_legacy(build_features(d[cohort],d[cohort+"_rows"],history,previous["baseline"]),previous["legacy_baselines"],history))
            if set(f.case_id)&forbidden:raise ValueError("v9 validation episode overlap")
            legacy[name][cohort]=f;bank[name][cohort]=add_duration_features(f,d[cohort+"_rows"],previous["reference"])
    payload={**{k:v for k,v in previous.items() if k not in ("bank","legacy_bank")},"bank":bank,"legacy_bank":legacy}
    joblib.dump(payload,OUTPUT/"development_inputs.joblib",compress=3)
    write_json(OUTPUT/"preparation.json",{"inputs":fingerprints(),"data_sha256":sha256(OUTPUT/"development_inputs.joblib"),
        "code_sha256":sha256(Path(__file__)),"v8_preparation_sha256":sha256(V8/"preparation.json"),
        "search_cases":1080,"confirmation_cases":1080,"fresh_seed":SEED,"normal_fit_unchanged_from_v8":True,
        "change":"Compact latency views and max of normal calibration/threshold domain quantiles."})
    return payload


def load():
    r=json.loads((OUTPUT/"preparation.json").read_text(encoding="utf-8"));load_v8()
    if r["inputs"]!=fingerprints() or r["code_sha256"]!=sha256(Path(__file__)):raise ValueError("Changed frozen v9 preparation")
    if r["v8_preparation_sha256"]!=sha256(V8/"preparation.json"):raise ValueError("Changed v8 sources")
    return trusted_load(OUTPUT/"development_inputs.joblib",r["data_sha256"])


def tune():
    data=load()
    if (OUTPUT/"selection.json").exists():raise FileExistsError("v9 frozen")
    selected,results={},[]
    for algorithm in ("if","kmeans"):
        best=model=fallback=fallback_model=None
        configs=[RefinedSettings(algorithm,samples=n,k=k,latency_view=view) for view in LATENCY_VIEWS
                 for n,k in ([(256,6),(1024,6)] if algorithm=="if" else [(512,6),(512,12)])]
        for settings in configs:
            print(f"v9 fit {asdict(settings)}",flush=True)
            family=Family(FEATURES,settings).fit(data["partitions"],data["source_support"])
            cached=components(family,data["bank"],"search")
            for beta in (.5,1.,2.):
                for q in (.995,.999):
                    for weight in (.25,.75):
                        for mode in ("mean","max"):
                            family.set_operating_point(q,weight,False,beta,mode)
                            r={"algorithm":algorithm,"settings":asdict(settings),"quantile":q,"global_weight":weight,"phase_aware":False,
                               "latency_weight":beta,"combination":mode,**summarize(family,cached)}
                            r["within_budget"]=r["worst_strategy_pooled_fpr"]<=.02 and r["worst_scenario_strategy_fpr"]<=.05
                            results.append(r);key=(r["objective"],-r["worst_strategy_pooled_fpr"],-len(LATENCY_VIEWS[settings.latency_view]),-settings.samples,-settings.k)
                            fk=(-r["worst_strategy_pooled_fpr"],-r["worst_scenario_strategy_fpr"],r["objective"])
                            if fallback is None or fk>fallback[0]:fallback,fallback_model=(fk,r),copy.deepcopy(family)
                            if r["within_budget"] and (best is None or key>best[0]):best,model=(key,r),copy.deepcopy(family)
        if best is None:best,model=fallback,fallback_model
        selected[algorithm]=model;write_json(OUTPUT/f"chosen_{algorithm}.json",best[1])
        print(f"v9 chosen {algorithm}: macro F1={best[1]['objective']:.2%}, pooled FPR={best[1]['worst_strategy_pooled_fpr']:.2%}, feasible={best[1]['within_budget']}",flush=True)
    suite=Suite(selected)
    for attr in ("reference","baseline","legacy_baselines"):setattr(suite,attr,data[attr])
    v4,_=frozen_v4();v7,_=frozen_v7();v8,_=frozen_v8()
    validation={name:{split:six_metrics(m,bank,split) for split in ("search","confirmation")}
                for name,m,bank in (("baseline_v4",v4,data["legacy_bank"]),("baseline_v7",v7,data["legacy_bank"]),
                                    ("baseline_v8",v8,data["bank"]),("tuned_v9",suite,data["bank"]))}
    joblib.dump(suite,OUTPUT/"models_v9.joblib",compress=3);write_json(OUTPUT/"validation_results.json",validation);write_json(OUTPUT/"search_results.json",results)
    write_json(OUTPUT/"selection.json",{"inputs":fingerprints(),"model_sha256":sha256(OUTPUT/"models_v9.joblib"),"code_sha256":sha256(Path(__file__)),
        "families":{a:json.loads((OUTPUT/f"chosen_{a}.json").read_text(encoding="utf-8")) for a in selected},
        "normal_only_fit":True,"labelled_selection":True,"confirmation_used_for_selection":False,"final_used_for_selection":False,"hard_used_for_selection":False,
        "objective":"Mean equal-scenario F1 over three strategies; pooled FPR<=2%, each scenario/strategy<=5%.",
        "not_blind_original_benchmark":True,"completed_utc":datetime.now(timezone.utc).isoformat()})


def frozen():
    r=json.loads((OUTPUT/"selection.json").read_text(encoding="utf-8"));load_v8()
    if r["inputs"]!=fingerprints() or r["code_sha256"]!=sha256(Path(__file__)):raise ValueError("Changed frozen v9 model code/input")
    return trusted_load(OUTPUT/"models_v9.joblib",r["model_sha256"]),r


def evaluate():
    suite,selection=frozen();verify_benchmark(BENCHMARK,SOURCE_CACHE)
    previous=json.loads((V8/"summary.json").read_text(encoding="utf-8"));pooled,support,datasets=Counts(),Counts(),[]
    for prior in previous["datasets"]:
        name=prior["dataset"];dest=OUTPUT/name;dest.mkdir(exist_ok=True)
        if (dest/"metrics.json").exists():
            result=json.loads((dest/"metrics.json").read_text(encoding="utf-8"));assert result["model_sha256"]==selection["model_sha256"]
            for c,digest in result["arrays_sha256"].items():assert sha256(dest/f"{c}.npz")==digest
        else:
            arrays,counts,supported={},Counts(),Counts()
            for cohort,digest in prior["arrays_sha256"].items():
                assert sha256(V8/name/f"{cohort}.npz")==digest
                with np.load(V8/name/f"{cohort}.npz",allow_pickle=False) as z:
                    a={k:z[k] for k in z.files if k not in ("margin","prediction","covered","channel_evidence")}
                frame=pd.DataFrame(a["features"],columns=FEATURES,dtype=float)
                frame["adapterType"],frame["phase"]=a["adapter"],a["phase"]
                for col in ("traffic_assessed","latency_assessed"):frame[col]=a[col]
                margins,preds,coverage,evidence=[],[],[],[]
                for start in range(0,len(frame),50_000):
                    part=frame.iloc[start:start+50_000];m,p,c=suite.score(part)
                    margins.append(m.astype(np.float32));preds.append(p);coverage.append(c)
                    evidence.append(np.column_stack([suite.families[algo].global_model.explain(part) for algo in ("if","kmeans")]).astype(np.float32))
                a["margin"],a["prediction"],a["covered"],a["channel_evidence"]=np.vstack(margins),np.vstack(preds),np.concatenate(coverage),np.vstack(evidence)
                counts.update(a["truth"],a["prediction"]);supported.update(a["truth"],a["prediction"],a["covered"])
                np.savez_compressed(dest/f"{cohort}.npz",**a);arrays[cohort]=a
            cases=pd.read_csv(BENCHMARK/name/"cases.csv",encoding="utf-8-sig");events=pd.read_csv(BENCHMARK/name/"events.csv",encoding="utf-8-sig")
            result={"dataset":name,"case_count":len(cases),"metrics":counts.records(),"supported_only_metrics":supported.records(),
                    "events":event_results(arrays["observed"],arrays["normal"],events,cases),"coverage":prior["coverage"],
                    "traffic_assessed_fraction":prior["traffic_assessed_fraction"],"latency_assessed_fraction":prior["latency_assessed_fraction"],
                    "model_sha256":selection["model_sha256"],"arrays_sha256":{c:sha256(dest/f"{c}.npz") for c in arrays}}
            write_json(dest/"metrics.json",result)
        pooled.values+=Counts.from_records(result["metrics"]).values;support.values+=Counts.from_records(result["supported_only_metrics"]).values;datasets.append(result)
        print(f"v9 {name}: IF ensemble={result['metrics'][2]['f1']:.2%}, KMeans ensemble={result['metrics'][5]['f1']:.2%}",flush=True)
    write_json(OUTPUT/"summary.json",{"pooled":pooled.records(),"supported_only":support.records(),"datasets":datasets,
        "basic_macro":[{"method":m,**{k:float(np.mean([d['metrics'][i][k] for d in datasets if d['dataset']!='mixed_hard'])) for k in METRIC_NAMES}} for i,m in enumerate(METHODS)],
        "thresholds":suite.threshold_records(),"support":suite.families['if'].support,"model_sha256":selection['model_sha256'],
        "protocol":{"original_labels_times_unchanged":True,"point_adjustment":False,"not_blind_original_test":True,"normal_only_fit":True,
                    "normal_training_refs_unchanged_from_v8":True,"previous_test_scores_not_features":True,"no_automatic_deployment":True}})
    verify_benchmark(BENCHMARK,SOURCE_CACHE)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=('prepare','tune','evaluate','all'),default='all');args=p.parse_args()
    if args.stage in ('prepare','all'):prepare()
    if args.stage in ('tune','all'):tune()
    if args.stage in ('evaluate','all'):evaluate()
