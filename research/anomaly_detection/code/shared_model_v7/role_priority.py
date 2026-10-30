"""User-requested model roles: preferences AFTER all safety/performance gates."""
TARGETS={'if':'individual_delay','kmeans':'group_delay','lof':'gradual_degradation'}


def task_f1(row,algorithm):
    return row['tasks'][TARGETS[algorithm]]['f1']


def analyze(summary):
    tasks={d['dataset']:{r['method']:r for r in d['metrics']}for d in summary['datasets']};rows=[]
    for algorithm,task in TARGETS.items():
        r=tasks[task][algorithm];others={a:x['f1']for a,x in tasks[task].items()if a!=algorithm}
        best=max(others.values());rows.append({'requested_model':algorithm,'task':task,'F1':r['f1'],'Precision':r['precision'],'Recall':r['recall'],
            'F1_gap_to_best_other_pp':100*(r['f1']-best),'observed_best_F1':r['f1']>best,'not_statistical_significance_claim':True})
    return {'requested_roles':rows,'all_desired_models_observed_best':all(r['observed_best_F1']for r in rows),
        'others_intentionally_weakened':False,'role_definition':'prefer target task F1 subject to every task and pooled constraints; report Precision/Recall too'}
