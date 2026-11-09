"""Importable estimator class for portable joblib artifacts."""
from shared_model_v3.models import Detector as Parent
from shared_model_v3.sensitive import SMALL
from shared_model_target.current_features import CURRENT
from adapter_feature_ablation.features import ADAPTER

class Detector(Parent):
    def fit(self,f):
        adapter=tuple(c for c in ADAPTER if c!='adapter_workload_z')
        if self.setting.view=='small':self.derived_columns=(*SMALL,*CURRENT,*adapter)
        elif self.setting.view=='small_compact':self.derived_columns=(*SMALL,'current_arrival_excess','current_prefix_burst',
            'gap_expected_prefix','gap_expected_recent','current_source_upper','current_source_lower',*adapter)
        elif self.setting.view=='small_minimal':self.derived_columns=('small_last_duration','small_last_residual','small_ewma','small_slope',
            'seq_arrival_lift','seq_gap_lift','current_source_upper','current_source_lower',*adapter)
        else:raise ValueError(self.setting.view)
        return super().fit(f)
