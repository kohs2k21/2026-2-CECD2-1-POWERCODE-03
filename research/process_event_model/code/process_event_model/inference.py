"""Backend-facing stateful predictor for completed original Process records."""
from pathlib import Path

import joblib
import pandas as pd

from .features import EventFeatures, normalize
from .row_context import ExactReference, RowContext


class EventMonitor:
    def __init__(self, reference, detector, session_policy="observed_date", incident_policy=None, processor_kind=None):
        self.reference = reference
        self.detector = detector
        self.session_policy = session_policy
        self.processor_kind=processor_kind
        self.processor=self._new_processor()
        self.incidents=None
        if incident_policy is not None:
            if 'delay_ttl' in incident_policy:
                from .event_policy_runtime import EventPolicyTracker
                self.incidents=EventPolicyTracker(**incident_policy)
            else:
                from .incident_runtime import IncidentTracker
                self.incidents=IncidentTracker(**incident_policy)

    def _new_processor(self):
        if self.processor_kind=='ewma_native_row_context':
            from .ewma_history import EWMANativeRowContext
            return EWMANativeRowContext(self.reference)
        if self.processor_kind in ('adaptive_row_context','native_row_context'):
            if not isinstance(self.reference,ExactReference):raise ValueError('An exact original normal reference is required')
            if self.processor_kind=='native_row_context':
                from .native_history import NativeRowContext
                return NativeRowContext(self.reference)
            from .adaptive_event import AdaptiveRowContext
            return AdaptiveRowContext(self.reference)
        if self.processor_kind=='predictive_row_context':
            from .predictive import PredictiveRowContext
            if not isinstance(self.reference,ExactReference):
                raise ValueError('Predictive row processing requires an ExactReference')
            return PredictiveRowContext(self.reference)
        if self.processor_kind not in (None,'row_context_v2'):
            raise ValueError(f'Unsupported row processor: {self.processor_kind}')
        return RowContext(self.reference) if isinstance(self.reference,ExactReference) else EventFeatures(self.reference)

    def reset(self):
        """Explicit restart/coverage boundary; never secretly reset every minute."""
        self.processor = self._new_processor()
        if self.incidents is not None:
            self.incidents.states.clear()

    def predict_features(self, features):
        score, alarm = self.detector.predict(features)
        result = features.copy()
        result["score"] = score
        result["threshold"] = self.detector.threshold
        result["anomaly"] = alarm
        result["observation_kind"] = "completed_process_execution"
        if self.incidents is not None and getattr(getattr(self.incidents,'policy',None),'traffic_model_family',False):
            result['calibrated_evidence_family']=self.detector.state(features)[0]
        if hasattr(self.detector,'calibration_context'):
            for name,values in self.detector.calibration_context(features).items():result[name]=values
        if self.incidents is not None:
            result=self.incidents.update(result,self.detector)
        return result

    def predict(self, records):
        """One output row per input Process row, original fields retained.

        Supply complete transaction payloads together, in availableTime order.
        availableTime should be actual receipt time; without it, max(endTime)
        within the supplied transaction payload is an explicitly documented proxy.
        Empty payloads are NOT an input-stop observation. Without an explicit
        case_id, observed-date sessions match the original-data fitting policy.
        """
        frame = pd.DataFrame(records).copy()
        if "case_id" not in frame and self.session_policy == "observed_date":
            frame = normalize(frame)
            dates = frame.availableTime.dt.tz_convert("Asia/Seoul").dt.normalize().astype(str)
            frame["case_id"] = frame.source_period.astype(str)+":"+dates
        return self.predict_features(self.processor.transform(frame))

    def incident_snapshot(self,now):
        if self.incidents is None:
            raise ValueError('Load with_incidents=True or provide an explicit incident policy')
        return self.incidents.snapshot(now)


def load(path, algorithm, with_incidents=False):
    """Only deserialize trusted locally trained artifacts, never untrusted pickle."""
    path=Path(path)
    package = joblib.load(path)
    policy=None
    if with_incidents:
        import json
        from .data import digest
        record=json.loads((path.parent/'incident_policy.json').read_text(encoding='utf-8'))
        if record['model_sha256']!=digest(path):raise ValueError('Incident policy belongs to a different model')
        if 'runtime_sha256' in record:
            if record['runtime_sha256']!=digest(Path(__file__).with_name('event_policy_runtime.py')):
                raise ValueError('Incident runtime code does not match the frozen policy')
        policy=record['models'][algorithm]['params']
    return EventMonitor(package["reference"], package["models"][algorithm],incident_policy=policy,
                        processor_kind=package.get('processor'))
