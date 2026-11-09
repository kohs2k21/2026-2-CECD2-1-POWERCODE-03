"""Raw threshold-free K-means geometry ablation on fixed research cases.

This separates estimator/input geometry from later Adapter/calibration score
fusion. The reused benchmark is diagnostic selection data, NOT a blind test.
"""
import joblib
from sklearn.metrics import roc_auc_score,average_precision_score
from model_research_v13.data import write
from shared_model_v7.protocol import OUT,BASE,TASKS
from shared_model_v7.larger_probe import bank


def run():
    data=bank();original=joblib.load(BASE/'models.joblib')['models']['kmeans'].base
    models={'v6_original_raw_core':original}
    for p in sorted((OUT/'cohort_kmeans_cores').glob('*.joblib')):models[p.stem]=joblib.load(p)
    records=[]
    for name,model in models.items():
        for task in TASKS:
            y=data[task]['y'];s=model.score(data[task]['frame'])
            records.append({'model':name,'task':task,'AUROC':float(roc_auc_score(y,s)),
                            'average_precision':float(average_precision_score(y,s)),
                            'positive_prevalence':float(y.mean()),'threshold_or_Adapter_calibration_applied':False})
        write(OUT/'cohort_raw_geometry_diagnostic.json',{'records':records,
            'normal_only_estimator_training':True,'not_final_model_performance':True,'external_blind_test':False})
        print('RAW KMEANS GEOMETRY',name,[(r['task'],round(r['average_precision'],4))for r in records if r['model']==name],flush=True)


if __name__=='__main__':run()
