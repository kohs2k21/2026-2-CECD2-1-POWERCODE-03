"""Evaluation-only paired alert metrics; truth never enters the policy."""
import numpy as np
import pandas as pd
from research.evaluation.long_term_trend import evaluation as ev


def compare_alerts(baseline,alert,metadata,events,baseline_events,alert_events,cadence):
    """Keep unknown exposure, episode hits, and ongoing suppression separate."""
    before=ev.decision_intervals(metadata,ev.decisions(baseline,metadata,cadence,.5),cadence)
    after=ev.decision_intervals(metadata,ev.decisions(alert,metadata,cadence,.5),cadence)
    if not before.row_id.equals(after.row_id):raise AssertionError('Interval alignment')
    same=np.array_equal(before.prediction_available,after.prediction_available)
    if not same:raise AssertionError('Policy changed full-minute availability')
    if np.any(after.positive&~before.positive):raise AssertionError('Policy created a positive minute')
    base={r['event_id']:r for r in baseline_events};current={r['event_id']:r for r in alert_events}
    records=[];inside=np.zeros(len(before),bool);tick=pd.to_datetime(before.decision_tick,utc=True)
    clear=np.zeros(len(before),bool);reactivate=np.zeros(len(before),bool)
    for _,indices in before.groupby('group',sort=False).indices.items():
        ix=np.asarray(indices);ready=after.prediction_available.to_numpy(bool)[ix];positive=after.positive.to_numpy(bool)[ix]
        previous_positive=np.r_[False,positive[:-1]];previous_ready=np.r_[False,ready[:-1]]
        previous_baseline_positive=np.r_[False,before.positive.to_numpy(bool)[ix][:-1]]
        clear[ix]=ready&previous_ready&previous_positive&~positive&before.positive.to_numpy(bool)[ix]
        reactivate[ix]=ready&previous_ready&previous_baseline_positive&~previous_positive&positive&before.positive.to_numpy(bool)[ix]
    for e in events.to_dict('records'):
        mask=before.group.eq(e['group']).to_numpy()&tick.ge(e['start']).to_numpy()&tick.lt(e['end']).to_numpy()
        inside|=mask;b=base[e['event_id']];a=current[e['event_id']]
        first_before=tick[mask&before.positive.to_numpy(bool)];first_after=tick[mask&after.positive.to_numpy(bool)]
        next_events=events.loc[events.group.eq(e['group'])&events.start.ge(e['end']),'start']
        horizon=next_events.min() if len(next_events) else tick[before.group.eq(e['group'])].max()+pd.Timedelta(minutes=1)
        recovered=before.group.eq(e['group']).to_numpy()&tick.ge(e['end']).to_numpy()&tick.lt(horizon).to_numpy()
        negatives=np.flatnonzero(recovered&after.prediction_available.to_numpy(bool)&~after.positive.to_numpy(bool))
        # Stability is limited to the next scheduled service event or split end.
        bad=np.flatnonzero(recovered&(~after.prediction_available.to_numpy(bool)|after.positive.to_numpy(bool)))
        stable=negatives[negatives>(bad.max() if len(bad) else -1)]
        delay=lambda indices:float((tick.iloc[indices[0]]-pd.Timestamp(e['end'])).total_seconds()/60) if len(indices) else None
        active_indices=np.flatnonzero(mask);end_indices=np.flatnonzero(before.group.eq(e['group']).to_numpy()&tick.eq(e['end']).to_numpy())
        def status(frame,indices):
            if not len(indices):return None
            index=indices[-1]
            return bool(frame.positive.iloc[index]) if frame.prediction_available.iloc[index] else None
        before_end_base=status(before,active_indices);before_end_alert=status(after,active_indices)
        precleared=before_end_base is True and before_end_alert is False
        censor=('all_unknown' if not (mask&before.prediction_available.to_numpy(bool)).any() else
                'baseline_miss' if not b['hit'] else 'alert_miss' if not a['hit'] else
                'precleared_before_truth_end' if precleared else None)
        records.append({'event_id':e['event_id'],'group':e['group'],'scenario':e['scenario'],
            'baseline_hit':b['hit'],'alert_hit':a['hit'],'lost_hit':bool(b['hit'] and not a['hit']),
            'added_reactivation_capture':bool(a['hit'] and not b['hit']),
            'matched_onset_added_delay_minutes':a['delay_minutes']-b['delay_minutes'] if a['hit'] and b['hit'] else None,
            'first_positive_added_delay_minutes':float((first_after.iloc[0]-first_before.iloc[0]).total_seconds()/60) if len(first_before) and len(first_after) else None,
            'active_suppressed_minutes':int((mask&before.positive.to_numpy(bool)&~after.positive.to_numpy(bool)).sum()),
            'active_premature_clear_count':int((mask&clear).sum()),'active_reactivation_count':int((mask&reactivate).sum()),
            'last_active_baseline_positive':before_end_base,'last_active_alert_positive':before_end_alert,
            'event_end_baseline_positive':status(before,end_indices),'event_end_alert_positive':status(after,end_indices),
            'recovery_precleared_before_truth_end':precleared,
            'recovery_delay_censored_due_to_preclear':precleared,
            'recovery_first_negative_delay_minutes':None if censor else delay(negatives),
            'recovery_stable_negative_delay_minutes':None if censor else delay(stable),
            'recovery_first_negative_censor_reason':censor or ('no_negative' if not len(negatives) else None),
            'recovery_stable_negative_censor_reason':censor or ('no_stable_negative' if not len(stable) else None),
            'recovery_horizon_unknown_minutes':int((recovered&~after.prediction_available.to_numpy(bool)).sum()),
            'recovery_first_negative_unobserved':not bool(len(negatives)),
            'recovery_stable_negative_unobserved':not bool(len(stable)),'recovery_horizon':horizon})
    suppressed=before.positive.to_numpy(bool)&~after.positive.to_numpy(bool)
    return {'finite_unknown_interval_mask_equal':same,'lost_hit_ids':[r['event_id'] for r in records if r['lost_hit']],
        'added_reactivation_capture_ids':[r['event_id'] for r in records if r['added_reactivation_capture']],
        'active_suppressed_minutes':int((inside&suppressed).sum()),'active_premature_clear_count':int((inside&clear).sum()),
        'active_reactivation_count':int((inside&reactivate).sum()),'all_clear_count':int(clear.sum()),
        'all_reactivation_count':int(reactivate.sum()),'event_details':records}
