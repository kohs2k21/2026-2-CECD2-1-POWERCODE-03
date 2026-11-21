"""Ablation of prior-vs-self cadence and pre-pooling channel scales."""
from dataclasses import dataclass
import numpy as np
from shared_model_v6.features import Evidence,RAW,BLOCKS
from shared_model_v6.models import Detector as Base,Setting as BaseSetting

@dataclass(frozen=True)
class Setting(BaseSetting):
    gap_gain:float=1.
    burst_gain:float=1.
    self_cadence_only:bool=False

class FocusedEvidence(Evidence):
    def __init__(self,history,setting):
        super().__init__(history,setting.conditional,setting.transform,setting.tail)
        self.focus_setting=setting
    def summarize(self,x,view):
        s=self.focus_setting;x=x.copy()
        if s.self_cadence_only:x[:,RAW.index('traffic_zero_logprob')]=0.
        for c in BLOCKS['gap']:x[:,RAW.index(c)]*=s.gap_gain
        for c in BLOCKS['burst']:x[:,RAW.index(c)]*=s.burst_gain
        return super().summarize(x,view)

class Detector(Base):
    def __init__(self,setting,history):
        super().__init__(setting,history);self.encoder=FocusedEvidence(history,setting)
