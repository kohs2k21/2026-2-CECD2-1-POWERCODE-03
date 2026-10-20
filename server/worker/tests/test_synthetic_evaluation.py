"""Controlled method fixtures; no customer rows, network, database or model training."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer.synthetic_profiles import PROFILES, binding, validate_binding, validate_request
from trainer.synthetic_generation import generate, fit_generator
from trainer.synthetic_metrics import score


def fixture(number=6, split='test'):
    rows = []
    for index in range(number):
        parent = {'rowId': 'parent-' + str(index), 'raw': {
            'transaction_id': 'tx-' + str(index), 'process_hub_id': 'hub', 'retry_count': 0,
            'interface_id': 'interface', 'start_time_raw': '2026-01-01T00:00:00+09:00',
            'end_time_raw': '2026-01-01T00:01:00+09:00'}}
        raw = {**{key: parent['raw'][key] for key in ('transaction_id', 'process_hub_id', 'retry_count')},
               'process_id': 'process', 'depend_process_id': None, 'adapter_type': 'A', 'channel_id': 'channel',
               'status': 'S', 'response_code': '000', 'total_count': 10, 'success_count': 10, 'error_count': 0,
               'start_time_raw': f'2026-01-01T00:00:{index:02d}+09:00',
               'end_time_raw': f'2026-01-01T00:00:{index:02d}.100+09:00'}
        rows.append({'rowId': str(index), 'split': split, 'raw': raw, 'parent': parent})
    return rows


def request():
    return {'profileId': 'esb-process-research-v1', 'profileVersion': '1',
            'scenarios': [{'scenario': 'P04', 'severity': 1}], 'seed': 11, 'targetRows': 3, 'repetitions': 1}


class SyntheticEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.rows = fixture()
        self.fit = fit_generator(fixture(split='train'))
        self.profile = copy.deepcopy(PROFILES['esb-process-research-v1'])

    def generation(self, **kwargs):
        return generate(self.rows, self.fit, self.profile, scenario=kwargs.pop('scenario', 'P04'),
                        severity=1, seed=11, target_rows=3, **kwargs)

    def test_p04_reproducible_total_only_and_original_parent_unchanged(self):
        original = copy.deepcopy(self.rows)
        result = self.generation()
        self.assertEqual(result, self.generation())
        self.assertEqual(self.rows, original)
        self.assertEqual(result['generatedCount'], 3)
        self.assertEqual({item['field'] for item in result['changes']}, {'total_count'})
        self.assertEqual(result['parentChanges'], [])
        for before, after in zip(original, result['rows']):
            self.assertEqual(before['parent'], after['parent'])
            for key in before['raw']:
                if key != 'total_count': self.assertEqual(before['raw'][key], after['raw'][key])

    def test_p01_remains_explicitly_unsupported(self):
        result = self.generation(scenario='P01')
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(result['reason'], 'failure_semantics_unconfirmed')
        self.assertEqual(result['rows'], self.rows)

    def test_p02_changes_raw_end_and_preserves_counts_and_identity(self):
        result = self.generation(scenario='P02')
        self.assertEqual(result['generatedCount'], 3)
        self.assertEqual({item['field'] for item in result['changes']}, {'end_time_raw'})
        self.assertEqual([item['rowId'] for item in result['rows']], [item['rowId'] for item in self.rows])

    def test_p03_rejects_entire_block_when_one_end_cannot_move(self):
        self.rows = self.rows[:3]
        self.rows[1]['parent']['raw']['end_time_raw'] = self.rows[1]['raw']['end_time_raw']
        result = self.generation(scenario='P03')
        self.assertEqual(result['generatedCount'], 0)
        self.assertEqual(result['rows'], self.rows)
        # 블록 단위 거부는 선택된 주입 시도의 거부이므로 rejectedCount에 들어가고, 사전 부적격은 없다.
        self.assertEqual((result['selectedCount'], result['rejectedCount'], result['ineligibleCount']), (3, 3, 0))

    def test_rejected_count_excludes_cohort_ineligible_rows(self):
        # 코호트 사전 부적격(미학습 어댑터·비정상 status)은 주입 시도가 아니므로 rejectedCount가 아니다.
        self.rows[4]['raw']['adapter_type'] = 'unseen'
        self.rows[5]['raw']['status'] = 'F'
        result = self.generation()
        self.assertEqual(result['status'], 'generated')
        self.assertEqual((result['generatedCount'], result['selectedCount'], result['rejectedCount'], result['ineligibleCount']),
                         (3, 3, 0, 2))
        self.assertEqual({item['rowId']: item['selected'] for item in result['rejects']}, {'4': False, '5': False})
        self.assertEqual(len(result['rejects']), result['rejectedCount'] + result['ineligibleCount'])

    def test_rejected_count_counts_selected_rows_rejected_after_planning(self):
        # 의존 프로세스 시작 시각 때문에 지연을 넣을 수 없는 행은 선택된 뒤 거부된다.
        self.rows = fixture(2)
        self.rows[1]['raw'].update(self.rows[0]['raw'], process_id='child', depend_process_id='process')
        self.rows[1]['raw']['start_time_raw'] = '2026-01-01T00:00:00.110+09:00'
        self.rows[1]['raw']['end_time_raw'] = '2026-01-01T00:00:00.200+09:00'
        self.rows[1]['parent'] = copy.deepcopy(self.rows[0]['parent'])
        result = self.generation(scenario='P02')
        rejected = [item for item in result['rejects'] if item['selected']]
        self.assertEqual([item['reason'] for item in rejected], ['insufficient_parent_or_dependency_headroom'])
        self.assertEqual((result['generatedCount'], result['selectedCount'], result['rejectedCount'], result['ineligibleCount']),
                         (1, 2, 1, 0))

    def test_p03_orders_instants_with_different_offsets_and_rejects_mixed_basis(self):
        rows = fixture(3)
        for row, start, end in zip(rows, ['00:00:00Z', '01:00:01+01:00', '00:00:02Z'],
                                   ['00:00:00.100Z', '01:00:01.100+01:00', '00:00:02.100Z']):
            row['raw'].update(start_time_raw='2026-01-01T' + start, end_time_raw='2026-01-01T' + end)
            row['parent']['raw'].update(start_time_raw='2026-01-01T00:00:00Z', end_time_raw='2026-01-01T00:01:00Z')
        result = generate(rows, self.fit, self.profile, scenario='P03', severity=1, seed=1, target_rows=2)
        self.assertIn({item['rowId'] for item in result['truth']}, [{'0', '1'}, {'1', '2'}])
        rows[1]['raw'].update(start_time_raw='2026-01-01T00:00:01', end_time_raw='2026-01-01T00:00:01.100')
        rows[1]['parent']['raw'].update(start_time_raw='2026-01-01T00:00:00', end_time_raw='2026-01-01T00:01:00')
        result = generate(rows, self.fit, self.profile, scenario='P03', severity=1, seed=1, target_rows=2)
        self.assertEqual(result['generatedCount'], 0)
        self.assertTrue(all(item['reason'] == 'ambiguous_time_basis' for item in result['rejects']))

    def test_external_observed_dependency_is_checked_and_submicrosecond_times_rejected(self):
        rows = fixture(1)
        dependency = copy.deepcopy(rows[0])
        dependency['rowId'] = 'external'
        dependency['raw'].update(process_id='external', depend_process_id='process',
                                 start_time_raw='2026-01-01T00:00:00.110+09:00')
        result = generate(rows, self.fit, self.profile, scenario='P02', severity=1, seed=1,
                          target_rows=1, context_siblings=[dependency])
        self.assertEqual(result['generatedCount'], 0)
        rows[0]['raw']['end_time_raw'] = '2026-01-01T00:00:00.100000001+09:00'
        result = generate(rows, self.fit, self.profile, scenario='P04', severity=1, seed=1, target_rows=1)
        self.assertEqual(result['rejects'][0]['reason'], 'invalid_process_time')
        rows[0]['raw']['end_time_raw'] = '2026-01-01T00:00:00,100000001+09:00'
        result = generate(rows, self.fit, self.profile, scenario='P04', severity=1, seed=1, target_rows=1)
        self.assertEqual(result['rejects'][0]['reason'], 'invalid_process_time')

    def test_external_context_cannot_remove_base_dependencies_or_conflict(self):
        rows = fixture(1)
        dependency = copy.deepcopy(rows[0])
        dependency.update(rowId='external')
        dependency['raw'].update(process_id='external', depend_process_id='process',
                                  start_time_raw='2026-01-01T00:00:00.110+09:00', end_time_raw='2026-01-01T00:00:00.200+09:00')
        rows.append(dependency)
        result = generate(rows, self.fit, self.profile, scenario='P02', severity=1, seed=1, target_rows=2, context_siblings=[])
        self.assertTrue(any(item['reason'] == 'insufficient_parent_or_dependency_headroom' for item in result['rejects']))
        conflict = copy.deepcopy(rows[0]); conflict['raw']['status'] = 'F'
        with self.assertRaisesRegex(ValueError, 'context_identity_conflict'):
            generate(rows, self.fit, self.profile, scenario='P02', severity=1, seed=1, target_rows=2, context_siblings=[conflict])

    def test_unknown_category_bad_counts_missing_parent_and_status_never_filled(self):
        for alter, reason in [(lambda row: row['raw'].update(adapter_type='unseen'), 'unseen_adapter_train_reference'),
                              (lambda row: row['raw'].update(status='?'), 'not_successful_quality_reference'),
                              (lambda row: row['raw'].update(total_count=None), 'count_contract_unverifiable'),
                              (lambda row: row.pop('parent'), 'missing_or_ambiguous_parent')]:
            self.rows = fixture(1)
            alter(self.rows[0])
            original = copy.deepcopy(self.rows)
            result = self.generation()
            self.assertEqual(result['status'], 'generation_failed')
            self.assertEqual(result['rejects'][0]['reason'], reason)
            self.assertEqual(self.rows, original)

    def test_parent_and_dependency_bounds(self):
        self.rows = fixture(2)
        self.rows[1]['raw'].update(self.rows[0]['raw'], process_id='child', depend_process_id='process')
        self.rows[1]['raw']['start_time_raw'] = '2026-01-01T00:00:00.110+09:00'
        self.rows[1]['raw']['end_time_raw'] = '2026-01-01T00:00:00.200+09:00'
        self.rows[1]['parent'] = copy.deepcopy(self.rows[0]['parent'])
        result = self.generation(scenario='P02')
        self.assertTrue(any(item['reason'] == 'insufficient_parent_or_dependency_headroom' for item in result['rejects']))

    def test_fit_train_only_and_explicit_anomaly_excluded(self):
        with self.assertRaisesRegex(ValueError, 'requires_train'): fit_generator(self.rows)
        train = fixture(2, split='train')
        train[1]['raw']['end_time_raw'] = '2026-01-01T00:00:50+09:00'
        train[1]['label'] = 'anomaly'
        self.assertEqual(fit_generator(train)['adapterDurationMs'], {'A': 100})

    def test_only_p03_requires_a_known_nonblank_parent_interface(self):
        self.assertEqual(self.profile['scenarioRequiredParentFields'], {'P03': ['interface_id']})
        for interface in (None, 123, True, '', '   ', [], {}):
            self.rows = fixture(3)
            for row in self.rows: row['parent']['raw']['interface_id'] = interface
            original = copy.deepcopy(self.rows)
            result = self.generation(scenario='P03')
            self.assertEqual(result['generatedCount'], 0)
            self.assertEqual(result['status'], 'generation_failed')
            self.assertEqual({item['reason'] for item in result['rejects']}, {'missing_or_invalid_parent_interface'})
            self.assertEqual(self.rows, original)
            for scenario in ('P02', 'P04'):
                self.assertEqual(self.generation(scenario=scenario)['generatedCount'], 3)
        for row in self.rows: row['parent']['raw']['interface_id'] = 'interface'
        self.assertEqual(self.generation(scenario='P03')['generatedCount'], 3)

    def test_normalized_identity_and_categories_do_not_coerce_numbers_or_bools(self):
        for field in ('transaction_id', 'process_id', 'process_hub_id', 'adapter_type', 'channel_id', 'status'):
            for value in (99, True, False, '   '):
                self.rows = fixture(1)
                self.rows[0]['raw'][field] = value
                original = copy.deepcopy(self.rows)
                result = self.generation()
                self.assertEqual(result['generatedCount'], 0, (field, value))
                self.assertEqual(result['rejects'][0]['reason'], 'invalid_process_text_field')
                self.assertEqual(self.rows, original)
        for field, value in [('transaction_id', 99), ('process_hub_id', True), ('retry_count', False)]:
            self.rows = fixture(1)
            if field != 'retry_count': self.rows[0]['raw'][field] = str(value)
            self.rows[0]['parent']['raw'][field] = value
            result = self.generation()
            self.assertEqual(result['generatedCount'], 0)
            self.assertEqual(result['rejects'][0]['reason'], 'invalid_parent_identity')

    def test_source_registration_and_request_bounds(self):
        profile = validate_request(request())
        registered = binding(profile, 'api-transcript-v1')
        validate_binding(profile, 'api-transcript-v1', {'syntheticProfiles': {profile['profileId']: registered}}, registered)
        with self.assertRaises(ValueError): validate_binding(profile, 'csv-replay-v1', {'syntheticProfiles': {profile['profileId']: registered}})
        for key, value in [('seed', True), ('repetitions', 6), ('targetRows', 101), ('profileVersion', '2')]:
            req = request(); req[key] = value
            with self.assertRaises(ValueError): validate_request(req)

    def test_manual_score_includes_missing_predictions_and_existing_alerts(self):
        truth = [{'rowId': str(index), 'label': 1} for index in range(4)]
        before = {str(index): {'ml': flag} for index, flag in enumerate([True, False, False, None])}
        after = {'0': {'ml': True}, '1': {'ml': True}, '2': {'ml': False}}
        metrics = score(truth, before, after)
        self.assertEqual(metrics['ml']['detectionRate'], .5)
        self.assertEqual(metrics['ml']['newlyDetectedCount'], 1)
        self.assertEqual(metrics['ml']['persistentAlertCount'], 1)
        self.assertEqual(metrics['ml']['unjudgedCount'], 1)
        self.assertEqual(metrics['sr']['unjudgedCount'], 4)
        self.assertEqual(metrics['sr']['reason'], 'sr_experiment_pending')
        self.assertIsNone(metrics['ml']['precision'])
        self.assertIsNone(metrics['ml']['falsePositiveRate'])
        self.assertEqual(metrics['combined']['injectedCount'], 4)

    def test_truth_identity_and_prediction_type_rejected(self):
        with self.assertRaises(ValueError): score([{'rowId': '1', 'label': 1}], {}, {})
        with self.assertRaises(ValueError): score([{'rowId': '1', 'label': 1}], {'1': {'ml': 1}}, {})


if __name__ == '__main__': unittest.main()
