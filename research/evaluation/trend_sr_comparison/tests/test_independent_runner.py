"""Gate rejection must happen before any sealed source can be generated."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from research.evaluation.trend_sr_comparison import runner


class RunnerContracts(unittest.TestCase):
    def test_source_digest_covers_entire_namespace_python_and_json(self):
        root = Path(runner.__file__).parent
        expected = {p.relative_to(root).as_posix():runner.file_hash(p) for p in root.rglob('*') if p.suffix in ('.py','.json')}
        for name in ('long_term_trend/features.py','long_term_trend/dataset.py','long_term_trend/protocol.json','long_term_trend/models.py','trend_family_comparison/detectors.py','trend_recovery_comparison/signals.py'):
            expected['../'+name]=runner.file_hash(root.parent/name)
        self.assertEqual(runner.source_digest(),expected)

    def test_bad_development_gate_cannot_generate_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner.dataset,'generate',side_effect=AssertionError('opened sealed source')):
            gate = Path(folder)/'gate.json'
            for payload in ({'phase':'development','approved':False}, {'phase':'final','approved':True}, {'phase':'development','approved':True,'source_hashes':{}}):
                gate.write_text(json.dumps(payload))
                with self.assertRaises(ValueError): runner.run_development(Path(folder)/'out',gate,limit=1)
                self.assertFalse((Path(folder)/'out').exists())

    def test_final_gate_lock_source_and_artifact_corruption_reject_before_generation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner.dataset,'generate',side_effect=AssertionError('opened final source')):
            base = Path(folder); dev = base/'dev'; dev.mkdir()
            artifact = dev/'frozen.txt'; artifact.write_text('frozen')
            lock = dev/'selection-lock.json'; gate=base/'gate.json'
            def setup(source=None, artifacts=None, digest=None):
                lock.write_text(json.dumps({'source_hashes':runner.source_digest() if source is None else source,
                    'artifact_hashes':{str(artifact):runner.file_hash(artifact)} if artifacts is None else artifacts}))
                gate.write_text(json.dumps({'phase':'final','approved':True,'selection_lock_sha256':runner.file_hash(lock) if digest is None else digest}))
            setup(digest='wrong')
            with self.assertRaisesRegex(ValueError,'lock_mismatch'): runner.run_final(dev,base/'out',gate)
            setup(source={})
            with self.assertRaisesRegex(ValueError,'source_changed'): runner.run_final(dev,base/'out',gate)
            setup(artifacts={str(artifact):'wrong'})
            with self.assertRaisesRegex(ValueError,'artifact_changed'): runner.run_final(dev,base/'out',gate)
            self.assertFalse((base/'out').exists())

    def test_cli_blocks_repository_output_and_allows_explicit_external_registry(self):
        module='research.evaluation.trend_sr_comparison.runner'
        inside=Path(runner.__file__).parent/'must-not-be-created.json'
        response=subprocess.run([sys.executable,'-B','-m',module,'registry','--output',str(inside)],capture_output=True,text=True)
        self.assertNotEqual(response.returncode,0)
        self.assertIn('outside_the_git_repository',response.stderr)
        self.assertFalse(inside.exists())
        with tempfile.TemporaryDirectory() as folder:
            external=Path(folder)/'registry.json'
            response=subprocess.run([sys.executable,'-B','-m',module,'registry','--output',str(external)],capture_output=True,text=True)
            self.assertEqual(response.returncode,0,response.stderr)
            self.assertEqual(json.loads(external.read_text())['nominal_core_cells'],3360)

    def test_selection_separates_error_denominators_and_exact_dedup_requires_all_seeds(self):
        from research.evaluation.trend_sr_comparison import recovery
        rows=[]
        for cell,method,suite,recall,signature in [('main','R01','selection',.6,'same'),('equivalent','R02','selection',.6,'same'),
                ('dominated','R03','selection',.4,'different'),('error13','R13','error',1.,'e13'),('error14','R14','error',1.,'e14'),
                ('incomplete','R04','selection',1.,'incomplete')]:
            for seed in runner.dataset.PROTOCOL['development_seeds']:
                if cell=='incomplete' and seed==runner.dataset.PROTOCOL['development_seeds'][-1]: continue
                rows.append(dict(cell_id=cell,method=method,suite=suite,seed=seed,view='replacement_package',calibration_passed=True,
                    family='error' if suite=='error' else 'count',cadence=5,window=60,configuration_window_minutes=60,
                    parameter_kind='finite_window',aggregation=1,baseline='B1',prediction_fingerprint=signature+str(seed),
                    independent_all_branch_availability=1.,macro_family_recall=recall,macro_subtype_recall=recall,
                    mean_loss360_minutes=10.,positive_time_fraction=.001,incremental_shared_score_cpu_seconds=1.))
        with tempfile.TemporaryDirectory() as folder, patch.object(runner,'file_hash',return_value='hash'), \
                patch.object(runner,'load_pickle',return_value={}), patch.object(runner,'load_selected_candidate',return_value={}), \
                patch.object(runner.dataset,'generate',return_value=None), patch.object(recovery,'normal_guard',return_value={'passed':True}):
            lock=runner.seal_selection(folder,rows)
            self.assertEqual([r['cell_id'] for r in lock['selected']],['main'])
            self.assertEqual({r['method'] for r in lock['error_selected']},{'R13','R14'})
            self.assertTrue(all(r['suite']=='error' for r in lock['error_selected']))
            self.assertFalse(lock['final_data_generated'])
            ranking=json.loads((Path(folder)/'selection-ranking.json').read_text())
            duplicate=next(r for r in ranking if r['cell_id']=='equivalent')
            self.assertEqual(duplicate['equivalent_prediction_representative'],'main')


if __name__ == '__main__': unittest.main()
