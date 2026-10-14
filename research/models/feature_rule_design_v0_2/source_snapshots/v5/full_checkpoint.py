"""Complete optimizer-boundary checkpoint. No guessed continuation or partial gradients."""
import os,random,torch,numpy as np
from pathlib import Path
from pilot_io import digest,write,read,pin,sha
STATE_VERSION='complete-feature-QLoRA-state-1.0'
def rng_state():
 return {'python':random.getstate(),'numpy':np.random.get_state(),'torch_cpu':torch.get_rng_state(),'cuda':torch.cuda.get_rng_state_all()if torch.cuda.is_available()else []}
def restore_rng(s):
 random.setstate(s['python']);np.random.set_state(s['numpy']);torch.set_rng_state(s['torch_cpu'])
 if s['cuda']:torch.cuda.set_rng_state_all(s['cuda'])
def save_checkpoint(path,model,optimizer,state,*,adapter=True,scheduler=None):
 path=Path(path);assert not path.exists();stage=path.parent/(path.name+'.incomplete');stage.mkdir(parents=True,exist_ok=False)
 assert all(p.grad is None for p in model.parameters()),'checkpoint requires optimizer boundary'
 if adapter:model.save_pretrained(stage/'adapter',safe_serialization=True)
 else:torch.save(model.state_dict(),stage/'toy_model.pt')
 payload={'version':STATE_VERSION,'optimizer':optimizer.state_dict(),'scheduler':scheduler.state_dict()if scheduler else None,'rng':rng_state(),'state':state}
 torch.save(payload,stage/'state.pt');write(stage/'manifest.json',{'version':STATE_VERSION,'state':state,'state_file':pin(stage/'state.pt'),'adapter_files':[{"file":str(p.relative_to(stage)),"sha256":sha(p)}for p in stage.rglob('*')if p.is_file()and p.name!='state.pt'],'adapter_only':adapter,'optimizer_present':True,'scheduler_policy':'state_saved'if scheduler else 'not_used_constant_learning_rate','rng_cpu_cuda_python_numpy':True,'complete_optimizer_boundary':True})
 # Source paths below include staging path, so do not use the pin path after rename.
 os.rename(stage,path)
 return path

def load_checkpoint(path,model,optimizer,expected,scheduler=None,*,adapter=True):
 path=Path(path);manifest=read(path/'manifest.json')
 from pilot_io import sha
 assert sha(path/'state.pt')==manifest['state_file']['sha256'],'checkpoint state SHA mismatch'
 for p in manifest.get('adapter_files',[]):assert sha(path/p['file'])==p['sha256'],'checkpoint adapter SHA mismatch'
 payload=torch.load(path/'state.pt',map_location='cpu',weights_only=False);s=payload['state']
 for k,v in expected.items():assert s[k]==v,('resume_identity_mismatch',k)
 if not adapter:model.load_state_dict(torch.load(path/'toy_model.pt',weights_only=True))
 optimizer.load_state_dict(payload['optimizer'])
 if scheduler:
  assert payload['scheduler'] is not None;scheduler.load_state_dict(payload['scheduler'])
 else:assert payload['scheduler'] is None
 restore_rng(payload['rng']);return s

def cpu_resume_test(output):
 from copy import deepcopy
 from collections import Counter
 torch.manual_seed(53);random.seed(53);np.random.seed(53)
 # Small actual low-rank frozen-base module exercises dropout, AdamW, sample order and RNG.
 class TinyLoRA(torch.nn.Module):
  def __init__(self):
   super().__init__();self.base=torch.nn.Linear(4,3,bias=False);self.base.weight.requires_grad_(False);self.A=torch.nn.Parameter(torch.randn(2,4)*.02);self.B=torch.nn.Parameter(torch.zeros(3,2));self.drop=torch.nn.Dropout(.05)
  def forward(self,x):return self.base(x)+self.drop(x)@self.A.T@self.B.T
 m=TinyLoRA();initial=deepcopy(m.state_dict());rng=rng_state();schedule=['case_'+str(i%9)for i in range(16)];cfg={'lr':.0002,'steps':16,'rank':2};identity={'dataset_sha':'controlled_cpu_fixture','configuration_sha':digest(cfg),'schedule_sha':digest(schedule)}
 def fresh():
  z=TinyLoRA();z.load_state_dict(initial);o=torch.optim.AdamW([z.A,z.B],lr=cfg['lr']);restore_rng(rng);return z,o
 def iterate(z,o,start,end,expo):
  z.train()
  for i in range(start,end):
   x=torch.randn(2,4)+random.random()+float(np.random.random());v=(z(x)**2).mean();v.backward();o.step();o.zero_grad(set_to_none=True);expo[schedule[i]]+=1
 z,op=fresh();all_exp=Counter();iterate(z,op,0,16,all_exp);full=deepcopy(z.state_dict());full_opt=deepcopy(op.state_dict());full_rng=rng_state()
 z,op=fresh();exp=Counter();iterate(z,op,0,6,exp);ck=Path(output).parent/('cpu_complete_checkpoint_'+Path(output).stem);state={**identity,'step':6,'sampler_cursor':6,'exposures':dict(exp),'losses':[],'sample_order':schedule};save_checkpoint(ck,z,op,state,adapter=False)
 z,op=fresh();s=load_checkpoint(ck,z,op,identity,adapter=False);exp=Counter(s['exposures']);iterate(z,op,s['sampler_cursor'],16,exp)
 assert all(torch.equal(z.state_dict()[k],v)for k,v in full.items());assert exp==all_exp
 for k,v in op.state_dict()['state'].items():
  for n,w in v.items():assert torch.equal(w,full_opt['state'][k][n])if isinstance(w,torch.Tensor)else w==full_opt['state'][k][n]
 final=rng_state();assert torch.equal(final['torch_cpu'],full_rng['torch_cpu'])and final['python']==full_rng['python']
 rejected=False
 try:load_checkpoint(ck,z,op,{**identity,'dataset_sha':'changed_data'},adapter=False)
 except AssertionError:rejected=True
 assert rejected
 write(output,{'passed':True,'exact_final_weights':True,'exact_AdamW_state':True,'exact_sample_order_and_exposures':True,'exact_torch_python_rng':True,'numpy_rng_exercised':True,'dataset_configuration_schedule_SHA_checked':True,'changed_data_rejected':True,'adapter_analogue_low_rank_frozen_Base':True,'checkpoint_step':6,'total_steps':16,'GPU_resume_actual_not_yet_exercised':True,'scheduler':'not used constant learning rate'})
if __name__=='__main__':
 from pilot_io import TOP
 cpu_resume_test(TOP/'review/CPU_full_resume_test_r02.json')
