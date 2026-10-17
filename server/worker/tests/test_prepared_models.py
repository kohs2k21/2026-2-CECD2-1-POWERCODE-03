"""Actual prepared IF proves no duplicate scaler/feature transforms or truth fill."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import joblib
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trainer.prepared_models import train,load,scores,check_evaluation_lineage,check_scaling_lineage
from trainer.prepared_parser import parse_csv
from trainer.prepared_evaluation import report
from test_prepared_csv import fixture,content


class PreparedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        cls.manifest,source=fixture(651)
        source[0][2]='anomaly'; source[1][4]='true'; source[2][3]='F'
        cls.rows=parse_csv(content(cls.manifest,source),cls.manifest)['rows']
        cls.original=copy.deepcopy(cls.rows)
        with patch('sklearn.preprocessing.RobustScaler.fit',side_effect=AssertionError('scaler refit')), \
             patch('sklearn.preprocessing.RobustScaler.transform',side_effect=AssertionError('scaler twice')):
            cls.trained=train(cls.rows,cls.manifest,cls.directory.name,dataset_id=uuid.uuid4(),snapshot_id=uuid.uuid4())
        cls.settings={'preparedInputContract':cls.trained['inputContract'],'preparedTrainingReference':cls.trained['trainingReference']}

    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()

    def test_actual_fixed_forest_scores_same_completed_vectors_without_scaler(self):
        with patch('sklearn.preprocessing.RobustScaler.transform',side_effect=AssertionError('scaler twice')):
            forest,reference=load(self.trained['modelPath'],self.trained['fitPath'],self.manifest,self.settings)
            self.assertEqual(scores(forest,self.rows[-1:]),(-forest.score_samples(np.asarray([self.rows[-1]['features']]))).tolist())
        self.assertEqual(self.rows,self.original)
        self.assertEqual(self.trained['trainingSelection']['selectedCount'],647)
        self.assertTrue({'0','1','2'}.isdisjoint(reference['eligibleRowIds']))
        self.assertTrue({'0','1','2'}.issubset(reference['trainRowIds']))
        self.assertFalse(joblib.load(self.trained['fitPath'])['additionalTransformApplied'])
        self.assertNotIn('scaler',joblib.load(self.trained['fitPath']))

    def test_exact_input_stage_schema_and_fit_mismatch_reject(self):
        changes=[lambda m:m['features'].reverse(),lambda m:m['features'][0].update(unit='ms'),
                 lambda m:m['preprocessing'].update(fitHash='c'*64),lambda m:m['preprocessing'].update(scaling='none'),
                 lambda m:m.update(domain='different'),lambda m:m.update(inputStage='feature_values')]
        for change in changes:
            manifest=copy.deepcopy(self.manifest); change(manifest)
            with self.assertRaisesRegex(ValueError,'input_contract_mismatch'):
                load(self.trained['modelPath'],self.trained['fitPath'],manifest,self.settings)
        with self.assertRaisesRegex(ValueError,'input_contract_mismatch'):
            load(self.trained['modelPath'],self.trained['fitPath'],self.manifest,{})

    def test_model_fit_lineage_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for key in ('eligibleRowIdsHash','trainRowIds','trainOriginIds','trainGroupIds'):
                fit=joblib.load(self.trained['fitPath'])
                fit['trainingReference'][key]='c'*64 if key.endswith('Hash') else ['tampered']
                path=Path(directory)/'fit.joblib'; joblib.dump(fit,path)
                with self.subTest(reference_key=key):
                    with self.assertRaisesRegex(ValueError,'fit_lineage_mismatch'):
                        load(self.trained['modelPath'],path,self.manifest,self.settings)
            settings=copy.deepcopy(self.settings)
            settings['preparedTrainingReference']['trainRowIds']=['tampered']
            with self.assertRaisesRegex(ValueError,'fit_lineage_mismatch'):
                load(self.trained['modelPath'],self.trained['fitPath'],self.manifest,settings)

    def test_training_evaluation_row_origin_alias_and_group_overlap_rejected(self):
        reference={'trainRowIds':['train-row'],'trainOriginIds':['root-row'],'trainGroupIds':['group']}
        for row in [{'rowId':'train-row','originRowId':None,'groupId':None},
                    {'rowId':'derived','originRowId':'train-row','groupId':None},
                    {'rowId':'root-row','originRowId':None,'groupId':None},
                    {'rowId':'new','originRowId':None,'groupId':'group'}]:
            with self.assertRaisesRegex(ValueError,'overlap'): check_evaluation_lineage([row],reference)
        check_evaluation_lineage([{'rowId':'independent','originRowId':None,'groupId':None}],reference)

    def test_forest_excluded_train_rows_still_block_evaluation_preprocessing_leakage(self):
        _,reference=load(self.trained['modelPath'],self.trained['fitPath'],self.manifest,self.settings)
        for row_id in ('0','1','2'):
            self.assertNotIn(row_id,reference['eligibleRowIds'])
            for row in [{'rowId':row_id,'originRowId':None,'groupId':None},
                        {'rowId':'derived-eval','originRowId':row_id,'groupId':None},
                        {'rowId':'independent','originRowId':'origin-'+row_id,'groupId':None},
                        {'rowId':'independent','originRowId':None,'groupId':'group-'+row_id}]:
                with self.subTest(excluded_train_row=row_id,evaluation=row):
                    with self.assertRaisesRegex(ValueError,'overlap'):
                        check_evaluation_lineage([row],reference)
        check_evaluation_lineage(self.rows[-1:],reference)

    def test_actual_training_boundary_625_accepts_624_rejects(self):
        for count,expected in ((624,False),(625,True)):
            manifest,source=fixture(count+1)
            rows=parse_csv(content(manifest,source),manifest)['rows']
            with tempfile.TemporaryDirectory() as directory:
                if expected:
                    trained=train(rows,manifest,directory,dataset_id=uuid.uuid4(),snapshot_id=uuid.uuid4())
                    self.assertEqual(trained['thresholdSpec']['referenceCount'],125)
                else:
                    with self.assertRaisesRegex(ValueError,'fit_calibration_population_insufficient'):
                        train(rows,manifest,directory,dataset_id=uuid.uuid4(),snapshot_id=uuid.uuid4())

    def test_manual_labels_unknown_and_absent_raw_checks_are_distinct(self):
        rows=[{'label':label} for label in ['anomaly','normal','normal','anomaly','unknown']]
        result=report(rows,[True,True,False,False,True])
        self.assertEqual({key:result[key] for key in ('tp','fp','tn','fn','unlabeledCount')},dict(tp=1,fp=1,tn=1,fn=1,unlabeledCount=1))
        self.assertEqual(result['precision'],.5); self.assertEqual(result['recall'],.5)
        self.assertEqual(result['hr'],{'value':None,'reason':'raw_rule_inputs_unavailable'})
        self.assertEqual(result['sr'],{'value':None,'reason':'raw_rule_inputs_unavailable'})
        self.assertIsNone(result['esbLatency']['value'])

    def test_unknown_scaled_fit_is_only_permitted_within_same_immutable_dataset(self):
        reference=self.trained['trainingReference']
        unknown=copy.deepcopy(self.manifest)
        unknown['preprocessing'].update(fitHash=None,fitSplit='unknown',trainRowIdsHash=None)
        check_scaling_lineage(unknown,reference,reference['datasetId'])
        with self.assertRaisesRegex(ValueError,'scaling_fit_unverified'):
            check_scaling_lineage(unknown,reference,str(uuid.uuid4()))
        check_scaling_lineage(self.manifest,reference,str(uuid.uuid4()))
        unknown['preprocessing']['scaling']='none'
        check_scaling_lineage(unknown,reference,str(uuid.uuid4()))


if __name__=='__main__': unittest.main()
