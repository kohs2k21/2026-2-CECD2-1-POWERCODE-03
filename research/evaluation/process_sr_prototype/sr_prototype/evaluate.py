"""Row-level scoring conventions copied from the research lab: explicit denominators,
unjudged rows stay in the denominator, rows without an injected label are *unlabelled*
(never treated as verified normal), so no false-positive rate is produced."""
from __future__ import annotations

import pandas as pd


def trial_metrics(after, before, truth_ids, **labels):
    """`after`/`before`: DataFrame[row_id, flag] for the mutated / unmodified test cohort."""
    joined = after[['row_id', 'flag']].merge(before[['row_id', 'flag']], on='row_id', how='left',
                                              suffixes=('_after', '_before'), validate='one_to_one')
    injected = joined.row_id.isin(set(truth_ids))
    fa = joined.flag_after.astype('boolean')
    fb = joined.flag_before.astype('boolean')
    hit_after, hit_before = fa.fillna(False), fb.fillna(False)
    comparable = fa.notna() & fb.notna()
    n_inj = int(injected.sum())
    detected = int((injected & hit_after).sum())
    unlabeled = ~injected
    n_unl = int(unlabeled.sum())
    unl_alert = int((unlabeled & hit_after).sum())
    return {**labels,
            'anomaly_rows': n_inj, 'detected_anomaly_rows': detected,
            'anomaly_unjudged_rows': int((injected & fa.isna()).sum()),
            'detection_rate': detected / n_inj if n_inj else None,
            'baseline_alert_anomaly_rows': int((injected & hit_before).sum()),
            'newly_detected_rows': int((injected & comparable & ~hit_before & hit_after).sum()),
            'unlabeled_rows': n_unl, 'unlabeled_alert_rows': unl_alert,
            'unlabeled_unjudged_rows': int((unlabeled & fa.isna()).sum()),
            'unlabeled_alert_rate': unl_alert / n_unl if n_unl else None,
            'false_positive_rate': None, 'verified_normal_rows': 0}


def control_metrics(before, **labels):
    """Unmodified test cohort (C00-like): alert counts without any label."""
    flag = before.flag.astype('boolean')
    n = int(len(before))
    alerts = int(flag.fillna(False).sum())
    return {**labels, 'rows': n, 'judged_rows': int(flag.notna().sum()), 'unjudged_rows': int(flag.isna().sum()),
            'unlabeled_alert_rows': alerts, 'unlabeled_alert_rate': alerts / n if n else None,
            'false_positive_rate': None, 'verified_normal_rows': 0,
            'reason_counts': before.reason.value_counts().to_dict()}


def summarize(trials):
    """Pool trials per (instance, scenario): sums, then pooled rates."""
    frame = pd.DataFrame(trials)
    keys = ['instance', 'scenario']
    sums = frame.groupby(keys, sort=True)[['anomaly_rows', 'detected_anomaly_rows', 'anomaly_unjudged_rows',
                                           'baseline_alert_anomaly_rows', 'newly_detected_rows',
                                           'unlabeled_rows', 'unlabeled_alert_rows', 'unlabeled_unjudged_rows']].sum()
    sums['trials'] = frame.groupby(keys).size()
    sums['detection_rate'] = (sums.detected_anomaly_rows / sums.anomaly_rows).where(sums.anomaly_rows.gt(0))
    sums['unlabeled_alert_rate'] = (sums.unlabeled_alert_rows / sums.unlabeled_rows).where(sums.unlabeled_rows.gt(0))
    sums['false_positive_rate'] = None
    return sums.reset_index()


def markdown_table(frame, columns, floatfmt='{:.3f}'):
    def cell(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return '-'
        if isinstance(v, float):
            return floatfmt.format(v)
        return str(v)
    lines = ['| ' + ' | '.join(columns) + ' |', '|' + '|'.join(['---'] * len(columns)) + '|']
    for _, row in frame.iterrows():
        lines.append('| ' + ' | '.join(cell(row[c]) for c in columns) + ' |')
    return '\n'.join(lines)
