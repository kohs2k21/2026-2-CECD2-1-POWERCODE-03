from shared_model_v6.models import Detector as Base,Setting
from shared_model_v7.features import Evidence,RAW

class Detector(Base):
    def __init__(self,setting,history):
        super().__init__(setting,history);self.encoder=Evidence(history,setting)
    def fit(self,f):
        super().fit(f);self.columns=RAW;return self
    def record(self):return {**super().record(),'multi_scale_traffic_not_current_gate_only':True,'representation_version':'v7-17source'}
