"""CPU-only tests of relocated artifacts, measured evidence and recorded outputs."""
from pathlib import Path
import copy
import hashlib
import json
import re
import struct
import subprocess
import sys
import unittest

MODEL = Path(__file__).resolve().parents[1]
REPO = MODEL.parents[2]
EVAL = REPO / 'research/evaluation/rule_generator_v0_1'
sys.path.insert(0, str(MODEL / 'implementation'))
from demo_runtime import assess, bind_input, package_data, rows, source_gate, system_text
from pilot_io import read, sha


class PublishedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.features, cls.inputs, cls.manifest = package_data()
        cls.inputs_by_id = {row['id']: row for row in cls.inputs}
        cls.results = read(EVAL / 'demo_results.json')
        cls.results_by_id = {row['id']: row for row in cls.results}
        cls.outputs = rows(EVAL / 'model_outputs.jsonl')

    def test_cpu_import_does_not_load_model_frameworks(self):
        self.assertNotIn('torch', sys.modules)
        self.assertNotIn('transformers', sys.modules)
        self.assertNotIn('peft', sys.modules)

    def test_frozen_assets_and_source_context(self):
        for name, expected in self.manifest['pinned_package_assets'].items():
            self.assertEqual(sha(REPO / name), expected, name)
        checked = source_gate(self.manifest)
        self.assertTrue(checked['passed'])
        self.assertFalse(checked['raw_source_revalidation'])

    def test_feature_scope_is_reported_accurately(self):
        self.assertEqual(len(self.features), 64)
        self.assertEqual(sum(f['origin'] == 'model_discovered_reused' for f in self.features.values()), 13)
        self.assertEqual(sum(f['measurement']['summary']['variation'] == 'constant' for f in self.features.values()), 39)
        self.assertEqual(sum(f['measurement']['summary']['valid_count'] > 0 for f in self.features.values()), 63)
        self.assertEqual(len({row['input']['feature']['id'] for row in self.inputs}), 23)

    def test_final_counts_partition_the_32_cases(self):
        approved = read(EVAL / 'validated_rules.json')
        pending = read(EVAL / 'pending_decisions.json')
        failed = rows(EVAL / 'failed_outputs.jsonl')
        combined = approved + pending + failed
        self.assertEqual(len(approved), 20)
        self.assertEqual(sum(row['decision']['role'] == 'HR' for row in approved), 5)
        self.assertEqual(sum(row['decision']['role'] == 'SR' for row in approved), 15)
        self.assertEqual(len(pending), 11)
        self.assertEqual(len(failed), 1)
        self.assertEqual({row['id'] for row in combined}, set(self.inputs_by_id))
        self.assertEqual(read(EVAL / 'reviewed_manual_examples.json'), [])
        self.assertEqual(read(EVAL / 'provisional_decisions.json'), [])

    def test_recorded_successes_still_pass_unchanged_validators(self):
        for row in read(EVAL / 'validated_rules.json') + read(EVAL / 'pending_decisions.json'):
            feature = self.features[row['input']['feature']['id']]
            self.assertTrue(assess(row['input'], row['decision'], feature)['passed'], row['id'])
            self.assertTrue(row['human_review']['passed'], row['id'])

    def test_raw_outputs_and_reviews_are_bound_without_rewriting(self):
        recorded = {(row['id'], row['stage']): row for row in self.outputs}
        self.assertEqual(len(recorded), 36)
        for review in read(EVAL / 'human_reviews.json'):
            self.assertEqual(review['raw_sha256'], recorded[(review['id'], review['stage'])]['raw_sha256'])
        for result in self.results:
            raw = recorded[(result['id'], result['stage'])]['raw_output']
            self.assertEqual(result['raw_output'], raw)
            self.assertEqual(result['decision'], json.loads(raw))
            self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), result['human_review']['raw_sha256'])
            self.assertFalse(result['output_semantically_rewritten'])
            self.assertEqual(result['origin'], 'model_generated')

    def test_meaning_failure_is_preserved_even_if_code_passes(self):
        row = self.results_by_id['real_18_f036']
        self.assertEqual(row['status'], 'failed')
        self.assertTrue(row['code_validation']['passed'])
        self.assertFalse(row['human_review']['passed'])
        self.assertEqual(row['input']['measurement']['valid_count'], 1)
        self.assertEqual(row['input']['feature']['dtype'], 'number')

    def test_changed_measurement_is_rejected(self):
        value = copy.deepcopy(self.inputs_by_id['real_01_f004'])
        value['input']['measurement']['valid_count'] = 0
        with self.assertRaises(ValueError):
            bind_input(value, self.features, self.inputs)

    def test_changed_definition_or_case_id_is_rejected(self):
        for field, value in [('sha', '0' * 64), ('dtype', 'number')]:
            row = copy.deepcopy(self.inputs_by_id['real_01_f004'])
            row['input']['feature'][field] = value
            with self.assertRaises(ValueError):
                bind_input(row, self.features, self.inputs)
        row = copy.deepcopy(self.inputs_by_id['real_01_f004'])
        row['id'] = '../outside'
        with self.assertRaises(ValueError):
            bind_input(row, self.features, self.inputs)

    def test_unprovided_baseline_is_rejected(self):
        row = self.results_by_id['real_23_f015']
        bad = copy.deepcopy(row['decision'])
        bad['condition']['baseline'] = 'invented_baseline'
        self.assertFalse(assess(row['input'], bad, self.features['f015'])['passed'])

    def test_unknown_observations_cannot_be_proposed(self):
        row = self.results_by_id['real_27_f060']
        self.assertEqual(row['decision']['state'], 'deferred')
        bad = copy.deepcopy(row['decision'])
        bad['state'] = 'proposed'
        bad['needed'] = []
        self.assertFalse(assess(row['input'], bad, self.features['f060'])['passed'])

    def test_false_boolean_condition_on_numeric_feature_is_rejected(self):
        row = self.results_by_id['real_27_f060']
        bad = copy.deepcopy(row['decision'])
        bad['condition'] = {'op': 'eq', 'value': True}
        self.assertFalse(assess(row['input'], bad, self.features['f060'])['passed'])

    def test_adapter_is_an_actual_pinned_safetensors_file(self):
        pin = self.manifest['adapter']['weights']
        path = REPO / pin['path']
        self.assertEqual(path.stat().st_size, 95136344)
        self.assertEqual(sha(path), pin['sha256'])
        with path.open('rb') as stream:
            length = struct.unpack('<Q', stream.read(8))[0]
            self.assertLess(length, 1024 * 1024)
            header = json.loads(stream.read(length))
        tensors = {k: v for k, v in header.items() if k != '__metadata__'}
        self.assertEqual(len(tensors), 608)
        self.assertTrue(all('lora_' in name for name in tensors))
        config = read(REPO / self.manifest['adapter']['config']['path'])
        self.assertEqual(config['base_model_name_or_path'], self.manifest['base']['model_name'])

    def test_published_inputs_have_no_answer_or_local_raw_path(self):
        text = (EVAL / 'example_inputs.jsonl').read_text()
        self.assertNotIn('/home/', text)
        self.assertNotIn('.csv', text)
        self.assertNotIn('"prediction"', text)
        self.assertNotIn('"answer"', text)
        self.assertNotIn('"expected_output"', text)

    def test_training_system_and_public_contract_are_present(self):
        system = system_text()
        self.assertTrue(system.startswith((MODEL / 'v2_system.txt').read_text().strip()))
        self.assertIn('Full public JSON Schema:', system)
        self.assertIn('ddl_not_null', system)
        self.assertIn('request_evidence', system)

    def test_cpu_cli_works_from_a_different_working_directory(self):
        run = subprocess.run([sys.executable, '-B', str(MODEL / 'demo_cli.py'), 'inspect'], cwd=MODEL.parent, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('"validated_model_HR": 5', run.stdout)
        self.assertIn('"validated_model_SR": 15', run.stdout)

    def test_static_html_binds_all_artifacts_and_uses_local_links(self):
        text = (EVAL / 'demo.html').read_text()
        match = re.search(r'<script id="demoData" type="application/json">(.*?)</script>', text, re.S)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertEqual(len(data['features']), 64)
        self.assertEqual(len(data['results']), 32)
        self.assertEqual(len(data['outputs']), 36)
        self.assertNotIn('/home/', data['command'])
        for target in re.findall(r'href="([^"]+)"', text):
            self.assertTrue((EVAL / target).is_file(), target)
        self.assertNotIn('fetch(', text)


if __name__ == '__main__':
    unittest.main()
