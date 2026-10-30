"""Different numeric views for requested roles, with one anomaly estimator each.

Every model retains coverage features for other tasks. Specialization is a
candidate preference, not truth-based serving routing or a reason to weaken
another model. Normal references and estimator fitting never use fault labels.
"""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.models import Setting as ParentSetting,Detector as Parent
from shared_model_v6.features import Evidence,RAW
from shared_model_v7.agreement import Geometry
from shared_model_v7.role_context import Context,TREND,COHORT
from shared_model_v7.remap_models import cached,_NEAR_CACHE,Transport

REFERENCE=None
PARENTS=None
_TRAJECTORY_CACHE={}


@dataclass(frozen=True)
class Setting(ParentSetting):
    role_view:str='point_pair'
    extra_gain:float=1.
    cold_point:bool=False


class Encoder:
    def __init__(self,s,history):
        self.setting=s;self.core=Evidence(history);self.trajectory=Context()
        if s.algorithm in('kmeans','lof'):self.geometry=Geometry(PARENTS[s.algorithm].base)
    def extras(self,f,training=False):
        if self.setting.algorithm=='if':
            near=cached(_NEAR_CACHE,(id(self.reference),id(f),training),f,lambda:self.reference.transform(f,exclude_case=training)[:,0])
            point=np.minimum(near,np.maximum(f.current_source_upper.to_numpy(float),0.)*.5)
            if self.setting.cold_point:point=point*Transport.cold(f)
            return np.column_stack([point,near,f.current_source_upper.to_numpy(float),f.current_source_delay.to_numpy(float)])
        if self.setting.algorithm=='kmeans':return np.nan_to_num(f[list(COHORT)].to_numpy(float),nan=0.)
        return cached(_TRAJECTORY_CACHE,id(f),f,lambda:self.trajectory.transform(f)).to_numpy()
    def fit(self,f):
        self.reference=REFERENCE;self.core.fit(f);self.fit_frame=f;self.fit_extra=self.extras(f,training=True)
        self.center=np.median(self.fit_extra,axis=0);self.scale=np.maximum(np.quantile(self.fit_extra,.95,axis=0)-self.center,.15)
        return self
    def transform(self,f,view):
        peaks=self.core.transform(f,'peaks');raw=self.fit_extra if f is getattr(self,'fit_frame',None)else self.extras(f)
        e=np.maximum((raw-self.center)/self.scale,0.)*self.setting.extra_gain
        if self.setting.algorithm=='if':
            enriched=peaks.copy();enriched[:,0]=e[:,0]
            if view=='peaks':return enriched
            if self.setting.role_view=='point_pair':x=np.column_stack([e[:,0],peaks[:,1:].max(axis=1)])
            else:x=np.column_stack([e,peaks[:,1:]])
        else:
            if view=='peaks':return peaks
            x=np.column_stack([self.geometry.values(f),e])
        if self.setting.transform=='asinh':x=np.arcsinh(x)
        return x
    def names(self,view):
        if self.setting.algorithm=='if':
            if self.setting.role_view=='point_pair':return ['normal_joint_point','other_evidence_max']
            return ['normal_joint_point','normal_nearest','normal_source_upper','normal_source_delay','delay','trend','burst','gap']
        return [*self.geometry.names,*(COHORT if self.setting.algorithm=='kmeans'else TREND)]
    def release(self):self.fit_frame=None;self.fit_extra=None


class Detector(Parent):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(s,history)
    def fit(self,f):
        super().fit(f)
        x=self.encoder.fit_extra
        self.extra_fit_distribution={'rows':len(f),'nonzero_fraction':np.mean(x>0,axis=0).tolist(),
            'median':np.median(x,axis=0).tolist(),'P95':np.quantile(x,.95,axis=0).tolist(),'maximum':np.max(x,axis=0).tolist()}
        if self.algorithm=='if':
            used=np.zeros(len(self.transformed_columns),int)
            for tree,cols in zip(self.estimator.estimators_,self.estimator.estimators_features_):
                local=tree.tree_.feature;local=local[local>=0]
                used[np.unique(np.asarray(cols)[local])]+=1
            self.extra_fit_distribution['trees_using_each_input']=used.tolist()
        self.encoder.release()
        self.columns=tuple(dict.fromkeys([*RAW,*COHORT,'residual_mean_1m','log_max_duration_1m','log_mean_count_5m',
            'adapterType','processId','completion_count_1m',*(self.encoder.geometry.columns if hasattr(self.encoder,'geometry')else())]))
        return self
    def record(self):
        from shared_model_v7.role_priority import TARGETS
        return {**super().record(),'requested_role':TARGETS[self.algorithm],'role_not_inference_truth_routing':True,'other_task_constraints_unchanged':True,'old_estimator_embedded':False,
            'extra_fit_distribution':getattr(self,'extra_fit_distribution',None)}
