"""C00: no-change serialization control, never a verified-normal cohort."""
import numpy as np
import pandas as pd

from research.evaluation.trainer_lab.datasets import load_input
from process_anomaly_pipeline.data import PROCESS_COLUMNS
from .data import sha256
from .evaluate import PATHS


def reload_canonical_snapshot(test_csv):
    """Normalize canonical fields and preserve recorded parser diagnostics.

    A canonical snapshot already replaced malformed source values with missing
    values. Re-parsing those missing values cannot reconstruct their invalidity.
    Internal _invalid_* booleans therefore travel with this snapshot explicitly.
    """
    reloaded = load_input(test_csv)
    columns = [name for name in reloaded if name.startswith("_invalid_")]
    if columns:
        raw = pd.read_csv(test_csv, usecols=columns, dtype="string", encoding="utf-8-sig")
        for name in columns:
            flags = raw[name].str.lower().map({"true": True, "false": False})
            if flags.isna().any():
                raise ValueError("C00 invalid recorded parser diagnostic")
            reloaded[name] = flags.astype(bool)
    return reloaded, columns


def noop_report(test_csv, baseline_predictions, model, *, expected_frame=None):
    """Reload an already-written canonical CSV and require exact prediction parity."""
    before_hash = sha256(test_csv)
    reloaded, diagnostic_columns = reload_canonical_snapshot(test_csv)
    if "row_id" not in reloaded or reloaded.row_id.isna().any() or reloaded.row_id.duplicated().any():
        raise ValueError("C00 requires unique baseline identities")
    if (baseline_predictions.row_id.isna().any() or baseline_predictions.row_id.duplicated().any()
            or set(reloaded.row_id) != set(baseline_predictions.row_id)):
        raise ValueError("C00 baseline cohort identity mismatch")
    result = model.predict(reloaded)
    base = baseline_predictions.set_index("row_id").sort_index()
    actual = result.set_index("row_id").reindex(base.index)
    decisions_equal = {name: base[name].astype("boolean").reset_index(drop=True).equals(
        actual[name].astype("boolean").reset_index(drop=True)) for name in PATHS}
    scores_before = base.ml_score.to_numpy(dtype=float)
    scores_after = actual.ml_score.to_numpy(dtype=float)
    scores_equal = bool(np.array_equal(scores_before, scores_after, equal_nan=True))
    file_unchanged = before_hash == sha256(test_csv)
    preserved = None
    if expected_frame is not None:
        preserved = {}
        columns = [name for name in (*PROCESS_COLUMNS, "source_period", "row_id", "duration_ms", *diagnostic_columns)
                   if name in expected_frame]
        original = expected_frame.set_index("row_id").reindex(base.index)
        snapshot = reloaded.set_index("row_id").reindex(base.index)
        for name in columns:
            if name == "row_id":
                continue
            try:
                left = original[name].reset_index(drop=True)
                right = snapshot[name].reset_index(drop=True)
                if pd.api.types.is_datetime64_any_dtype(left) and pd.api.types.is_datetime64_any_dtype(right):
                    # Equal timestamps may use us vs ns storage after CSV parsing.
                    # Normalize units without rounding values or relaxing equality.
                    left = left.astype("datetime64[ns]")
                    right = right.astype("datetime64[ns]")
                pd.testing.assert_series_equal(left, right,
                                               check_dtype=False, check_exact=True)
                preserved[name] = True
            except AssertionError:
                preserved[name] = False
    fields_equal = preserved is None or all(preserved.values())
    report = {"control": "C00", "meaning": "no_mutation_roundtrip_not_normal_truth", "rows": len(base),
              "canonical_csv_sha256": before_hash, "input_unchanged": file_unchanged,
              "decision_and_unjudged_equal": decisions_equal, "scores_exactly_equal": scores_equal,
              "recorded_parser_diagnostics": diagnostic_columns, "canonical_field_equal": preserved,
              "truth_rows_created": 0,
              "status": "passed" if all(decisions_equal.values()) and scores_equal and file_unchanged and fields_equal else "failed"}
    return report, result
