"""Boundary tests: chunking may never conceal required or cross-packet evidence."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xai_runtime.base_eval import split_packet,view_for,validate_chunk,merge_plans
from xai_runtime.engine import default_plan,render
from xai_runtime.artifacts import InputError
EX=json.loads((Path(__file__).resolve().parents[1]/'design/1.1.0/examples.json').read_text())[0]['input']
class Tokenizer:
    def apply_chat_template(self,msg,**kwargs):return json.dumps(msg)
    def __call__(self,text,**kwargs):return {'input_ids':list(range(len(text)//8))}
class ChunkTests(unittest.TestCase):
    def test_split_and_merge_preserve_all_required_and_exact_values(self):
        p=copy.deepcopy(EX);views=split_packet(p,Tokenizer(),max_facts=1,max_input=6144)
        self.assertEqual([f for v in views for f in v['facts']],p['facts'])
        plans=[dict(schema_version='xai-chunk-plan/1.0.0',packet_id=p['packet_id'],items=[dict(template_id=f['kind'],evidence_id=f['id']) for f in v['facts'] if f['required']],next_check_codes=[]) for v in views]
        merged=merge_plans(p,views,plans);actual=render(p,supplied_plan=merged)
        self.assertEqual(actual['decision'],p['decision']);self.assertEqual(actual['severity'],p['severity'])
        self.assertEqual({s['evidence_id'] for s in actual['sentences']},{f['id'] for f in p['facts'] if f['required']})
    def test_other_chunk_fact_and_required_omission_rejected(self):
        p=copy.deepcopy(EX);v=view_for(p,p['facts'][:1],0,2)
        plan=dict(schema_version='xai-chunk-plan/1.0.0',packet_id=p['packet_id'],items=[],next_check_codes=[])
        with self.assertRaisesRegex(InputError,'required_fact_omitted'):validate_chunk(v,plan)
        plan['items']=[dict(template_id=p['facts'][1]['kind'],evidence_id=p['facts'][1]['id'])]
        with self.assertRaisesRegex(InputError,'cross_chunk'):validate_chunk(v,plan)
    def test_oversized_single_fact_is_not_truncated(self):
        with self.assertRaisesRegex(InputError,'single_evidence_over_token_budget'):
            split_packet(copy.deepcopy(EX),Tokenizer(),max_input=1)
    def test_timeout_fallback_keeps_global_required_and_severity(self):
        p=copy.deepcopy(EX);a=render(p,failure_reason='base_chunk_failure');b=render(p)
        self.assertEqual(a['status'],'template_fallback');self.assertEqual(a['sentences'],b['sentences'])
        self.assertEqual(a['severity'],p['severity']);self.assertEqual(a['decision'],p['decision'])
if __name__=='__main__':unittest.main()
