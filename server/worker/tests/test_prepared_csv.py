"""Small controlled prepared matrices; no customer data, database or network."""
import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer.prepared_contract import canonical, digest, validate_manifest
from trainer.prepared_parser import parse_csv, training_eligible
from trainer.prepared_storage import read, store


def fixture(number=4):
    manifest = {'contractVersion': 'prepared-csv-v1', 'sourceId': str(uuid.uuid4()), 'sourceSchemaVersion': 'controlled-v1',
                'domain': 'controlled', 'rowCount': number, 'inputStage': 'model_input',
                'features': [{'name': 'x', 'type': 'number', 'unit': 'ratio'}, {'name': 'y', 'type': 'number', 'unit': 'ratio'}],
                'preprocessing': {'definitionHash': 'a'*64, 'scaling': 'applied', 'fitHash': 'b'*64, 'fitSplit': 'train',
                                  'trainRowIdsHash': digest(sorted(str(index) for index in range(number-1)))},
                'split': {'version': 'provided-v1', 'timeBasis': 'unspecified'},
                'columns': {'rowId': 'row_id', 'split': 'split', 'label': 'label', 'status': 'status', 'isSynthetic': 'synthetic',
                            'originRowId': 'origin', 'groupId': 'group_id', 'scenario': 'scenario'},
                'labelSource': {'kind': 'manual', 'reference': 'controlled_fixture'}}
    rows = [[str(index), 'train' if index<number-1 else 'test', 'unknown', 'S', 'false',
             'origin-'+str(index), 'group-'+str(index), '', index/10, -index/5] for index in range(number)]
    return manifest, rows


def content(manifest, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow([name for name in manifest['columns'].values() if name is not None] + [item['name'] for item in manifest['features']])
    writer.writerows(rows)
    return stream.getvalue().encode()


class PreparedCsvTests(unittest.TestCase):
    def test_prepared_values_are_unmodified_and_metadata_does_not_enter_features(self):
        manifest, rows = fixture()
        parsed = parse_csv(content(manifest, rows), manifest)
        self.assertEqual(parsed['rows'][1]['features'], [.1, -.2])
        self.assertEqual(parsed['rows'][1]['label'], 'unknown')
        self.assertNotIn('features', parsed['rowMetadata'][1])
        self.assertEqual(parsed['normalizedHash'], parse_csv(content(manifest, rows), manifest)['normalizedHash'])
        self.assertTrue(training_eligible(parsed['rows'][1]))
        self.assertEqual(parsed['validationResult']['trainingEligibleCount'], 3)
        self.assertFalse(parsed['validationResult']['operationalEligible'])

    def test_unknown_information_registers_without_inventing_split_status_or_synthetic(self):
        manifest, _ = fixture(1)
        manifest['columns'] = {'rowId': 'row_id'}
        manifest['preprocessing'].update(fitHash=None, fitSplit='unknown', trainRowIdsHash=None)
        parsed = parse_csv(content(manifest, [['a', 1, 2]]), manifest)
        row = parsed['rows'][0]
        self.assertIsNone(row['status']); self.assertIsNone(row['split']); self.assertIsNone(row['isSynthetic'])
        self.assertEqual(row['label'], 'unknown')
        self.assertFalse(training_eligible(row))
        self.assertEqual(parsed['validationResult']['lineageStatus'], 'unverified')

    def test_synthetic_anomaly_and_scenario_rows_never_train(self):
        manifest, rows = fixture()
        rows[0][2] = 'anomaly'; rows[1][4] = 'true'; rows[2][7] = 'injected'
        parsed = parse_csv(content(manifest, rows), manifest)
        self.assertEqual(parsed['validationResult']['trainingEligibleCount'], 0)
        self.assertEqual([row['label'] for row in parsed['rows']], ['anomaly', 'unknown', 'unknown', 'unknown'])
        self.assertEqual(parsed['rows'][-1]['split'], 'test')

    def test_duplicate_identity_split_leakage_fit_membership_and_row_loss_fail(self):
        manifest, rows = fixture()
        for mutation, reason in [(lambda values: values[1].__setitem__(0, values[0][0]), 'duplicate'),
                                 (lambda values: values[-1].__setitem__(5, values[0][5]), 'lineage_split_overlap'),
                                 (lambda values: values[0].__setitem__(1, 'test'), 'fit_membership_mismatch'),
                                 (lambda values: values.pop(), 'row_count_mismatch')]:
            changed = copy.deepcopy(rows); mutation(changed)
            with self.assertRaisesRegex(ValueError, reason): parse_csv(content(manifest, changed), manifest)

    def test_feature_order_nonfinite_missing_extra_columns_are_rejected(self):
        manifest, rows = fixture()
        for value in ('NaN', 'Infinity', '', ' true', 'abc'):
            changed = copy.deepcopy(rows); changed[0][-1] = value
            with self.assertRaises(ValueError): parse_csv(content(manifest, changed), manifest)
        data = content(manifest, rows).replace(b'x,y', b'y,x', 1)
        with self.assertRaisesRegex(ValueError, 'order'): parse_csv(data, manifest)
        with self.assertRaisesRegex(ValueError, 'header'): parse_csv(content(manifest, rows).replace(b'x,y', b'x,y,extra', 1), manifest)

    def test_evaluation_only_csv_preserves_external_fit_lineage_as_unverified(self):
        manifest, rows = fixture()
        for row in rows: row[1] = 'test'
        parsed = parse_csv(content(manifest, rows), manifest)
        self.assertIn('external_fit_membership_unverified', parsed['validationResult']['reasonCodes'])
        self.assertTrue(parsed['validationResult']['evaluationPossible'])

    def test_case_preserved_feature_names_and_optional_null_mapping(self):
        manifest, _ = fixture(1)
        manifest['features'][0]['name'] = 'startTimeHourSin'
        manifest['features'][1]['name'] = '_Adapter1'
        manifest['columns'] = {'rowId': 'RowID', 'split': None}
        parsed = parse_csv(content(manifest, [['id', .25, -.5]]), manifest)
        self.assertEqual(parsed['rows'][0]['features'], [.25, -.5])
        self.assertIsNone(parsed['rows'][0]['split'])

    def test_event_time_is_strict_UTC_and_not_upload_order(self):
        manifest, rows = fixture()
        manifest['columns']['occurredAt'] = 'occurred_at'
        for index, row in enumerate(rows): row.insert(8, f'2026-01-01T00:00:0{index}Z')
        manifest['split']['timeBasis'] = 'occurred_at'
        self.assertEqual(parse_csv(content(manifest, rows), manifest)['rows'][0]['occurredAt'], '2026-01-01T00:00:00Z')
        for value in ('2026-01-01T00:00:00', '2026-01-01T09:00:00+09:00', '2026-01-01T00:00:00.000000001Z'):
            changed = copy.deepcopy(rows); changed[0][8] = value
            with self.assertRaisesRegex(ValueError, 'event_time'): parse_csv(content(manifest, changed), manifest)
        rows[-1][8] = '2025-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'time_split_overlap'): parse_csv(content(manifest, rows), manifest)

    def test_storage_hash_reuse_tampering_and_path_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            identifier = uuid.uuid4(); (Path(temporary)/'prepared'/str(identifier)).mkdir(parents=True)
            reference = store(temporary, identifier, b'{"features":[1]}\n')
            self.assertEqual(reference, store(temporary, identifier, b'{"features":[1]}\n'))
            self.assertEqual(read(temporary, reference['relativePath'], reference['sha256']), b'{"features":[1]}\n')
            (Path(temporary)/reference['relativePath']).write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'hash_mismatch'): read(temporary, reference['relativePath'], reference['sha256'])
            with self.assertRaises(ValueError): read(temporary, '../outside', 'a'*64)

    def test_local_origin_references_and_transitive_groups_cannot_cross_splits(self):
        manifest, rows = fixture()
        rows[-1][5] = rows[0][0]
        with self.assertRaisesRegex(ValueError, 'lineage_split_overlap'): parse_csv(content(manifest, rows), manifest)
        rows[-1][5] = 'origin-last'
        rows[1][1] = ''
        rows[0][5] = rows[1][0]
        rows[-1][6] = rows[1][6]
        manifest['preprocessing']['trainRowIdsHash'] = None
        with self.assertRaisesRegex(ValueError, 'lineage_split_overlap'): parse_csv(content(manifest, rows), manifest)

    def test_parse_deadline_includes_final_normalization(self):
        manifest, rows = fixture(1)
        with patch('trainer.prepared_parser.time.monotonic', side_effect=[0,1,21]):
            with self.assertRaisesRegex(ValueError,'parse_time_exceeded'): parse_csv(content(manifest,rows),manifest)

    def test_training_possibility_matches_500_fit_plus_twenty_percent_calibration(self):
        for eligible,possible in ((624,False),(625,True)):
            manifest,rows = fixture(eligible+1)
            result = parse_csv(content(manifest,rows),manifest)['validationResult']
            self.assertEqual(result['trainingEligibleCount'],eligible)
            self.assertEqual(result['trainingPossible'],possible)
            self.assertEqual(result['trainingMinimumEligibleRows'],625)


if __name__ == '__main__': unittest.main()
