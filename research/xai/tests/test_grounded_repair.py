"""Grammar narrowing cannot make formerly invalid evidence acceptable."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xai_runtime.base_eval import view_for
from xai_runtime.grounded_repair import constrained_chunk_schema
from xai_runtime.grounded_followup import validate_chunk

EX = json.loads((Path(__file__).resolve().parents[1] / 'design/1.1.0/examples.json').read_text())


class GroundedRepairTests(unittest.TestCase):
    def test_wrong_template_and_cross_chunk_ID_are_blocked_by_schema(self):
        packet = deepcopy(EX[0]['input']);view=view_for(packet,packet['facts'][:1],0,2)
        validator=Draft202012Validator(constrained_chunk_schema(view))
        good=dict(schema_version='xai-grounded-chunk-plan/1.0.0',packet_id=packet['packet_id'],
                  items=[dict(template_id='rule_comparison',evidence_id='E1')],next_checks=[])
        validator.validate(good);validate_chunk(view,good)
        for field,value in [('template_id','ml_score'),('evidence_id','E2')]:
            bad=deepcopy(good);bad['items'][0][field]=value
            with self.assertRaises(ValidationError):validator.validate(bad)

    def test_missing_input_action_is_still_rejected_semantically(self):
        from xai_runtime.artifacts import InputError
        packet=deepcopy(EX[2]['input']);view=view_for(packet,packet['facts'],0,1)
        q=dict(schema_version='xai-grounded-chunk-plan/1.0.0',packet_id=packet['packet_id'],
               items=[dict(template_id=f['kind'],evidence_id=f['id']) for f in packet['facts']],next_checks=[])
        Draft202012Validator(constrained_chunk_schema(view)).validate(q)
        with self.assertRaisesRegex(InputError,'required_unavailable_followup_omitted'):validate_chunk(view,q)

    def test_unsupported_action_is_blocked_and_original_validator_unchanged(self):
        packet=deepcopy(EX[0]['input']);view=view_for(packet,packet['facts'][:1],0,1)
        q=dict(schema_version='xai-grounded-chunk-plan/1.0.0',packet_id=packet['packet_id'],
               items=[dict(template_id='rule_comparison',evidence_id='E1')],
               next_checks=[dict(code='review_recovery_history',evidence_ids=['E1'],priority=2)])
        with self.assertRaises(ValidationError):Draft202012Validator(constrained_chunk_schema(view)).validate(q)


if __name__=='__main__':unittest.main()
