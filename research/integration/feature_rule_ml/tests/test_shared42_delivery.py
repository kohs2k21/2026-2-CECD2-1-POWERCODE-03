"""Integration and failure-boundary checks on public constructed T/P/M CSVs."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import csv
import gzip
import json
import shutil
import unittest

import numpy as np

from research.integration.feature_rule_ml.common import InputFailure, read, write, sha
from research.integration.feature_rule_ml.shared42.artifacts import DEFAULT, load_models, profile, seal
from research.integration.feature_rule_ml.shared42.capture import Capture
from research.integration.feature_rule_ml.shared42.detector import detect, score_family
from research.integration.feature_rule_ml.shared42.example import make_capture
from research.integration.feature_rule_ml.shared42.features import materialize
from research.integration.feature_rule_ml.shared42.trainer import train
from research.integration.feature_rule_ml.shared42.importer import capture_csv


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp_parent = Path(__file__).resolve().parents[1]/'runs'
        temp_parent.mkdir(exist_ok=True)
        cls.tmp = TemporaryDirectory(dir=temp_parent,prefix='shared42-test-')
        cls.root = Path(cls.tmp.name)
        _,cls.p = profile()
        cls.paths = [make_capture(cls.root/n,n,d) for n,d in [('train','2026-07-01'),('calibration','2026-07-02'),('threshold','2026-07-03'),('test','2026-07-04')]]
        cls.training = train(*cls.paths[:3],cls.root/'trained')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy_capture(self, suffix):
        dest=self.root/('case_'+suffix)
        shutil.copytree(self.paths[3].parent,dest)
        return dest/'capture.json'

    def edit_csv(self, path, short, mutate):
        m=read(path);p=path.parent/m['tables'][short]['file']
        with p.open(newline='') as h:
            rd=csv.DictReader(h);header=rd.fieldnames;rows=list(rd)
        mutate(rows,header)
        with p.open('w',newline='') as h:
            w=csv.DictWriter(h,fieldnames=header);w.writeheader();w.writerows(rows)
        m['tables'][short].update(sha256=sha(p),row_count=len(rows));write(path,m)

    def test_all_nine_models_fit_saved_and_reloaded(self):
        self.assertEqual(self.training['new_ML_fit_count'],9)
        self.assertEqual(self.training['LLM_fit_count'],0)
        self.assertTrue(all(a['saved_reloaded_exact'] for a in self.training['audits']))
        self.assertEqual(len(load_models(self.root/'trained/models',self.p,True)[0]),9)
        meta=read(self.root/'trained/training_T_lof/model.json')
        self.assertEqual(meta['calibration_date']['start_inclusive'],'2026-07-02')

    def test_frozen_detector_cannot_call_fit_and_replay_is_exact(self):
        with patch('gh_ml_core.Family.fit',side_effect=AssertionError('unexpected fit')),patch('model_research_v5.models.RawModel.fit',side_effect=AssertionError('unexpected fit')):
            a=detect(self.paths[3],self.root/'detect_a',model_root=self.root/'trained/models',trusted=True)
            b=detect(self.paths[3],self.root/'detect_b',model_root=self.root/'trained/models',trusted=True)
        self.assertEqual(a['independent_checks'],65)
        self.assertEqual(a['rule_count'],110)
        self.assertEqual(a['ML_fit_count'],0)
        self.assertEqual(a['transaction'],b['transaction'])
        for name in ['T','P','M','transaction']:
            self.assertEqual(gzip.open(self.root/'detect_a'/(name+'_decisions.csv.gz'),'rb').read(),gzip.open(self.root/'detect_b'/(name+'_decisions.csv.gz'),'rb').read())

    def test_test_split_rejected_as_training(self):
        with self.assertRaisesRegex(InputFailure,'roles'):
            train(self.paths[3],self.paths[1],self.paths[2],self.root/'illegal_test_fit')

    def test_period_overlap_rejected(self):
        path=self.copy_capture('overlap');m=read(path);m['split']='calibration';m['period']=read(self.paths[0])['period'];write(path,m)
        # Move sample timestamps into the declared day, preserving valid capture admission.
        for s in 'TPM':
            self.edit_csv(path,s,lambda rows,h:[r.update({k:v.replace('2026-07-04','2026-07-01') for k,v in r.items()}) for r in rows])
        with self.assertRaisesRegex(InputFailure,'overlap|chronological'):
            train(self.paths[0],path,self.paths[2],self.root/'illegal_period_fit')

    def test_duplicate_family_across_references_rejected(self):
        path=self.copy_capture('leak');m=read(path);m['split']='calibration';write(path,m)
        original='EXAMPLE_train_0'
        for s in 'TPM':
            self.edit_csv(path,s,lambda rows,h:rows[0].update(TRANSACTION_ID=original))
        with self.assertRaisesRegex(InputFailure,'leakage'):
            train(self.paths[0],path,self.paths[2],self.root/'illegal_family_fit')

    def test_body_is_rejected_even_with_updated_file_hash(self):
        path=self.copy_capture('body')
        def modify(rows,header):
            header.append('response_body')
            for r in rows:r['response_body']='PUBLIC_TEST'
        self.edit_csv(path,'M',modify)
        with self.assertRaisesRegex(InputFailure,'schema|forbidden'):
            Capture(path,self.p)

    def test_explicit_metadata_projection_excludes_body_and_preserves_source(self):
        path=self.copy_capture('projection')
        def modify(rows,header):
            header.append('RESPONSE_MESSAGE')
            for r in rows:r['RESPONSE_MESSAGE']='PUBLIC_EXAMPLE_BODY'
        self.edit_csv(path,'T',modify)
        sources=[path.parent/(s+'.csv') for s in 'TPM']
        before=[sha(x) for x in sources]
        result=capture_csv(*sources,self.root/'projected','2026-07-04','2026-07-05','application','controlled',True,True,True,True)
        self.assertNotIn('PUBLIC_EXAMPLE_BODY',(self.root/'projected/T.csv').read_text())
        self.assertEqual(before,[sha(x) for x in sources])
        self.assertEqual(result['row_counts'],dict(T=32,P=32,M=32))

    def test_importer_does_not_guess_unknown_fields_or_completeness(self):
        path=self.copy_capture('projection_unknown')
        sources=[path.parent/(s+'.csv') for s in 'TPM']
        with self.assertRaisesRegex(InputFailure,'Explicit'):
            capture_csv(*sources,self.root/'unapproved_projection','2026-07-04','2026-07-05','application','controlled')
        with patch('research.integration.feature_rule_ml.shared42.importer.profile',side_effect=AssertionError('source should not be accessed')):
            with self.assertRaisesRegex(InputFailure,'held'):
                capture_csv(*sources,self.root/'held_projection','2026-09-30','2026-10-03','application','controlled',True,True,True,True)
        def modify(rows,header):
            header.append('injection_applied')
            for r in rows:r['injection_applied']='1'
        self.edit_csv(path,'P',modify)
        with self.assertRaisesRegex(InputFailure,'Unknown'):
            capture_csv(*sources,self.root/'unknown_projection','2026-07-04','2026-07-05','application','controlled',True,True,True,True)

    def test_label_column_cannot_enter_features(self):
        path=self.copy_capture('labels')
        def modify(rows,header):
            header.append('injection_applied')
            for r in rows:r['injection_applied']='1'
        self.edit_csv(path,'T',modify)
        with self.assertRaisesRegex(InputFailure,'schema'):
            Capture(path,self.p)

    def test_file_tamper_and_mutation_after_admission_rejected(self):
        path=self.copy_capture('tamper');c=Capture(path,self.p)
        (path.parent/'T.csv').write_text((path.parent/'T.csv').read_text()+'\n')
        with self.assertRaisesRegex(InputFailure,'SHA'):Capture(path,self.p)
        with self.assertRaisesRegex(InputFailure,'changed'):c.assert_unchanged()

    def test_retry_relation_lexical_copy_is_not_reinterpreted(self):
        path=self.copy_capture('retry')
        def modify(rows,header):
            header.append('__RETRY_LEXICAL')
            for r in rows:r['__RETRY_LEXICAL']='different'
        self.edit_csv(path,'P',modify)
        with self.assertRaisesRegex(InputFailure,'Retry'):Capture(path,self.p)

    def test_unit_schema_and_held_period_rejected(self):
        path=self.copy_capture('schema');m=read(path);m['schema_sha']='changed-unit';write(path,m)
        with self.assertRaisesRegex(InputFailure,'Schema'):Capture(path,self.p)
        m['schema_sha']=read(self.paths[3])['schema_sha'];m['period']=dict(start_inclusive='2026-09-30',end_exclusive='2026-10-03');write(path,m)
        with self.assertRaisesRegex(InputFailure,'held'):Capture(path,self.p)

    def test_malformed_numeric_is_rejected_instead_of_imputed(self):
        path=self.copy_capture('numeric');self.edit_csv(path,'P',lambda rows,h:rows[0].update(TOTAL_COUNT='not-a-number'))
        with self.assertRaisesRegex(InputFailure,'numeric'):Capture(path,self.p)

    def test_duplicate_transaction_id_rejected(self):
        path=self.copy_capture('duplicate');self.edit_csv(path,'T',lambda rows,h:rows[1].update(TRANSACTION_ID=rows[0]['TRANSACTION_ID']))
        with self.assertRaisesRegex(InputFailure,'duplicate'):Capture(path,self.p)

    def test_missing_relation_parent_is_retained_for_diagnostics(self):
        path=self.copy_capture('orphan');self.edit_csv(path,'M',lambda rows,h:rows[0].update(TRANSACTION_ID='EXAMPLE_ORPHAN'))
        result=detect(path,self.root/'orphan_detection',trusted=True)
        self.assertGreater(result['unlinked_source_rows_by_method']['M:HR'],0)
        with gzip.open(self.root/'orphan_detection/M_decisions.csv.gz','rt') as f:
            self.assertEqual(next(csv.DictReader(f))['transaction_id'],'EXAMPLE_ORPHAN')

    def test_empty_child_tables_are_not_invented_or_imputed_rows(self):
        path=self.copy_capture('empty_children')
        for short in 'PM':
            self.edit_csv(path,short,lambda rows,header:rows.clear())
        result=detect(path,self.root/'empty_children_detection',trusted=True)
        self.assertEqual(result['transactions'],32)
        self.assertEqual(result['native']['P']['ML'],{})
        self.assertEqual(result['native']['M']['ML'],{})

    def test_existing_outputs_are_not_overwritten(self):
        with self.assertRaisesRegex(InputFailure,'existing'):
            detect(self.paths[3],self.root/'trained',trusted=True)

    def test_missing_hr_authority_is_unassessable_not_healthy(self):
        path=self.copy_capture('authority');m=read(path);m['confirmed_hr_evidence_ids']=[];write(path,m)
        result=detect(path,self.root/'no_authority_detection',trusted=True)
        self.assertEqual(result['transaction']['HR'],{'unassessable':32})

    def test_untrusted_missing_or_changed_model_cannot_fit_as_fallback(self):
        with self.assertRaisesRegex(InputFailure,'trust'):
            load_models(DEFAULT/'models',self.p,False)
        dest=self.root/'bad_models';shutil.copytree(DEFAULT/'models',dest)
        (dest/'T_if.joblib').write_bytes(b'not-a-model')
        with self.assertRaisesRegex(InputFailure,'Changed'):
            load_models(dest,self.p,True)

    def test_all_unavailable_ML_row_never_becomes_nonwarning(self):
        features=materialize(Capture(self.paths[3],self.p),self.p)
        obj=load_models(self.root/'trained/models',self.p,True)[0]['T_if']
        frame=features['T']['frame'].iloc[:1].copy()
        frame.loc[:,obj['contract']['features']]=np.nan
        scores,codes=score_family(obj,frame)
        self.assertTrue(np.isnan(scores[0]));self.assertEqual(codes[0],-1)

    def test_frozen_models_are_included_and_paths_are_portable(self):
        self.assertEqual(len(list((DEFAULT/'models').glob('*.joblib'))),9)
        self.assertNotIn('/home/',(DEFAULT/'profile.json').read_text())
        self.assertNotIn('/home/',(DEFAULT/'models/model_set.json').read_text())


if __name__=='__main__':unittest.main()
