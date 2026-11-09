"""Verify the EXPORTED handoff, tests and executed notebook; set local latest.

Run only after manually inspecting the generated figures. Does not deploy,
write Git/Notion, alter datasets, or replace the preceding v6 release.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'code'))
import argparse,io,unittest
import joblib
import nbformat
import numpy as np
from model_research_v13.data import read,write
from model_evaluation.data import sha256
from shared_model_v7.protocol import OUT,BASE,ALGORITHMS,TASKS,freeze
from shared_model_v7.acceptance import check,policy
from shared_model_v7.release import estimators
from shared_model_v7.progress import run as progress


def run(visual_reviewed):
    if not visual_reviewed:raise ValueError('Inspect generated figures before final local latest pointer')
    p=freeze();dest=OUT/'release';summary=read(dest/'summary.json');selection=read(dest/'selection.json')
    assert check(summary,p['baseline_summary'],**policy(p))['all_targets_met']
    assert summary['all_targets_met']and summary['requested_roles']['all_desired_models_observed_best']
    assert sha256(dest/'models.joblib')==selection['model_sha256']
    bundle=joblib.load(dest/'models.joblib');manifest=read(dest/'handoff/manifest.json');source=ROOT/selection['source_attempt']
    for a in ALGORITHMS:
        rec=next(r for r in manifest['models']if r['algorithm']==a)
        path=dest/'handoff'/rec['file'];assert sha256(path)==rec['sha256']
        monitor=joblib.load(path);assert len(estimators(monitor.detector))==1
        for t in TASKS:
            z=np.load(dest/(t+'_scores.npz'));prior=np.load(source/(a+'_'+t+'_scores.npz'))
            np.testing.assert_array_equal(z['scores'][:,list(ALGORITHMS).index(a)],prior['scores'])
            for k in ('y','case_index','adapter'):np.testing.assert_array_equal(z[k],prior[k])
        t=TASKS[0];idx=read(ROOT/'reports/shared_model_v2/feature_cache/4f5469a098b82372'/t/'index.json')
        path=ROOT/'reports/shared_model_v3/feature_cache/4f5469a098b82372'/t/idx[0]['file']
        f=joblib.load(path)['frame'].iloc[:100].copy();expected=np.load(dest/(t+'_scores.npz'))['scores'][:100,list(ALGORITHMS).index(a)]
        np.testing.assert_allclose(monitor.detector.score(f),expected,rtol=1e-10,atol=1e-10)
        np.testing.assert_array_equal(bundle['models'][a].score(f),monitor.detector.score(f))
    modules=('shared_model_v7.test_v7','shared_model_v6.test_v6','shared_model_v5.test_v5',
             'shared_model_v4.test_v4','shared_model_v3.test_v3','shared_model_v2.test_v2',
             'shared_model_target.test_target','shared_model_improvement.test_models','adapter_feature_ablation.test_ablation')
    tests=unittest.defaultTestLoader.loadTestsFromNames(modules);stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream).run(tests);print(stream.getvalue(),flush=True)
    assert result.wasSuccessful()
    report=read(dest/'report.json');nb=nbformat.read(ROOT/report['notebook'],as_version=4);nbformat.validate(nb)
    assert report['executed']and all(not any(o.output_type=='error'for o in c.get('outputs',[]))for c in nb.cells)
    assert all(c.get('execution_count')is not None for c in nb.cells if c.cell_type=='code')
    for name in ('recall_f1_comparison','false_positive_comparison','input_feature_distribution',
                 'if_score_distribution','kmeans_score_distribution','lof_score_distribution'):
        assert(dest/(name+'.png')).stat().st_size>1000
    audit=read(dest/'audit.json');audit.update({'exported_handoff_scores_verified_against_full_replay':True,
        'unit_tests_run':result.testsRun,'unit_tests_passed':True,'executed_notebook_validated':True,
        'figure_visual_review_completed':True,'report_notebook_sha256':sha256(ROOT/report['notebook']),
        'no_benchmark_or_ADBench_changes':True,'production_deployed':False})
    write(dest/'audit.json',audit)
    write(OUT/'latest.json',{'current_release':str(dest.relative_to(ROOT)),'notebook':report['notebook'],
        'all_targets_met':True,'each_model_task_Recall_at_least_55':True,'FPR_limit':.06,
        'all_task_accuracy_precision_F1_nonregression':True,'requested_roles_observed':True,
        'local_research_only':True,'external_blind_test':False,'ADBench_unchanged':True,'production_deployed':False})
    progress();print('V7 LOCAL FINAL AUDIT PASSED',result.testsRun,'tests; notebook and exported handoff verified',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--visual-reviewed',action='store_true')
    run(parser.parse_args().visual_reviewed)
