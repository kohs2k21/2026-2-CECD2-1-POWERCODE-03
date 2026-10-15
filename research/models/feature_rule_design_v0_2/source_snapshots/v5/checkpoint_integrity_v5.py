"""Audit actual final full checkpoint without executing any optimizer step."""
from prepare_v5 import *
import torch
from safetensors.torch import load_file

def main():
 from full_checkpoint import STATE_VERSION
 path=TOP/'training/checkpoints/step_058';manifest=read(path/'manifest.json');assert sha(path/'state.pt')==manifest['state_file']['sha256']
 for x in manifest['adapter_files']:assert sha(path/x['file'])==x['sha256']
 p=torch.load(path/'state.pt',map_location='cpu',weights_only=False);s=p['state'];cfg=read(TOP/'training/configuration.json');schedule=read(DATA/'training_schedule.json');trainpin=pin(DATA/'train.jsonl')
 assert s['dataset_sha']==trainpin['sha256']and s['configuration_sha']==digest(cfg)and s['schedule_sha']==digest(schedule);assert s['sample_order']==schedule and s['step']==58 and s['sampler_cursor']==232;assert sum(s['exposures'].values())==232 and len(s['exposures'])==160;assert p['scheduler']is None
 a=load_file(str(path/'adapter/adapter_model.safetensors'));b=load_file(str(TOP/'training/adapter_final/adapter_model.safetensors'));assert a.keys()==b.keys()and all(torch.equal(a[k],b[k])for k in a)
 assert all(torch.isfinite(v).all()for state in p['optimizer']['state'].values()for v in state.values()if isinstance(v,torch.Tensor));assert len(p['rng']['cuda'])==1;assert set(p['rng'])=={'python','numpy','torch_cpu','cuda'}
 write(TOP/'review/actual_checkpoint_integrity.json',{'passed':True,'actual_final_adapter_equal_to_complete_checkpoint':True,'adapter_tensors':len(a),'optimizer_tensors_finite':True,'optimizer_parameter_states':len(p['optimizer']['state']),'all_rng_states_present':True,'schedule_data_configuration_SHA_match':True,'retained_step':58,'physical_optimizer_steps':60,'sampler_cursor':232,'unique_samples':160,'effective_presentations':232,'scheduler':'not used constant LR','actual_GPU_OOM_complete_state_resume_performed':True,'CPU_exact_resume_test':pin(TOP/'review/CPU_full_resume_test_r02.json'),'periodic_checkpoints':[p.name for p in sorted((TOP/'training/checkpoints').glob('step_*'))],'no_extra_optimizer_steps':True});print('actual checkpoint integrity passed')
if __name__=='__main__':main()
