"""Explicit row-level denominators; unknown truth is never a normal label."""
import pandas as pd

PATHS = ("HR_existing", "SR_existing", "ML_provisional")


def _slices(joined):
    slices = [("overall", "all", joined)]
    for dimension in ("scenario", "adapter", "interface"):
        if dimension in joined:
            slices.extend((dimension, str(name), group) for name, group in joined.groupby(dimension, dropna=False, observed=True))
    return slices


def score_rows(truth, predictions):
    if truth.row_id.duplicated().any() or predictions.row_id.duplicated().any():
        raise ValueError("Duplicate evaluation identity")
    if truth.row_id.isna().any() or predictions.row_id.isna().any():
        raise ValueError("Missing evaluation identity")
    if truth.label.isna().any() or not truth.label.isin([0, 1]).all():
        raise ValueError("Explicit labels must be 0 or 1")
    joined = truth.merge(predictions, on="row_id", how="left", validate="one_to_one")
    reports = []
    for dimension, name, group in _slices(joined):
        for path in PATHS:
            positive = group.label.eq(1)
            normal = group.label.eq(0)
            result = group[path].astype("boolean")
            tp = int((positive & result.fillna(False)).sum())
            fp = int((normal & result.fillna(False)).sum())
            positive_n, normal_n = int(positive.sum()), int(normal.sum())
            reports.append({"dimension": dimension, "group": name, "path": path,
                            "anomaly_rows": positive_n, "detected_anomaly_rows": tp,
                            "anomaly_unjudged_rows": int((positive & result.isna()).sum()),
                            "detection_rate": tp / positive_n if positive_n else None,
                            "verified_normal_rows": normal_n, "false_positive_rows": fp,
                            "normal_unjudged_rows": int((normal & result.isna()).sum()),
                            "false_positive_rate": fp / normal_n if normal_n else None,
                            "unjudged_rows": int(result.isna().sum())})
    return reports


def paired_score_rows(truth, baseline_predictions, post_predictions):
    """Distinguish existing alerts from changes caused by an injected mutation.

    Baseline is the original test cohort identity registry. Missing post results
    remain unjudged. No normality is inferred from pre-injection predictions.
    """
    for label, frame in (("truth", truth), ("baseline", baseline_predictions), ("post", post_predictions)):
        if frame.row_id.isna().any() or frame.row_id.duplicated().any():
            raise ValueError(f"Invalid or duplicate {label} identity")
    if truth.label.isna().any() or not truth.label.isin([0, 1]).all():
        raise ValueError("Explicit labels must be 0 or 1")
    baseline_ids = set(baseline_predictions.row_id)
    if not set(truth.row_id).issubset(baseline_ids) or not set(post_predictions.row_id).issubset(baseline_ids):
        raise ValueError("Evaluation identity absent from baseline cohort")
    positive = truth.loc[truth.label.eq(1)].copy()
    before = baseline_predictions[["row_id", *PATHS]].rename(columns={path: f"{path}_before" for path in PATHS})
    after = post_predictions[["row_id", *PATHS]].rename(columns={path: f"{path}_after" for path in PATHS})
    joined = positive.merge(before, on="row_id", how="left", validate="one_to_one").merge(
        after, on="row_id", how="left", validate="one_to_one")
    reports = []
    for dimension, name, group in _slices(joined):
        for path in PATHS:
            baseline = group[f"{path}_before"].astype("boolean")
            post = group[f"{path}_after"].astype("boolean")
            comparable = baseline.notna() & post.notna()
            before_hit, after_hit = baseline.fillna(False), post.fillna(False)
            reports.append({"dimension": dimension, "group": name, "path": path,
                            "injected_anomaly_rows": len(group),
                            "baseline_alert_rows": int(before_hit.sum()), "post_alert_rows": int(after_hit.sum()),
                            "newly_detected_rows": int((comparable & ~before_hit & after_hit).sum()),
                            "persistent_alert_rows": int((comparable & before_hit & after_hit).sum()),
                            "lost_alert_rows": int((comparable & before_hit & ~after_hit).sum()),
                            "unchanged_nonalert_rows": int((comparable & ~before_hit & ~after_hit).sum()),
                            "paired_assessed_rows": int(comparable.sum()),
                            "baseline_unjudged_rows": int(baseline.isna().sum()),
                            "post_unjudged_rows": int(post.isna().sum()),
                            "paired_unjudged_rows": int((~comparable).sum())})
    return reports


def control_report(frame, predictions):
    from .data import reference_mask
    if frame.row_id.duplicated().any() or predictions.row_id.duplicated().any():
        raise ValueError("Duplicate control identity")
    predictions = frame[["row_id"]].merge(predictions, on="row_id", how="left", validate="one_to_one")
    proxy = reference_mask(frame).to_numpy()
    reports = {}
    for path in PATHS:
        values = predictions[path].astype("boolean")
        reports[path] = {"rows": len(frame), "assessed_rows": int(values.notna().sum()),
                         "unjudged_rows": int(values.isna().sum()),
                         "unlabelled_alert_rows": int(values.fillna(False).sum()),
                         "successful_proxy_rows": int(proxy.sum()),
                         "successful_proxy_alert_rate": float(values.loc[proxy].fillna(False).mean()) if proxy.any() else None,
                         "false_positive_rate": None, "verified_normal_rows": 0,
                         "reason": "No independently verified normal labels supplied"}
    return reports
