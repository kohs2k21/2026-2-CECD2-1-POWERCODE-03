"""AR/IID controls: independent tuple, correlation, lineage and sealed gates."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from research.evaluation.trend_sr_comparison import dataset, relation_contexts as rc
from research.evaluation.trend_sr_comparison.tests.audit_generation_artifacts import read_arrays


class RelationContextContracts(unittest.TestCase):
    def test_all_eight_cases_five_roles_preserve_declared_counterfactuals(self):
        cases=rc.case_specs(9501,'selection')
        self.assertEqual(len(cases),8)
        fields=['count','duration_ms','total_count','error_count']
        for case in cases:
            args=(9501,'selection',case['context'],case['context_case_id'])
            base=rc.generate_context_case(*args,'no_intervention')
            if case['context']=='AR': pd.testing.assert_frame_equal(base.frame,dataset.generate(9501,'selection','normal').frame)
            start,end=case['intervals'][0]
            mask=base.frame.group.eq(case['group']) & base.frame.bin_start.ge(start) & base.frame.bin_start.lt(end)
            before=base.frame.loc[mask]
            for role in rc.ROLES:
                item=rc.generate_context_case(*args,role)
                pd.testing.assert_frame_equal(item.frame.loc[~mask],base.frame.loc[~mask])
                self.assertEqual(len(item.events),int(role=='intervention'))
                np.testing.assert_array_equal(np.sort(item.frame.loc[mask,'duration_ms']),np.sort(before.duration_ms))
                if role in ('no_intervention','marginal_only'): pd.testing.assert_frame_equal(item.frame,base.frame)
                elif role=='serial_only':
                    after=item.frame.loc[mask]
                    hours=before.bin_start.dt.tz_convert('Asia/Seoul').dt.floor('h')
                    for hour in hours.unique():
                        ids=hours.eq(hour).to_numpy()
                        left=sorted(map(tuple,before.iloc[np.flatnonzero(ids)][fields].to_numpy()))
                        right=sorted(map(tuple,after.iloc[np.flatnonzero(ids)][fields].to_numpy()))
                        self.assertEqual(left,right)
                    self.assertFalse(before[fields].equals(after[fields]))
                    self.assertAlmostEqual(np.corrcoef(before['count'],before.duration_ms)[0,1],np.corrcoef(after['count'],after.duration_ms)[0,1],places=12)
                    self.assertAlmostEqual(spearmanr(before['count'],before.duration_ms).statistic,spearmanr(after['count'],after.duration_ms).statistic,places=12)
                elif role=='cross_alignment_shift': np.testing.assert_array_equal(item.frame.loc[mask,'duration_ms'],np.roll(before.duration_ms.to_numpy(),61))
                elif role=='intervention':
                    value=np.corrcoef(item.frame.loc[mask,'count'],item.frame.loc[mask,'duration_ms'])[0,1]
                    self.assertLessEqual(abs(value),.3) if case['subtype']=='R01' else self.assertLessEqual(value,-.4)

    def test_iid_factory_calls_zero_coefficient_without_mutating_original_factory(self):
        rc._normal_context.cache_clear()
        original=dataset._ar
        calls=[]
        def trace(*args,**kwargs):
            calls.append(kwargs.get('coefficient',.35))
            return original(*args,**kwargs)
        normal=dataset.generate(9501,'selection','normal').frame.copy(deep=True)
        with patch.object(dataset,'_ar',side_effect=trace): iid=rc._normal_context(9501,'selection','IID')
        self.assertEqual(calls,[0.,0.,0.,0.])
        self.assertIs(dataset._ar,original)
        pd.testing.assert_frame_equal(dataset.generate(9501,'selection','normal').frame,normal)
        self.assertFalse(iid.equals(normal))

    def test_final_is_rejected_before_context_factory(self):
        with patch.object(rc,'_normal_context',side_effect=AssertionError('sealed source opened')):
            with self.assertRaises(ValueError): rc.generate_context_case(4101,'final','IID','sealed','no_intervention')

    def test_saved_fixture_all_forty_sources_hash_and_patch_reconstruction(self):
        private=os.environ.get('SR_REVIEW_ARTIFACT_ROOT')
        if not private: self.skipTest('explicit private artifact location not supplied')
        root=Path(private).parent/'relation-context-fixture9501-v1'
        paths=list(root.rglob('definition.json'))
        self.assertEqual(len(paths),40)
        codehash=hashlib.sha256(Path(rc.__file__).read_bytes()).hexdigest()
        for path in paths:
            manifest=json.loads(path.read_text(encoding='utf-8')); folder=path.parent
            self.assertEqual(manifest['seed'],9501)
            self.assertEqual(manifest['context_code_sha256'],codehash)
            for relative,digest in json.loads((folder/'hashes.json').read_text()).items():
                target=(folder/relative).resolve(); self.assertTrue(target.is_relative_to(root.resolve()))
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),digest)
            columns=manifest['source_columns']
            frame,_=read_arrays(folder/manifest['context_normal_file'],columns)
            updated,positions=read_arrays(folder/'source-patch.npz',columns)
            self.assertEqual(len(positions),len(np.unique(positions)))
            pd.testing.assert_index_equal(frame.index[positions],updated.index)
            for column in columns: frame.iloc[positions,frame.columns.get_loc(column)]=updated[column].to_numpy()
            digest=hashlib.sha256(pd.util.hash_pandas_object(frame,index=True).to_numpy().tobytes()).hexdigest()
            self.assertEqual(digest,manifest['source_hash'])


if __name__=='__main__': unittest.main()
