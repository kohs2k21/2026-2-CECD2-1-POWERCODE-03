"""Confirmed user scope: no task's Accuracy/Precision/F1 may regress."""
METRICS=('accuracy','precision','f1')

def policy(protocol):
    return {'max_fpr':protocol.get('maximum_FPR',.05),
            'fpr_inclusive':protocol.get('maximum_FPR_inclusive',False),
            'fpr_nonincrease':protocol.get('hard_FPR_nonincrease_each_task_and_pooled',True)}


def check(summary,baseline,max_fpr=.05,fpr_inclusive=False,fpr_nonincrease=True):
    def under_limit(value):return value<=max_fpr+1e-12 if fpr_inclusive else value<max_fpr
    prior={d['dataset']:{r['method']:r for r in d['metrics']}for d in baseline['datasets']}
    current={d['dataset']:{r['method']:r for r in d['metrics']}for d in summary['datasets']}
    if set(prior)!=set(current):raise ValueError('Task scope changed')
    oldpool={r['method']:r for r in baseline['pooled']};newpool={r['method']:r for r in summary['pooled']}
    result={}
    for a,b in oldpool.items():
        tasks={}
        for t in prior:
            r=current[t][a];before=prior[t][a]
            maintained={k:r[k]>=before[k]-1e-12 for k in METRICS}
            fpr_maintained=r['normal_false_positive_rate']<=before['normal_false_positive_rate']+1e-12
            tasks[t]={'Recall_at_least_55':r['recall']>=.55,'FPR_within_requested_limit':under_limit(r['normal_false_positive_rate']),
                'FPR_not_increased':fpr_maintained,
                'maintained':maintained,'changes_pp':{k:100*(r[k]-before[k])for k in (*METRICS,'recall','normal_false_positive_rate')}}
            tasks[t]['all_met']=all(maintained.values())and tasks[t]['Recall_at_least_55']and tasks[t]['FPR_within_requested_limit']and(not fpr_nonincrease or fpr_maintained)
        r=newpool[a];pooled=all(r[k]>=b[k]-1e-12 for k in (*METRICS,'recall'))and under_limit(r['normal_false_positive_rate'])
        pooled=pooled and(not fpr_nonincrease or r['normal_false_positive_rate']<=b['normal_false_positive_rate']+1e-12)
        result[a]={'tasks':tasks,'pooled_nonregression':pooled,'all_task_metrics_met':all(v['all_met']for v in tasks.values()),
            'minimum_task_Recall':min(current[t][a]['recall']for t in current)}
        result[a]['all_met']=pooled and result[a]['all_task_metrics_met']
    return {'algorithms':result,'all_targets_met':all(r['all_met']for r in result.values()),
        'max_FPR':max_fpr,'FPR_limit_inclusive':fpr_inclusive,'FPR_nonincrease_required':fpr_nonincrease,
        'scope':'each task Recall>=55%; each task Accuracy/Precision/F1>=v6; pooled metrics non-regression; explicit FPR policy recorded above'}
