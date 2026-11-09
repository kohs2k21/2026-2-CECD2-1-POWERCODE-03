"""Raw-Process frontend to a trusted, calibrated hourly model bank.

Five-minute summaries are exposed separately, pending a dedicated fine-resolution
benchmark. The frozen bank still consumes correctly weighted hourly aggregates.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from ..intervals.model import checked_hours
from .aggregation import aggregate_processes, hourly_frame, kst, group_key, case_id, window_features


@dataclass(frozen=True)
class ProcessReference:
    key: tuple
    end: pd.Timestamp
    model_reference: object


class ProcessWindowService:
    def __init__(self, bank):
        self.bank = bank

    def prepare_reference(self, reviewed_normal_processes, *, end, coverage, groups=None):
        """Caller reviews normal candidates; S alone is not normal ground truth.

        History is used only here, never updated with monitoring records.
        The existing release needs 672 hourly positions (with explicit masks).
        Real exports without that support must not be padded with fabricated 0s.
        """
        end = kst(end)
        start = end - pd.Timedelta(hours=self.bank.context_hours)
        raw = aggregate_processes(reviewed_normal_processes, start=start, end=end,
                                  coverage=coverage, groups=groups)
        refs = {}
        internal = raw.bins if getattr(self.bank, "input_frequency_minutes", 60) == 5 else hourly_frame(raw)
        for identity, frame in internal.groupby("case_id", sort=False):
            b = raw.bins.loc[raw.bins.case_id.eq(identity)].iloc[0]
            key = group_key(b.adapterType, b.processId)
            refs[key] = ProcessReference(key, end, self.bank.prepare_reference(frame))
        return refs

    def predict_processes(self, records, *, references, window_end, analysis_hours, coverage=None):
        """Same original Process columns, one selectable n-hour input interval.

        Returns hourly retrospective decisions and internal five-minute
        diagnostics. A fine-bin slope alone is not a calibrated alarm.
        """
        h = checked_hours(analysis_hours)
        end = kst(window_end)
        start = end - pd.Timedelta(hours=h)
        if not references:
            raise ValueError("At least one reviewed normal reference is required")
        for key, reference in references.items():
            if not isinstance(reference, ProcessReference) or reference.key != key:
                raise TypeError("Expected correctly keyed ProcessReference objects")
            if reference.end > start:
                raise ValueError("Normal reference overlaps selected monitoring data")
        raw = aggregate_processes(records, start=start, end=end, coverage=coverage,
                                  groups=references.keys())
        decisions = []
        native = getattr(self.bank, "input_frequency_minutes", 60) == 5
        internal = raw.bins if native else hourly_frame(raw)
        for key, ref in references.items():
            batch = internal.loc[internal.case_id.eq(case_id(key))]
            result = self.bank.predict_batch(ref.model_reference, batch, analysis_hours=h).copy()
            result["adapterType"], result["processId"] = key
            result["raw_process_input"] = True
            result["fine_bin_features_used_for_alarm"] = native
            decisions.append(result)
        return {"decisions": pd.concat(decisions, ignore_index=True),
                "fine_bins": raw.bins, "window_features": window_features(raw),
                "diagnostics": {**raw.diagnostics, "analysis_hours": h,
                    "window_start": start.isoformat(), "window_end_exclusive": end.isoformat(),
                    "monitoring_state_carried": False,
                    "finer_resolution_retraining_required": not native,
                    "raw_population_performance_validated": False}}
