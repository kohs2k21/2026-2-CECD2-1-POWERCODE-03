from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys,unittest
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from xai_runtime.artifacts import InputError
from xai_runtime.ml_sensitivity import Sensitivity


class SensitivityTests(unittest.TestCase):
    def fixture(self,score='6'):
        s=Sensitivity.__new__(Sensitivity);s.cache={};s.calls=0;s.probe_rows=0;s.rels={}
        columns=['a','b','c','missing__c'];encoder=SimpleNamespace(columns=columns,median=np.array([1.,1.,1.,0.]))
        contract=dict(features=['a','b','c'],columns=columns)
        s.bundles={'T_if':dict(model=SimpleNamespace(global_model=SimpleNamespace(encoder=encoder)),contract=contract)}
        s.model_meta=dict(models={'T_if':dict(file='fake.joblib',sha256='1'*64)})
        s.source=SimpleNamespace(model_folder=Path('.'),features={k:dict(sha='a'*64,unit='count',expression=dict(op='column',table='EC_TRANSACTION_MONITORING',column=k))for k in ['a','b','c']})
        frame=pd.DataFrame([dict(a=4.,b=2.,c=np.nan,missing__c=1.,population__a=True,population__b=True,population__c=True,adapterType='X',phase='steady')])
        s.frame=lambda *args:frame.copy()
        def scoring(bundle,probe):
            self.assertTrue((probe['missing__c']==1.).all());self.assertTrue((probe['adapterType']=='X').all());self.assertTrue(probe['c'].isna().all())
            values=probe[['a','b']].sum(axis=1).to_numpy();return values,(values>0).astype(np.int8)
        s.score_family=scoring
        f=dict(id='E',kind='ml_score',state='warning',source_refs=['T:1'],payload=dict(score_grain='T',algorithm='IF',model_sha256='1'*64,
                 feature_space_sha256='2'*64,score=score,score_direction='larger_is_more_anomalous',threshold='0'))
        return s,f
    @patch('xai_runtime.ml_sensitivity.sha',return_value='1'*64)
    def test_frozen_reference_probe_reproduces_score_and_preserves_missingness(self,_):
        s,f=self.fixture();r=s.explain(f,0,dict(__SOURCE_ROW_ID='1'))
        self.assertEqual(r['reproduced_score'],6.)
        self.assertEqual(r['top_positive_changes'][0]['feature_id'],'a')
        self.assertEqual(r['top_positive_changes'][0]['score_decrease'],3.)
        self.assertEqual(r['skipped_features'],['c']);self.assertTrue(r['no_fit'])
        self.assertIn('not additive attribution',r['limitation'])
    @patch('xai_runtime.ml_sensitivity.sha',return_value='1'*64)
    def test_mismatched_saved_score_is_rejected(self,_):
        s,f=self.fixture('7')
        with self.assertRaisesRegex(InputError,'not_reproduced'):s.explain(f,0,dict(__SOURCE_ROW_ID='1'))
    @patch('xai_runtime.ml_sensitivity.sha',return_value='0'*64)
    def test_changed_model_rejected(self,_):
        s,f=self.fixture()
        with self.assertRaisesRegex(InputError,'pin_changed'):s.explain(f,0,dict(__SOURCE_ROW_ID='1'))

if __name__=='__main__':unittest.main()
