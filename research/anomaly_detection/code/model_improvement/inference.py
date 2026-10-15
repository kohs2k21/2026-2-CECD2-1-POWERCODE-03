"""Backend-facing interface to the frozen research candidate.

Apply existing hard rules upstream. Keep a rolling state per monitored group;
run on a timer even when no Process arrives (otherwise input_stop is invisible).
Caller supplies grid and PRIOR traffic history, not benchmark oracle tables.
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from model_evaluation.data import sha256
from model_evaluation.models import METHODS
from model_improvement.features import build_features


class ProcessMonitor:
    def __init__(self, model_path, trusted_sha256, prior_history):
        model_path = Path(model_path)
        if sha256(model_path) != trusted_sha256:
            raise ValueError("Untrusted or changed model artifact")
        # joblib can execute code: only load locally produced, verified artifacts.
        self.suite = joblib.load(model_path)
        self.prior_history = prior_history

    def score(self, minute_grid, completed_processes):
        """Grid: case_id (stream ID), adapterType, processId, window_start/end,
        execution_arrivals. Completions: case_id, adapterType, processId,
        totalCount, duration (ms), end_seconds. Stream history must include
        the preceding context; do not reset it on each backend HTTP request.
        """
        self.validate(minute_grid, completed_processes)
        features = build_features(minute_grid, completed_processes, self.prior_history, self.suite.baseline)
        if hasattr(self.suite, "legacy_baselines"):
            from model_improvement.refine import with_legacy
            features = with_legacy(features, self.suite.legacy_baselines, self.prior_history)
        margin, prediction, covered = self.suite.score(features)
        output = features[["case_id", "adapterType", "processId", "window_start", "window_end"]].copy()
        output["expert_available"] = covered
        output["route"] = pd.Series(covered).map({True: "global+adapter", False: "global_fallback"}).to_numpy()
        for i, method in enumerate(METHODS):
            output[method+"_margin"] = margin[:, i]
            output[method+"_anomaly"] = prediction[:, i]
        return output

    @staticmethod
    def validate(grid, rows):
        required_grid = {"case_id", "adapterType", "processId", "window_start", "window_end", "execution_arrivals"}
        required_rows = {"case_id", "adapterType", "processId", "totalCount", "duration", "end_seconds"}
        if not len(grid) or not required_grid.issubset(grid) or not required_rows.issubset(rows):
            raise ValueError("Missing Process/grid schema or empty decision grid")
        if grid[list(required_grid)].isna().any().any():
            raise ValueError("Missing monitored group or decision value")
        starts = pd.to_datetime(grid.window_start, format="ISO8601")
        ends = pd.to_datetime(grid.window_end, format="ISO8601")
        if not (ends-starts).eq(pd.Timedelta(minutes=1)).all():
            raise ValueError("Decision intervals must be exactly one minute")
        ordered = grid.assign(_start=starts).sort_values(["case_id", "_start"])
        gaps = ordered.groupby("case_id")._start.diff().dropna()
        if not gaps.eq(pd.Timedelta(minutes=1)).all():
            raise ValueError("Keep a contiguous minute grid, including empty windows")
        groups = grid[["case_id", "adapterType", "processId"]].drop_duplicates()
        if groups.case_id.duplicated().any():
            raise ValueError("Each stream must monitor exactly one Adapter/Process group")
        if len(rows):
            groups = groups.set_index("case_id")
            if not rows.case_id.isin(groups.index).all():
                raise ValueError("Unmapped completion stream")
            if not (rows.adapterType.eq(rows.case_id.map(groups.adapterType)) &
                    rows.processId.eq(rows.case_id.map(groups.processId))).all():
                raise ValueError("Completion belongs to a different monitored group")
            for column in ("duration", "end_seconds", "totalCount"):
                numeric = pd.to_numeric(rows[column], errors="raise")
                if not np.isfinite(numeric).all() or (column != "end_seconds" and (numeric < 0).any()):
                    raise ValueError("Invalid completion value; hard rules must run first")
