"""Empirical-Bayes traffic reference from EXISTING dev normal controls.

Core sklearn models remain original-only. The additional count prior is fitted
on already-existing synthetic development NORMAL controls, explicitly recorded.
No confirmation observations/labels, generated rows or future current-session
timestamps enter this reference or posterior feature.
"""
import copy
import json

import joblib
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import nbinom

from .adapter_score_calibration import AdapterTrafficDetector
from .adaptive_models import NativeDetector
from .data import ROOT,write_json,digest
from .minute_refinement import ALGORITHMS,ACTIVE_TASKS,EventPolicy,MinuteReplay,results,quality


def fit_prior(frame):
    """Gamma-Poisson marginal MLE over independent normal sessions >=10 min."""
    rates=[]
    for _,part in frame.loc[frame.cohort.eq('normal')].groupby('_uid',sort=False):
        t=part.availableTime.astype('datetime64[ns, UTC]').astype('int64').to_numpy()/6e10
        exposure=float(t.max()-t.min())
        founders=int(np.sum(t==t.min()))
        if exposure>=10 and len(t)>founders:
            rates.append((len(t)-founders,exposure))
    n,t=np.asarray(rates,float).T
    mu=float(n.sum()/t.sum())
    def loss(params):
        alpha,beta=np.exp(params)
        return -float(np.sum(gammaln(n+alpha)-gammaln(alpha)+alpha*np.log(beta)-(n+alpha)*np.log(beta+t)))
    fit=minimize(loss,np.log([max(mu*5,.1),5.]),bounds=[(np.log(.1),np.log(1e4)),(np.log(.1),np.log(1e4))])
    if not fit.success:raise ValueError('Count-prior fit failed: '+str(fit.message))
    alpha,beta=np.exp(fit.x)
    # Do not let a nearly degenerate fitted prior imply unlimited confidence.
    beta_capped=min(float(beta),20.)
    alpha_capped=float(alpha/beta*beta_capped)
    return dict(alpha=alpha_capped,beta_minutes=beta_capped,fit_alpha=float(alpha),fit_beta_minutes=float(beta),
        normal_sessions=len(n),normal_execution_rows=int(n.sum()),min_session_exposure_minutes=10.,
        prior_exposure_cap_minutes=20.,source='existing first400 development normal controls only',
        newly_generated_normal_logs=0)


def posterior_evidence(f,prior,strength=1.):
    n=f.predictive_support_1m.to_numpy(float)
    exposure=f.predictive_exposure_1m.to_numpy(float)
    dispersion=np.maximum(f.predictive_dispersion_1m.to_numpy(float),1.)
    count=np.rint(np.expm1(f.past_log_count_1m.to_numpy(float)))
    alpha=(n+.5+strength*prior['alpha'])/dispersion
    beta=(exposure+strength*prior['beta_minutes'])/dispersion
    valid=exposure>=1.
    result=np.zeros(len(f))
    result[valid]=np.minimum(-nbinom.logsf(count[valid]-1,alpha[valid],beta[valid]/(beta[valid]+1)),20.)
    return np.nan_to_num(result,nan=0.,posinf=20.,neginf=0.)


class EmpiricalTrafficDetector(AdapterTrafficDetector):
    def __init__(self,parent,prior,strength=1.,weight=None,cutoff=None,stat_scale=.25):
        super().__init__(parent,parent.traffic_factors,parent.traffic_support)
        self.count_prior=dict(prior);self.prior_strength=float(strength)
        self.traffic_model_weight=float(parent.model.family_weights[3] if weight is None else weight)
        self.traffic_cutoff=float(parent.model.cutoffs[3] if cutoff is None else cutoff)
        self.traffic_stat_scale=float(stat_scale)
        self.past_rate_factors=copy.deepcopy(getattr(parent,'rate_factors',None))
        self.past_dispersion_factors=copy.deepcopy(getattr(parent,'dispersion_factors',None))

    def traffic_factor(self,f):
        factor=super().traffic_factor(f)
        if self.past_rate_factors is not None:
            from .rate_conditioned import RateTrafficDetector
            rate,dispersion=RateTrafficDetector.codes(f)
            factor=factor*self.past_rate_factors[rate]*self.past_dispersion_factors[dispersion]
        return factor

    def traffic_peak(self,f):
        native=NativeDetector.peaks(f)[:,3]
        return np.sqrt(native*posterior_evidence(f,self.count_prior,self.prior_strength))

    def normalized(self,f):
        raw=self.base.raw_score(f)
        cold,mode,warm,point=self.model.parts(f,raw)
        normalized=warm/self.model.cutoffs[:4]
        peak=self.traffic_peak(f);stat=np.log1p(peak/self.traffic_stat_scale)
        center,noise=self.model.model_stats.get(3,self.model.default_stats)
        learned=np.log1p(np.maximum((raw-center)/noise,0.))
        weight=self.traffic_model_weight
        normalized[:,3]=np.where(peak>0,weight*learned+(1-weight)*stat,0.)/self.traffic_cutoff/self.traffic_factor(f)
        score=normalized.max(axis=1);group=normalized.argmax(axis=1)
        score[cold]=point[cold]/self.model.cutoffs[mode[cold]]/self.factor(f)[cold]
        group[cold]=mode[cold]
        return group,score

    def calibration_context(self,f):
        return {**super().calibration_context(f),'empirical_prior_burst_1m':posterior_evidence(f,self.count_prior,self.prior_strength)}

    def record(self):
        return {**super().record(),'empirical_count_prior':self.count_prior,'prior_strength':self.prior_strength,
            'traffic_model_weight':self.traffic_model_weight,'traffic_cutoff':self.traffic_cutoff,
            'traffic_stat_scale':self.traffic_stat_scale,'original_only_sklearn_fit':True,
            'count_reference_fit_uses_existing_synthetic_development_normals':True}


def run():
    source=ROOT/'reports/process_event_model/event_candidate';out=ROOT/'reports/process_event_model/empirical_traffic'
    out.mkdir(exist_ok=True)
    package=joblib.load(source/'event_models.joblib');selection=json.loads((source/'selection.json').read_text(encoding='utf-8'))
    data=joblib.load(source/'minute_development.joblib');f=data['frame'];rp=MinuteReplay(f,data['windows'])
    normal=joblib.load(ROOT/'reports/process_event_model/native_event/original_features.joblib')['partitions']['calibration']
    prior=fit_prior(f);write_json(out/'count_prior.json',prior);print('EMPIRICAL COUNT PRIOR',prior,flush=True)
    models={};records={}
    for a in ALGORITHMS:
        parent=package['models'][a];base=parent.model;raw=base.base.raw_score(f)
        cold,mode,warm,point=base.parts(f,raw);other=(warm[:,:3]/base.cutoffs[:3]).max(axis=1)
        cold_score=point/base.cutoffs[mode]/parent.factor(f);factor=parent.traffic_factor(f)
        params=dict(selection['models'][a]['policy']);legacy={t:data['legacy'][t][a] for t in ACTIVE_TASKS}
        baseline=results(data,rp.project(parent.raw_score(f),EventPolicy(**params)))
        def objective(rows):
            if any(m['false_positive_rate']>.06 for m in rows.values()):return -1e6
            if any(rows[t]['f1']<min(baseline[t]['f1'],legacy[t]['f1'])-.01 for t in ACTIVE_TASKS if t!='time_burst'):return -1e6
            return quality(rows,legacy)-.25*max(legacy['time_burst']['precision']-rows['time_burst']['precision'],0.)
        best=objective(baseline);rows=baseline;best_model=copy.deepcopy(parent);best_params=dict(params)
        choice=dict(source_preserved=True)
        center,noise=base.model_stats.get(3,base.default_stats);learned=np.log1p(np.maximum((raw-center)/noise,0.))
        for strength in (.1,.25,.5,1.,2.):
            prototype=EmpiricalTrafficDetector(parent,prior,strength)
            peak=prototype.traffic_peak(f);stat_scale=max(float(np.quantile(prototype.traffic_peak(normal),.95)),.25)
            statistic=np.log1p(peak/stat_scale)
            for weight in (.03,.15,.35,.65):
                channel=np.where(peak>0,weight*learned+(1-weight)*statistic,0.)/factor
                negatives=(~cold)&~f.label.to_numpy(bool)
                cuts=np.unique(np.r_[base.cutoffs[3]*np.array([.6,.8,1.,1.2,1.5]),
                    np.quantile(channel[negatives],[.85,.94,.97,.98,.99])])
                for cutoff in cuts:
                    score=np.maximum(other,channel/max(cutoff,1e-8));score[cold]=cold_score[cold]
                    for ttl in (90.,180.,240.,300.):
                        p=dict(params,traffic_ttl=ttl);r=results(data,rp.project(score,EventPolicy(**p)));q=objective(r)
                        if q>best+1e-10:
                            best=q;rows=r;best_params=p
                            best_model=EmpiricalTrafficDetector(parent,prior,strength,weight,cutoff,stat_scale)
                            choice=dict(prior_strength=strength,weight=weight,cutoff=float(cutoff),stat_scale=stat_scale)
            print('EMPIRICAL TRAFFIC',a,strength,choice,{t:round(m['f1'],4) for t,m in rows.items()},
                'P',round(rows['time_burst']['precision'],4),flush=True)
        models[a]=best_model
        assert results(data,rp.project(best_model.raw_score(f),EventPolicy(**best_params)))==rows
        records[a]=dict(policy=best_params,tasks=rows,objective=best,selected=choice,settings=vars(best_model.settings))
        write_json(out/'progress.json',dict(models=records))
    joblib.dump({**package,'models':models},out/'event_models.joblib',compress=3)
    joblib.dump(data,out/'minute_development.joblib',compress=3)
    write_json(out/'selection.json',dict(models=records,source_sha256=digest(source/'event_models.joblib'),
        normal_count_prior=prior,development_first400_cases_only=True,benchmark_unchanged=True,
        no_additional_normal_training_rows=True,sklearn_estimator_fit_original_only=True))


if __name__=='__main__':
    from .empirical_traffic import run as canonical_run
    canonical_run()
