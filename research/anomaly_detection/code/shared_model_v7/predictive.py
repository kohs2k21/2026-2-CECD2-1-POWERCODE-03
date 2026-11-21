"""Normal conditional duration quantiles as numeric anomaly features.

Regressors learn normal duration, NOT fault labels. Process-family/Adapter
categories enter the normal baseline generator; the anomaly estimator still
gets numeric residuals, not identifier codes or Adapter experts.
"""
from dataclasses import dataclass
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits
from shared_model_v7.semantic import family
from shared_model_v6.features import Evidence,RAW
from shared_model_v6.models import Setting as ParentSetting,Detector as Base

class Quantiles:
    def values(self,f):
        categories=[f.adapterType.astype(str),f.processId.map(family)]
        coded=[]
        for series,table in zip(categories,self.categories):coded.append(series.map(table).to_numpy(float))
        columns=[*coded,f.log_mean_count_5m.to_numpy(float),np.log1p(f.completion_count_1m.to_numpy(float)),f.adapter_reference_log_duration.to_numpy(float)]
        return np.column_stack(columns)
    def fit(self,f):
        self.categories=[]
        for series in(f.adapterType.astype(str),f.processId.map(family)):
            allowed=series.value_counts().index[:250];self.categories.append({v:i for i,v in enumerate(allowed)})
        x=self.values(f);y=f.log_max_duration_1m.to_numpy(float);ok=f.latency_assessed.to_numpy(bool)&np.isfinite(y)
        self.models=[]
        with threadpool_limits(limits=2):
            for q in(.5,.95):
                model=HistGradientBoostingRegressor(loss='quantile',quantile=q,max_iter=160,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=2.,
                    categorical_features=[True,True,False,False,False],random_state=42)
                model.fit(x[ok],y[ok]);self.models.append(model)
        return self
    def transform(self,f):
        x=self.values(f)
        with threadpool_limits(limits=2):mid=self.models[0].predict(x);upper=self.models[1].predict(x)
        spread=np.maximum(upper-mid,.15);observed=f.latency_assessed.to_numpy(bool);y=np.nan_to_num(f.log_max_duration_1m.to_numpy(float),nan=0.)
        return np.column_stack([np.where(observed,np.maximum((y-mid)/spread,0.),0.),np.where(observed,np.maximum(y-upper,0.)/spread,0.)])

@dataclass(frozen=True)
class Setting(ParentSetting):
    baseline_mode:str='joint'

class Encoder:
    def __init__(self,history,s):self.core=Evidence(history);self.setting=s
    def fit(self,f):
        self.core.fit(f);self.quantiles=Quantiles().fit(f);x=self.quantiles.transform(f);self.center=np.median(x,axis=0)
        self.scale=np.maximum(np.quantile(x,.95,axis=0)-self.center,.15);return self
    def transform(self,f,view):
        peak=self.core.transform(f,'peaks').copy();extra=np.minimum(np.maximum((self.quantiles.transform(f)-self.center)/self.scale,0.),100.)
        mode=self.setting.baseline_mode
        if mode=='joint':peak[:,0]=np.sqrt(peak[:,0]*extra[:,0])
        elif mode=='regression':peak[:,0]=extra[:,0]
        if view=='peaks':return peak
        if mode=='append':out=np.column_stack([peak,extra])
        else:out=np.column_stack([peak,extra[:,1]])
        if self.setting.transform=='asinh':out=np.arcsinh(out)
        return out
    def names(self,view):return ['point','delay','trend','burst','gap']+(['normal_duration_z','normal_upper_margin']if self.setting.baseline_mode=='append'else['normal_upper_margin'])

class Detector(Base):
    def __init__(self,s,history):super().__init__(s,history);self.encoder=Encoder(history,s)
    def fit(self,f):super().fit(f);self.columns=tuple(dict.fromkeys([*RAW,'log_max_duration_1m','log_mean_count_5m','completion_count_1m','adapter_reference_log_duration']));return self
    def record(self):return {**super().record(),'normal_quantile_regression_feature_models':2,'fault_labels_fit_by_regressors':False,'identifier_codes_enter_anomaly_estimator':False}
