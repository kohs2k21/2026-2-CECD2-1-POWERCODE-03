"""Controlled source rows exercise selection, actual HR/ML and missing-value contracts."""
import copy
import json
import sys
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import jsonschema
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector.status_rule import default_policy, evaluate_status
from detector.policy import Policy
from trainer.selection import select_training
from trainer.metrics import compare
from telemetry import cost_family, percentile


class MetricsUtilityTests(unittest.TestCase):
    def test_status_policy_is_source_versioned_and_unknown_is_unavailable(self):
        policy = default_policy(uuid.uuid4())
        for status, anomaly, severity in [('S',False,[]), ('F',True,['critical']),
                                          ('P',True,['warning']), ('N',True,['warning'])]:
            result = evaluate_status(policy, {'status': status})
            self.assertEqual((result['status'],result['anomalous'],result['severity']), ('succeeded',anomaly,severity))
            self.assertEqual(result['evidence'][0]['sourceId'], policy['sourceId'])
        for status in ['?', None, '', 's']:
            self.assertIsNone(evaluate_status(policy, {'status': status})['anomalous'])
        changed = copy.deepcopy(policy); changed['mapping']['P'] = 'critical'
        with self.assertRaisesRegex(ValueError, 'unsupported_status_policy'):
            evaluate_status(changed, {'status':'P'})

    def test_sr_aggregator_is_independent_and_skipped_or_partial_never_comparable(self):
        def checker(anomaly,status='succeeded'): return {'status':status,'anomalous':anomaly}
        rows = [{'sr':checker(a), 'hr':checker(not a),'ml':checker(b)} for a,b in [(True,True),(True,False),(False,True),(False,False)]]
        rows += [{'sr':checker(True,'not_ready'),'ml':checker(True)}, {'sr':checker(False),'ml':checker(None,'skipped')}]
        metrics = compare(rows,'sr')
        self.assertTrue(all(metrics[key]['numerator']==1 for key in ('both','ruleOnly','mlOnly','neither')))
        self.assertEqual(metrics['missing']['value'],2)
        self.assertIsNone(compare([],'sr')['both']['value'])

    def test_cost_units_percentiles_nulls_and_memory_scope_match_schema(self):
        with patch('telemetry.process_rss',return_value=12345):
            family = cost_family({'totalMs':100,'trainMs':90},batch_size=10,samples=[1,2,3,4],queue_wait=5)
        self.assertEqual(family['metrics']['throughput']['value'],100)
        self.assertEqual(family['metrics']['itemP50Ms']['value'],2.5)
        self.assertAlmostEqual(percentile([1,2,3,4],.95),3.85)
        self.assertEqual(family['metrics']['processRssBytes']['unit'],'bytes')
        self.assertEqual(family['details']['memoryScope'],'process_current_rss_sample')
        self.assertIsNone(family['metrics']['esbLatencyMs']['value'])
        schema=json.loads((Path(__file__).resolve().parents[2]/'contracts/trainer-evaluation-envelope.schema.json').read_text())
        jsonschema.Draft202012Validator(schema['$defs']['measurement']).validate(family['metrics']['trainMs'])


if __name__=='__main__': unittest.main()
