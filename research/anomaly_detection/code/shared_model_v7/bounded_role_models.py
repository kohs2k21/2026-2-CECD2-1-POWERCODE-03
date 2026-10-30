from dataclasses import dataclass
import numpy as np
from shared_model_v7.role_models import Setting as RoleSetting,Encoder as RoleEncoder,Detector as RoleDetector
from shared_model_v7.role_context import COHORT,TREND
from shared_model_v7.subspace_forest import Paths,REFERENCES
from shared_model_target.calibration import _surprise

CALIBRATION_NORMAL=None


@dataclass(frozen=True)
class Setting(RoleSetting):
    subspace_blend:float=1.


class Encoder(RoleEncoder):
    def selected(self):
        return (0,1,4)if self.setting.algorithm=='kmeans'else(0,5,6)
    def fit(self,f):
        super().fit(f)
        if self.setting.algorithm!='if':self.refs=[np.sort(self.fit_extra[:,j])for j in self.selected()]
        return self
    def transform(self,f,view):
        if self.setting.algorithm=='if':return super().transform(f,view)
        if view=='peaks':return self.core.transform(f,'peaks')
        raw=self.fit_extra if f is getattr(self,'fit_frame',None)else self.extras(f)
        tail=np.column_stack([_surprise(raw[:,j],r)for j,r in zip(self.selected(),self.refs)])
        extra=np.arcsinh(tail)*self.setting.extra_gain
        return np.column_stack([self.geometry.values(f),extra])
    def names(self,view):
        if self.setting.algorithm=='if':return super().names(view)
        names=COHORT if self.setting.algorithm=='kmeans'else TREND
        return [*self.geometry.names,*['bounded_'+names[j]for j in self.selected()]]


class Detector(RoleDetector):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(s,history)
    def fit(self,f):
        super().fit(f)
        if self.algorithm=='if':
            if CALIBRATION_NORMAL is None:raise ValueError('Separate normal score-reference cases required')
            self.paths=Paths(self.estimator);x=self.values(CALIBRATION_NORMAL);groups=self.paths.group_scores(x)
            self.group_refs=[np.sort(groups[:,j])for j in range(groups.shape[1])]
            self.whole_ref=np.sort(-self.estimator.score_samples(x))
        return self
    def score(self,f):
        if self.algorithm!='if':return super().score(f)
        x=self.values(f)
        if self.setting.subspace_blend==0:return -self.estimator.score_samples(x)
        groups=self.paths.group_scores(x);per_group=np.column_stack([_surprise(groups[:,j],r)for j,r in enumerate(self.group_refs)])
        ordinary=_surprise(-self.estimator.score_samples(x),self.whole_ref)
        return (1-self.setting.subspace_blend)*ordinary+self.setting.subspace_blend*per_group.max(axis=1)
    def record(self):
        return {**super().record(),'normal_bounded_auxiliary_features':self.algorithm!='if',
            'score_is_unmodified_sklearn_IF':False if self.algorithm=='if'else None,
            'within_forest_subspace_aggregation':self.setting.subspace_blend if self.algorithm=='if'else None,
            'fitted_IsolationForest_count':1 if self.algorithm=='if'else 0,
            'references':REFERENCES if self.algorithm=='if'else(),
            'original_feature_bagging_paper_reproduced':False}
