"""Distribution checks using only shipped artifacts and synthetic inputs."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from selection_runner import verify_adapter
from xai_runtime.evidence_selection import default_selection,validate_selection


class DeliveryTests(unittest.TestCase):
    def test_shipped_adapter_matches_original_weight_identity(self):
        card=verify_adapter(ROOT/'models/xai_summary_lora_v1')
        self.assertEqual(card['adapter']['weights_sha256'],'c8f98ff98bdb32d75932b3efaa2b44c04e3a7707fff265babae2add0e2b2766e')
        self.assertFalse(card['adapter']['weights_changed'])

    def test_safetensors_contains_only_expected_LoRA_matrices(self):
        file=ROOT/'models/xai_summary_lora_v1/adapter_model.safetensors'
        with file.open('rb') as f:
            size=struct.unpack('<Q',f.read(8))[0];header=json.loads(f.read(size))
        tensors={k:v for k,v in header.items() if k!='__metadata__'}
        parameters=0
        for key,value in tensors.items():
            self.assertTrue('lora_A' in key or 'lora_B' in key)
            self.assertEqual(len(value['shape']),2)
            parameters+=value['shape'][0]*value['shape'][1]
            self.assertLessEqual(value['data_offsets'][1]+size+8,file.stat().st_size)
        self.assertEqual(parameters,23760896)

    def test_portable_config_uses_pinned_Base_identity(self):
        config=json.loads((ROOT/'models/xai_summary_lora_v1/adapter_config.json').read_text())
        base=json.loads((ROOT/'models/base_files.json').read_text())
        self.assertEqual(config['base_model_name_or_path'],base['repo_id'])
        self.assertEqual(config['revision'],base['revision'])
        self.assertEqual(config['r'],8)

    def test_CPU_CLI_runs_without_private_logs_or_models(self):
        with tempfile.TemporaryDirectory(dir=ROOT.parent) as temp:
            for name in ('service_HR','service_unavailable','research_packet'):
                target=Path(temp)/(name+'.json')
                r=subprocess.run([sys.executable,'-B',str(ROOT/'run.py'),'--input',str(ROOT/'examples'/(name+'.json')),
                                  '--output',str(target)],cwd=temp,capture_output=True,text=True)
                self.assertEqual(r.returncode,0,r.stderr)
                result=json.loads(target.read_text())
                if name=='service_HR':self.assertEqual(len(result['facts']),4)
                if name=='service_unavailable':self.assertTrue(any(f['state']=='unavailable' for f in result['facts']))

    def test_shipped_selection_input_has_valid_ID_only_plan(self):
        view=json.loads((ROOT/'examples/selection_view.json').read_text())
        plan=default_selection(view);validate_selection(view,plan)
        self.assertEqual(set(plan),{'schema_version','summary_evidence_ids'})


if __name__=='__main__':unittest.main()
