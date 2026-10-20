"""Independent freeze/GO refusals happen before data generation or pickle load."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.evaluation.trend_recovery_comparison import datahelper
from research.evaluation.trend_recovery_comparison import run_experiment as runner


def write(path,value):
    path.write_text(json.dumps(value),encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SealContracts(unittest.TestCase):
    def test_final_go_and_development_acceptance_checked_before_load(self):
        for allow,split,accepted in [(False,'blind',True),(True,'calibration',True),(True,'blind',False)]:
            sealed={'phase':'final','protocol':{},'development_approved':accepted}
            with patch.object(runner,'check_seal',return_value=sealed), \
                    patch.object(runner.frozen,'checked_pickle') as load, \
                    patch.object(datahelper,'load_item') as generate:
                with self.assertRaises(ValueError):runner.run('unused','unused',split,allow)
                load.assert_not_called();generate.assert_not_called()

    def test_changed_dependency_refused_before_model_or_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);dependency=root/'frozen.bin';dependency.write_bytes(b'original')
            record={'sources':runner.source_hashes(),'protocol':datahelper.load_protocol(),
                    'dependencies':{str(dependency):hashlib.sha256(b'original').hexdigest()}}
            seal=root/'seal.json';write(seal,record)
            runner.check_seal(seal)
            dependency.write_bytes(b'changed')
            with patch.object(runner.frozen,'checked_pickle') as load,patch.object(datahelper,'load_item') as generate:
                with self.assertRaisesRegex(ValueError,'dependency'):runner.run(seal,root/'output')
                load.assert_not_called();generate.assert_not_called()
                self.assertFalse((root/'output').exists())

    def test_source_or_protocol_change_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'seal.json'
            record={'sources':{},'protocol':datahelper.load_protocol(),'dependencies':{}}
            write(path,record)
            with self.assertRaisesRegex(ValueError,'sources'):runner.check_seal(path)
            record['sources']=runner.source_hashes();record['protocol']['version']='changed'
            write(path,record)
            with self.assertRaisesRegex(ValueError,'Protocol'):runner.check_seal(path)

    def test_final_seal_needs_policy_cal_and_matching_accepted_record(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(runner.frozen,'sources',side_effect=lambda:{}):
            root=Path(folder);screen=root/'screen';prior=root/'prior';screen.mkdir();prior.mkdir()
            lock={'fixture':False,'sources':{},'seeds':[]}
            lock_sha=write(screen/'normal-lock.json',lock)
            write(screen/'manifest.json',{'normal_lock_sha256':lock_sha})
            write(prior/'manifest.json',{'status':'completed','normal_lock_sha256':lock_sha})
            p=copy.deepcopy(datahelper.load_protocol());p['base_normal_lock_sha256']=lock_sha
            with patch.object(datahelper,'load_protocol',return_value=p):
                with self.assertRaisesRegex(ValueError,'calibration disposition'):
                    runner.seal(screen,prior,root/'output','final')
                cal=root/'policy-cal-lock.json'
                cal_sha=write(cal,{'sources':runner.source_hashes(),'normal_lock_sha256':lock_sha,
                    'status':'completed','model_seeds':[101,137,173]})
                with self.assertRaisesRegex(ValueError,'accepted development'):
                    runner.seal(screen,prior,root/'output','final',policy_cal=cal)
                approval=root/'accepted.json'
                write(approval,{'status':'accepted','sources':runner.source_hashes(),'policy_cal_sha256':'wrong'})
                with self.assertRaisesRegex(ValueError,'excludes policy'):
                    runner.seal(screen,prior,root/'output','final',approval,cal)
                self.assertFalse((root/'output').exists())
                self.assertNotEqual(cal_sha,'wrong')


if __name__=='__main__':
    unittest.main()
