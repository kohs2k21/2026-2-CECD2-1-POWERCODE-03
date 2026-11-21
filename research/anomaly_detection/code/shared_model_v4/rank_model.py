"""One estimator with causal sequence evidence; no Adapter experts."""
from dataclasses import dataclass
import pandas as pd
from shared_model_v3.models import Detector as Base, Setting as BaseSetting
from shared_model_v4.rank_context import Context, RANK, no_context
from shared_model_v4.manifold import COLUMNS
from shared_model_v3.features import SEQUENCE

@dataclass(frozen=True)
class Setting(BaseSetting):
    view:str='rank'
    rank_view:str='compact'
    rank_prefix:int=6
    context_dropout:bool=False

class Detector(Base):
    def __init__(self,setting,history):
        super().__init__(setting);self.context=Context(history,setting.rank_prefix)
    def derive(self,f):
        if set(RANK)<=set(f):return f
        return pd.concat([f,self.context.transform(f)],axis=1)
    def fit(self,f):
        s=self.setting
        if s.rank_view=='compact':cols=(*COLUMNS[:5],'current_source_upper','current_source_lower',*COLUMNS[17:26],*RANK)
        elif s.rank_view=='sequence':cols=(*SEQUENCE,*COLUMNS[17:26],*RANK)
        elif s.rank_view=='minimal':cols=(*COLUMNS[:5],'current_source_upper','current_source_lower',*RANK)
        else:cols=(*COLUMNS,*RANK)
        self.derived_columns=tuple(dict.fromkeys(cols))
        x=self.derive(f)
        if s.context_dropout:x=pd.concat([x,self.derive(no_context(f.drop(columns=list(RANK),errors='ignore')))],ignore_index=True)
        return super().fit(x)
    def score(self,f):return super().score(self.derive(f))
    def record(self):
        return {**super().record(),'causal_context':True,'fixed_prefix_completed_observations':self.context.prefix,
            'new_hard_rule':False,'requires_contiguous_minutes':True,'unavailable_context_zero_plus_mask':True}
