"""Independent service contract and train-only fitted-feature regression checks."""
import copy
import json
import tempfile
import unittest
import sys
from pathlib import Path

import joblib
import jsonschema
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import model_adapters
from feature_fit import definitions_hash, fit_features, transform, validate_state
from feature_runtime import validate_definitions
from trainer.service_contract import evaluation_envelope


def definitions():
    field = {'op': 'field', 'field': 'P.total_count'}
    return [{'name': 'count_raw', 'expression': field},
            {'name': 'count_median', 'expression': {'op': 'group_median', 'groupBy': 'P.adapter_type', 'value': field}},
            {'name': 'count_centered', 'expression': {'op': 'group_centered', 'groupBy': 'P.adapter_type', 'value': field}}]


class TrainerServiceContractTests(unittest.TestCase):
    def test_group_fit_excludes_explicit_anomaly_preserves_unknown_and_missing(self):
        defs = definitions()
        rows = [{'raw': {'adapter_type': 'A', 'total_count': value}, 'label': label}
                for value, label in [(10, 'normal'), (20, None), (10000, 'anomaly')]]
        state = fit_features(defs, rows, snapshot_id='fixture-snapshot')
        self.assertEqual(state['features']['count_median']['groups'], {'A': '15'})
        self.assertEqual(state['features']['count_median']['referenceCounts'], {'A': 2})
        before = copy.deepcopy(state)
        self.assertEqual(transform(defs, {'adapter_type': 'A', 'total_count': 1000}, state)[0]['count_centered'], '985')
        self.assertEqual(state, before)
        for group in ['NEW', '', None]:
            values, missing = transform(defs, {'adapter_type': group, 'total_count': 50}, state)
            self.assertEqual(values['count_raw'], '50')
            self.assertEqual(missing, ['count_median', 'count_centered'])
        values, missing = transform(defs, {'adapter_type': 'A', 'total_count': None}, state)
        self.assertEqual(values['count_median'], '15')
        self.assertEqual(missing, ['count_raw', 'count_centered'])

    def test_fit_identity_and_unsupported_nested_group_reject(self):
        defs = definitions()
        state = fit_features(defs, [{'raw': {'adapter_type': 'A', 'total_count': 1}}], snapshot_id='fixture')
        with self.assertRaisesRegex(ValueError, 'preprocessing_definition_mismatch'):
            validate_state(list(reversed(defs)), state)
        bad = [{'name': 'bad_value', 'expression': {'op': 'add', 'left': defs[1]['expression'], 'right': defs[0]['expression']}}]
        with self.assertRaises(ValueError):
            validate_definitions(bad)
        with self.assertRaisesRegex(ValueError, 'fitted_feature_reference_empty'):
            fit_features(defs, [{'raw': {'total_count': 1}}], snapshot_id='fixture')

    def test_saved_model_and_fit_share_transform_identity_and_detect_tamper(self):
        defs = definitions()
        raw_rows = [{'raw': {'adapter_type': 'A' if i % 2 else 'B', 'total_count': 10 + i % 19}} for i in range(700)]
        state = fit_features(defs, raw_rows, snapshot_id='fixture')
        prepared = []
        for row in raw_rows:
            values, missing = transform(defs, row['raw'], state)
            prepared.append({'features': values, 'missing': missing})
        names = [d['name'] for d in defs]
        with tempfile.TemporaryDirectory() as directory:
            result = model_adapters.train_iforest(prepared, names, directory, preprocessing=state)
            reference = {**result, 'featureNames': names, 'definitionsHash': definitions_hash(defs)}
            raw = {'adapter_type': 'A', 'total_count': 123}
            expected = transform(defs, raw, state)
            self.assertEqual(model_adapters.transform_iforest_features(reference, defs, raw), expected)
            self.assertEqual(model_adapters.score_iforest(reference, expected[0]), model_adapters.score_iforest(reference, expected[0]))
            with self.assertRaisesRegex(ValueError, 'preprocessing_definition_mismatch'):
                model_adapters.transform_iforest_features({**reference, 'definitionsHash': '0' * 64}, defs, raw)
            changed = joblib.load(result['fitPath'])
            changed['preprocessing']['features']['count_median']['groups']['A'] = '999'
            joblib.dump(changed, result['fitPath'])
            model_adapters._load_pair.cache_clear()
            model_adapters._preprocessing.cache_clear()
            with self.assertRaisesRegex(ValueError, 'preprocessing_hash_mismatch'):
                model_adapters.prepare_iforest(reference)

    def test_envelope_null_and_denominator_semantics(self):
        schema = json.loads((Path(__file__).resolve().parents[2] / 'contracts/trainer-evaluation-envelope.schema.json').read_text())
        envelope = evaluation_envelope()
        jsonschema.validate(envelope, schema)
        self.assertTrue(all(item['metrics'] is None and item['status'] == 'unavailable' for item in envelope.values()))
        metric = {'value': 0, 'unit': 'ratio', 'numerator': 0, 'denominator': 5, 'coverage': 1, 'reason': None}
        envelope['rule_ml'] = {'status': 'measured', 'reason': None, 'metrics': {'overlap': metric}, 'metricVersion': 'fixture-v1'}
        jsonschema.validate(envelope, schema)
        metric['denominator'] = 0
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(envelope, schema)
        metric.update(value=None, reason='no_eligible_rows', coverage=None)
        jsonschema.validate(envelope, schema)
        del metric['numerator']
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(envelope, schema)


if __name__ == '__main__':
    unittest.main()
