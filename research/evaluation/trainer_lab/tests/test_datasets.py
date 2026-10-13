"""Dataset quality and split checks using generated records only."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import warnings

import pandas as pd

from research.evaluation.trainer_lab import datasets


def rows(days=10, per_day=20):
    result = []
    for day in range(days):
        for n in range(per_day):
            start = pd.Timestamp('2026-01-01') + pd.Timedelta(days=day, minutes=n)
            result.append(dict(source_period='synthetic', transactionId=f't{day}-{n}',
                processId='p0', processHubId='hub', retryCount=0, status='S',
                adapterType='DB', startTime=start.isoformat(),
                endTime=(start + pd.Timedelta(milliseconds=10+n)).isoformat(),
                totalCount=n+1, successCount=n+1, errorCount=0))
    return result


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def csv(self, records):
        path = self.root / 'input.csv'
        pd.DataFrame(records).to_csv(path, index=False)
        return path

    def test_bounded_loading_preserves_all_rows_and_input_hash(self):
        path = self.csv(rows())
        frame = datasets.load_input(path)
        report = datasets.audit_dataset(frame)
        self.assertEqual(report['rows'], 200)
        self.assertFalse(report['automatic_row_removal'])
        self.assertEqual(report['input']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        for limits in ({'max_rows': 199}, {'max_bytes': 5}, {'max_rows': True}):
            with warnings.catch_warnings(), self.assertRaises(ValueError):
                warnings.simplefilter('error', ResourceWarning)
                datasets.load_input(path, **limits)

    def test_invalid_duplicate_fractional_records_retained_and_reported(self):
        source = rows()
        source.append(dict(source[0]))
        source[1]['totalCount'] = -1
        source[2]['retryCount'] = .5
        source[3]['startTime'] = 'invalid-date'
        source[4]['successCount'] = 'not-a-count'
        frame = datasets.load_input(self.csv(source))
        report = datasets.audit_dataset(frame)
        self.assertEqual(len(frame), 201)
        self.assertEqual(report['duplicate_key_rows'], 2)
        self.assertEqual(report['negative_counts']['totalCount'], 1)
        self.assertEqual(report['noninteger_counts']['retryCount'], 1)
        self.assertEqual(report['parse_invalid_counts']['startTime'], 1)
        self.assertEqual(report['parse_invalid_counts']['successCount'], 1)
        self.assertLess(report['model_eligible_rows'], 201)

    def test_ambiguous_alias_rejected(self):
        source = rows()
        source[0]['START_TIME'] = source[0]['startTime']
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            datasets.load_input(self.csv(source))

    def test_split_keeps_transaction_atomic_and_test_held_out(self):
        frame = datasets.load_input(self.csv(rows()))
        parts, report = datasets.split_and_audit(frame)
        self.assertEqual(report['transaction_overlap_count'], 0)
        self.assertTrue(all(len(part) for part in parts.values()))
        seen = set()
        for part in parts.values():
            keys = set(zip(part.source_period, part.transactionId))
            self.assertFalse(seen & keys)
            seen |= keys
        self.assertLess(parts['train'].startTime.max(), parts['test'].startTime.min())
        self.assertEqual(sum(map(len, parts.values())), len(frame))
        self.assertFalse(report['fit_execution_verified'])
        self.assertEqual(report['lineage']['status'], 'unknown')
        self.assertEqual(report['time_availability']['status'], 'unknown')

    def test_boundary_transaction_excluded_without_splitting(self):
        source = rows()
        source[-1]['transactionId'] = source[0]['transactionId']
        frame = datasets.load_input(self.csv(source))
        parts, report = datasets.split_and_audit(frame)
        self.assertEqual(report['excluded_rows']['excluded_boundary_transaction'], 2)
        self.assertFalse(any('t0-0' in set(part.transactionId) for part in parts.values()))

    def test_transitive_template_lineage_cross_split_detected(self):
        source = rows()
        for i, item in enumerate(source):
            item['source_template_id'] = f's{i}'
        source[0]['parent_template_id'] = 'parent'
        source[-1]['parent_template_id'] = 'parent'
        _, report = datasets.split_and_audit(datasets.load_input(self.csv(source)))
        self.assertEqual(report['lineage']['status'], 'failed')
        self.assertEqual(report['lineage']['cross_partition_components'], 1)

    def test_future_end_time_is_availability_violation(self):
        source = rows()
        for item in source:
            item['observed_at'] = item['endTime']
        source[0]['observed_at'] = source[0]['startTime']
        report = datasets.audit_dataset(datasets.load_input(self.csv(source)))
        self.assertEqual(report['time_availability']['status'], 'failed')
        self.assertEqual(report['time_availability']['end_after_observation_rows'], 1)

    def test_archive_requires_policy_and_retains_source_lineage(self):
        from process_anomaly_pipeline.config import PipelineConfig
        path = self.root / 'input.json'
        archive = {'templates': [{'template_id': 'template', 'source_period': 's', 'rows': rows()}]}
        path.write_text(json.dumps(archive), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'policy.config'):
            datasets.load_input(path, 'archive')
        archive['policy'] = {'config': PipelineConfig().to_dict()}
        path.write_text(json.dumps(archive), encoding='utf-8')
        frame = datasets.load_input(path, 'archive')
        self.assertEqual(set(frame.source_template_id), {'template'})
        self.assertEqual(set(frame.source_period), {'s'})
        self.assertEqual(datasets.split_and_audit(frame)[1]['lineage']['status'], 'failed')


