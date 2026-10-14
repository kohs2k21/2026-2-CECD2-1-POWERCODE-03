from pathlib import Path
import copy
import json
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'implementation'))
import package_runtime as runtime


class PublicationTests(unittest.TestCase):
    def test_pinned_assets_and_two_adapters(self):
        manifest = runtime.verify_package(weights=True)
        self.assertEqual(set(manifest['adapters']), {'feature_v5', 'rule_v2'})
        self.assertFalse(manifest['raw_records_included'])

    def test_all_twelve_saved_rule_decisions(self):
        for path in sorted((runtime.EVALUATION / 'rule_outputs').glob('*.json')):
            with self.subTest(path=path.name):
                result = runtime.check_rule(path.stem, path)
                self.assertTrue(result['code_validation']['passed'], result)

    def test_missing_measurement_evidence_rejected(self):
        path = next(p for p in (runtime.EVALUATION / 'rule_outputs').glob('*numeric_reference_comparison.json')
                    if runtime.read(p)['prediction']['state'] == 'proposed')
        value = copy.deepcopy(runtime.read(path)['prediction'])
        value['evidence'] = [value['condition']['baseline']]
        self.assertFalse(runtime.check_rule(path.stem, value)['code_validation']['passed'])

    def test_definition_sha_change_rejected(self):
        path = next((runtime.EVALUATION / 'rule_outputs').glob('*.json'))
        value = copy.deepcopy(runtime.read(path)['prediction'])
        value['sha'] = '0' * 64
        self.assertFalse(runtime.check_rule(path.stem, value)['code_validation']['passed'])

    def test_boolean_integer_substitution_rejected(self):
        path = next(p for p in (runtime.EVALUATION / 'rule_outputs').glob('*observational_diagnostic.json')
                    if runtime.read(p)['prediction']['condition']['op'] == 'eq')
        value = copy.deepcopy(runtime.read(path)['prediction'])
        value['condition']['value'] = 1
        self.assertFalse(runtime.check_rule(path.stem, value)['code_validation']['passed'])

    def test_all_twenty_replays_are_cpu_only_archives(self):
        for path in sorted((runtime.EVALUATION / 'feature_inputs').glob('*.json')):
            with self.subTest(case=path.stem):
                result = runtime.replay(path.stem)
                self.assertEqual(result['GPU_calls'], 0)
                self.assertFalse(result['archived_measurement_recalculated'])

    def test_metrics_keep_zeros_separate_from_measurements(self):
        comparison = runtime.read(runtime.EVALUATION / 'comparison_r02.json')['results']
        self.assertEqual(comparison['v5']['full_pass'], 14)
        self.assertEqual(comparison['v5']['measured_candidates'], 17)
        self.assertEqual(comparison['v5']['by_group']['discovery']['full_pass'], 6)

    def test_case_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            runtime.load_case('feature', '../private')

    def test_manifest_does_not_claim_if_execution(self):
        manifest = runtime.read(PACKAGE / 'model_manifest.json')
        self.assertFalse(manifest['anomaly_execution'])
        self.assertFalse(manifest['server_connected'])

    def test_cpu_import_does_not_load_model_frameworks(self):
        self.assertNotIn('torch', sys.modules)
        self.assertNotIn('transformers', sys.modules)
        self.assertNotIn('sklearn', sys.modules)


if __name__ == '__main__':
    unittest.main()
