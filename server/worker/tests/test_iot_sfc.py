"""IoT 투영·정답 분리·시각 분할·판정 불가를 독립 입력으로 확인한다."""
import csv
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iot_sfc.adapter import InputError, NUMERIC, features, iter_rows, project
from iot_sfc.pipeline import decide, metrics, split_rows
from iot_sfc.sample import make_sample


def raw(**overrides):
    return {'ts': '1744000000.123456789', 'uid': 'public-test-uid', 'log_type': 'conn',
            'proto': 'tcp', 'service': 'http', 'conn_state': 'SF',
            'duration': '0.5', 'orig_bytes': '100', 'resp_bytes': '200',
            'orig_pkts': '2', 'resp_pkts': '3', 'attack': '0', 'type_of_attack': 'normal', **overrides}


class AdapterTests(unittest.TestCase):
    def test_projection_excludes_payload_credentials_addresses_and_free_text(self):
        row = project(raw(password='SECRET_SENTINEL', mqtt_password='SECRET_SENTINEL',
                          payload='SECRET_SENTINEL', msg='SECRET_SENTINEL', orig_h='SECRET_SENTINEL'), 1)
        self.assertNotIn('SECRET_SENTINEL', str(row))
        self.assertNotIn('attack', row['allowed'])
        self.assertNotIn('type_of_attack', row['allowed'])
        self.assertEqual(row['allowed']['ts'], '1744000000.123456789')
        self.assertTrue(row['source_time'].endswith('+00:00'))

    def test_changing_labels_cannot_change_features(self):
        clean = project(raw(), 1)
        attack = project(raw(attack='1', type_of_attack='injected-test'), 1)
        self.assertEqual(clean['allowed'], attack['allowed'])
        self.assertEqual(features(clean['allowed']), features(attack['allowed']))
        self.assertNotEqual(clean['truth'], attack['truth'])

    def test_missing_invalid_and_fractional_counts_are_not_zero_filled(self):
        for value in ('', '-', 'NaN', 'Infinity', '-1', '1e9999'):
            with self.subTest(value=value):
                self.assertIsNone(features(project(raw(orig_bytes=value), 1)['allowed']))
        self.assertIsNone(features(project(raw(orig_pkts='1.5'), 1)['allowed']))
        self.assertIsNone(features(project(raw(orig_pkts='1.0000000000000000001'), 1)['allowed']))
        self.assertIsNotNone(features(project(raw(orig_pkts='0'), 1)['allowed']))

    def test_unknown_clock_is_not_guessed(self):
        for value in ('2025-04-07 10:00:00', '', 'NaN'):
            with self.assertRaises(InputError):
                project(raw(ts=value), 1)

    def test_csv_filters_log_type_without_fabricating_esb_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'input.csv'
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(raw()))
                writer.writeheader()
                writer.writerows([raw(log_type='dns'), raw()])
            rows = list(iter_rows(path))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['row_number'], 2)
            self.assertNotIn('transaction_id', rows[0]['allowed'])

    def test_same_uid_cannot_cross_time_splits(self):
        rows = [project(raw(ts=str(100 + i), uid=str(i)), i + 1) for i in range(10)]
        rows[0]['allowed']['uid'] = rows[-1]['allowed']['uid'] = 'crossing'
        assigned, _ = split_rows(rows)
        self.assertEqual(assigned[0]['split'], 'excluded')
        self.assertEqual(assigned[-1]['split'], 'excluded')
        roles = {}
        for row in assigned:
            if row['split'] != 'excluded':
                roles.setdefault(row['allowed']['uid'], set()).add(row['split'])
        self.assertTrue(all(len(value) == 1 for value in roles.values()))

    def test_timestamp_precision_does_not_use_lexical_iso_order(self):
        rows = [project(raw(ts=value, uid=str(i)), i + 1)
                for i, value in enumerate(('100.9', '100.01', '100', '100.2', '100.001'))]
        assigned, _ = split_rows(rows)
        self.assertEqual({row['allowed']['ts'] for row in assigned if row['split'] == 'train'},
                         {'100', '100.001', '100.01'})


class SampleTests(unittest.TestCase):
    def test_sample_writes_only_projected_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = io.StringIO(newline='')
            writer = csv.DictWriter(source, fieldnames=list(raw()) + ['password', 'payload'])
            writer.writeheader()
            writer.writerow({**raw(), 'password': 'PRIVATE_SENTINEL', 'payload': 'PRIVATE_SENTINEL'})
            prefix = folder / 'prefix.zip'
            with zipfile.ZipFile(prefix, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('iot_dataset_complete.csv', source.getvalue())
            target = folder / 'sample'
            make_sample(target, prefix=prefix, limit=1)
            text = (target / 'sample.csv').read_text()
            self.assertNotIn('PRIVATE_SENTINEL', text)
            self.assertNotIn('password', text)
            self.assertNotIn('payload', text)
            self.assertEqual(len(list(iter_rows(target / 'sample.csv'))), 1)
            with self.assertRaises(InputError):
                make_sample(target, prefix=prefix, limit=1)

    def test_server_ignoring_range_is_rejected_before_body_read(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen') as get:
            response = get.return_value.__enter__.return_value
            response.status = 200
            with self.assertRaises(InputError):
                make_sample(Path(folder) / 'new')
            response.read.assert_not_called()


class DecisionTests(unittest.TestCase):
    def test_unavailable_is_not_normal(self):
        decision = decide({}, project(raw(duration='-'), 1)['allowed'])
        self.assertEqual(decision['status'], 'unavailable')
        self.assertIsNone(decision['anomaly'])
        self.assertIsNone(decision['anomaly_score'])

    def test_metrics_keep_unavailable_and_unknown_labels_in_denominator(self):
        result = metrics([
            ({'status': 'complete', 'anomaly': True}, {'attack': True}),
            ({'status': 'unavailable', 'anomaly': None}, {'attack': False}),
            ({'status': 'complete', 'anomaly': False}, {'attack': None}),
        ])
        self.assertEqual(result['test_rows'], 3)
        self.assertEqual(result['unavailable'], 1)
        self.assertEqual(result['unknown_label'], 1)
        self.assertEqual(result['tn'], 0)
        self.assertEqual(result['coverage'], 2 / 3)


if __name__ == '__main__':
    unittest.main()
