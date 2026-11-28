from copy import deepcopy
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xai_runtime.artifacts import InputError,digest
from xai_runtime.explanation_context import label,dependencies,build_context,groups,render_explained,validate_explained
from xai_runtime.statistical_severity import apply,calibrate
from test_grounded_followup import example,add_unavailable


class ExplanationContextTests(unittest.TestCase):
    def setUp(self):
        self.policy=calibrate([example(i)for i in range(1,10)],dict(id='context-test'))
        self.definition=dict(schema=dict(relationships=[]),features=[])
    def packet(self):
        p=example(4);add_unavailable(p);return apply(p,self.policy)[0]
    def context(self,p):return build_context(p,dict(packet_id=p['packet_id'],fact_metadata={}),self.definition)
    def test_aggregate_label_resolves_different_relations_and_filter(self):
        rels={i:dict(id=i,from_table=child,to_table='EC_TRANSACTION_MONITORING',from_columns=['TRANSACTION_ID'],to_columns=['TRANSACTION_ID'])for i,child in [('p','EC_PROCESS_MONITORING'),('m','EC_MESSAGE_MONITORING')]}
        expr=dict(op='subtract',args=[dict(op='child_count',relationship_id=i)for i in ('p','m')])
        text=label(expr,rels)
        self.assertIn('프로세스',text);self.assertIn('메시지',text);self.assertNotIn('child_count()',text)
        self.assertEqual(len(dependencies(expr,rels)['relationships']),2)
    def test_visible_locations_specific_unknown_and_fixed_grade(self):
        p=self.packet();c=self.context(p);r=render_explained(p,self.policy,c)
        self.assertEqual(r['decision'],p['decision']);self.assertEqual(r['severity'],p['severity'])
        self.assertEqual(r['severity_interpretation']['operational_impact'],'unconfirmed')
        self.assertTrue(all(s['locations']for s in r['details']))
        self.assertTrue(any('결측·평가 불가 확인' in q['text']for q in r['next_checks']))
    def test_large_packets_keep_all_details(self):
        p=self.packet()
        for i in range(20):
            f=deepcopy(p['facts'][0]);f['id']='repeat'+str(i);p['facts'].append(f)
        c=self.context(p);r=render_explained(p,self.policy,c)
        self.assertGreater(r['coverage']['adverse_facts'],16)
        self.assertTrue({f['id']for f in p['facts']if f['required']}<={s['evidence_id']for s in r['details']})
    def test_reordering_preserves_semantic_group_ids(self):
        p=self.packet();c=self.context(p);first=[g['id']for g in groups(p,c)]
        p['facts'].reverse();c=self.context(p)
        self.assertEqual(first,[g['id']for g in groups(p,c)])
    def test_wrong_packet_and_changed_text_rejected(self):
        p=self.packet();c=self.context(p);r=render_explained(p,self.policy,c)
        bad=deepcopy(r);bad['details'][0]['text']='네트워크 장애 확정'
        with self.assertRaisesRegex(InputError,'unsupported_explanation'):validate_explained(p,c,bad)
        c['packet_sha256']='0'*64
        with self.assertRaisesRegex(InputError,'context_packet_changed'):render_explained(p,self.policy,c)

if __name__=='__main__':unittest.main()
