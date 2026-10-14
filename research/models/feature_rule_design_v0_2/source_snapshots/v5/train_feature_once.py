"""One fresh Base feature-only QLoRA,60steps; frozen16 comparison; no repair."""
from feature_contract import *
from prepare_features import load_context
from inference_v3 import load_model,gen_batch
from budget import reserve,complete,audit
from peft import get_peft_model,LoraConfig,PeftModel
import torch,torch.nn.functional as F,time,subprocess,traceback,os
OUT=TOP/'training';START=time.monotonic()
def mem():return {'allocated_MiB':round(torch.cuda.memory_allocated()/2**20,1),'peak_MiB':round(torch.cuda.max_memory_allocated()/2**20,1),'reserved_MiB':round(torch.cuda.memory_reserved()/2**20,1)}
def event(kind,**kw):
 with (OUT/'events.jsonl').open('a')as f:f.write(json.dumps({'event':kind,'seconds':round(time.monotonic()-START,2),**kw})+'\n')
 print(json.dumps({'event':kind,**kw}),flush=True)
def run():
 OUT.mkdir();freeze=read(TOP/'evaluation_freeze.json')
 assert read(TOP/'pretraining_admission.json')['passed']
 write(OUT/'review_freeze.json',{'pins':[pin(TOP/'pretraining_admission.json'),pin(TOP/'frozen_judgment_criteria.json'),pin(TOP/'review/leakage_audit.json')],'before_any_model_output':True})
 for p in freeze['pins']:assert pin(ROOT/p['path'])==p
 code=[pin(x)for x in (TOP/'implementation').glob('*.py')];write(OUT/'execution_freeze.json',{'code':code,'data':pin(TOP/'evaluation_freeze.json'),'fresh_from_original_Base':True,'SFT_runs':1,'maximum_optimizer_steps':60})
 gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True);write(OUT/'GPU_preflight.json',{'GPU':gpu,'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES'),'torch_visible':torch.cuda.device_count(),'bf16':torch.cuda.is_bf16_supported()});assert torch.cuda.device_count()==1
 torch.manual_seed(42);model,tok=load_model(None);base=model;event('Base_loaded',actual_class=type(model).__name__,**mem())
 train=rows(TOP/'data_r04/train.jsonl');evals=rows(TOP/'data_r04/eval_private.jsonl');encoded={}
 for r in train+evals:
  prefix=tok(tok.apply_chat_template(r['messages'][:2],tokenize=False,add_generation_prompt=True,enable_thinking=False),add_special_tokens=False)['input_ids'];answer=tok(r['messages'][2]['content']+tok.eos_token,add_special_tokens=False)['input_ids'];encoded[r['id']]={'prefix':prefix,'answer':answer,'full':prefix+answer};assert len(prefix+answer)<=4096
 write(OUT/'loss_mask_audit.json',{'assistant_and_EOS_loss_only':True,'input_loss_tokens':0,'truncated_samples':0,'max_length':4096,'max_actual_length':max(len(x['full'])for x in encoded.values())})
 def loss(r):
  e=encoded[r['id']];ids=torch.tensor([e['full']],device='cuda:0');pos=torch.arange(len(e['prefix'])-1,len(e['full'])-1,device='cuda:0');o=model(input_ids=ids,attention_mask=torch.ones_like(ids),use_cache=False,logits_to_keep=pos,return_dict=True);return F.cross_entropy(o.logits[0].float(),torch.tensor(e['answer'],device=o.logits.device)),len(e['answer'])
 def evaluate(phase):
  model.eval();ls=[];outputs=[]
  with torch.no_grad():
   for r in evals:
    v,n=loss(r);assert torch.isfinite(v);ls.append({'id':r['id'],'loss':float(v),'answer_tokens':n})
  for r in evals:
   c=load_context((ROOT/r['source_context']['path']).parent);admission(c,'model_input');assert pin(ROOT/r['source_context']['path'])==r['source_context'];group='frozen_base'if phase=='base'else 'frozen_feature_adapter';key=reserve(group,[r['id']],phase,2048);g=gen_batch(model,tok,[r['messages'][:2]],2048)[0]
   try:
    o=json.loads(g['raw_output']);check=evaluate_candidates(c,o)
    visible={(t['table'],z['name'])for t in r['input']['tables']for z in t['columns']};vr={z['id']for z in r['input']['relationships']}
    for x in check['candidates']:
     q=x['candidate'];x['visible_sources']=set(tuple(z)for z in q.get('sources',{}).get('columns',[]))<=visible and set(q.get('sources',{}).get('relationships',[]))<=vr
     if not x['visible_sources']:x['errors'].append('source not present in supplied schema projection')
   except Exception as e:o=None;check={'JSON_contract':False,'candidates':[],'zero':False,'errors':[str(e)]}
   item={'id':r['id'],'phase':phase,'source_kind':r['source_scope']['source_kind'],'category':r['category'],'input_sha':digest(r['input']),'prediction':o,'checks':check,'direct_meaning_status':'pending_root_review',**g};write(OUT/'responses'/phase/(r['id']+'.json'),item);outputs.append(item);complete(key,group,[item]);event('generation',phase=phase,id=r['id'],contract=check['JSON_contract'],candidates=len(check['candidates']),calculated=sum(x['measurement_completed']for x in check['candidates']),tokens=g['generated_tokens'])
  result={'phase':phase,'loss':sum(x['loss']*x['answer_tokens']for x in ls)/sum(x['answer_tokens']for x in ls),'loss_items':ls,'outputs':outputs,'same_conditions':{'NF4':True,'greedy':True,'thinking':False,'max_new_tokens':2048,'grammar':False,'repairs':0}};write(OUT/(phase+'_evaluation.json'),result);event('evaluation_complete',phase=phase,loss=result['loss']);torch.cuda.empty_cache();return result
 base_result=evaluate('base')
 suffixes=['o_proj','q_proj','k_proj','v_proj','in_proj_a','in_proj_b','in_proj_qkv','in_proj_z','out_proj'];targets=[n for n,m in base.named_modules()if 'language_model'in n and n.rsplit('.',1)[-1]in suffixes and hasattr(m,'weight')];assert len(targets)==304,len(targets)
 cfg=LoraConfig(r=8,lora_alpha=16,lora_dropout=0.05,target_modules=targets,bias='none',task_type='CAUSAL_LM');model=get_peft_model(base,cfg);model.config.use_cache=False;model.config.text_config.use_cache=False;model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False});params=[(n,p)for n,p in model.named_parameters()if p.requires_grad];assert len(params)==608 and all('lora_'in n and 'language_model'in n for n,p in params);ntrain=sum(p.numel()for n,p in params);assert ntrain==23760896
 write(OUT/'LoRA_identity.json',{'start':'original Qwen Base, fresh adapter; Rule adapter never loaded for training','actual_class':type(base).__name__,'targets':targets,'target_suffixes':suffixes,'target_modules':len(targets),'trainable_parameters':ntrain,'LoRA_tensors':len(params),'Base_vision_frozen':True,'rank':8,'alpha':16,'dropout':0.05,'NF4_doublequant':True,'bf16':True,'learning_rate':2e-4,'batch':1,'accumulation':4,'steps':60})
 optimizer=torch.optim.AdamW([p for n,p in params],lr=2e-4);optimizer.zero_grad(set_to_none=True);before={n:p.detach().cpu().clone()for n,p in params};frozen={n:p.detach().flatten()[:16].cpu().clone()for n,p in model.named_parameters()if not p.requires_grad and any(z in n for z in ['embed_tokens.weight','lm_head.weight','language_model.norm.weight'])};schedule=read(TOP/'data_r04/training_schedule.json');byid={r['id']:r for r in train};expo=Counter();cats=Counter();losses=[];trainpin=pin(TOP/'data_r04/train.jsonl');model.train();torch.cuda.reset_peak_memory_stats();trainstart=time.monotonic()
 for step in range(1,61):
  ms=[]
  for micro in range(4):
   s=schedule[(step-1)*4+micro];r=byid[s['id']];assert digest(r)==s['record_sha']and digest(r['target'])==s['answer_sha'];v,n=loss(r);assert torch.isfinite(v);ms.append(float(v.detach()));(v/4).backward();expo[r['id']]+=1;cats[r['category']]+=1
   receipt={'sequence':(step-1)*4+micro,'optimizer_step':step,'microstep':micro+1,'sample_id':r['id'],'family':r['family'],'category':r['category'],'train_file':trainpin,'record_sha':digest(r),'answer_sha':digest(r['target']),'input_sha':digest(r['input']),'source_context':r['source_context'],'source_version':r['input']['source_version'],'loss':ms[-1],'answer_tokens':n,'exposure':expo[r['id']]}
   with (OUT/'consumed_samples.jsonl').open('a')as stream:stream.write(json.dumps(receipt)+'\n')
  assert all(p.grad is None or torch.isfinite(p.grad).all().item()for n,p in params);assert all(p.grad is None for p in model.parameters()if not p.requires_grad);norm=torch.nn.utils.clip_grad_norm_([p for n,p in params],1.0);assert torch.isfinite(norm);optimizer.step();optimizer.zero_grad(set_to_none=True);losses.append(sum(ms)/4);event('optimizer_step',step=step,loss=losses[-1],gradient_norm=float(norm),**mem())
  if step==1:
   changed=sum(not torch.equal(before[n],p.detach().cpu())for n,p in params);unchanged=all(torch.equal(v,dict(model.named_parameters())[n].detach().flatten()[:16].cpu())for n,v in frozen.items());assert changed>0 and unchanged;write(OUT/'first_step_health.json',{'forward_backward':True,'finite_loss_grad':True,'LoRA_tensors_changed':changed,'base_samples_unchanged':unchanged,'base_gradients_none':True,'GPU':mem()});del before
  if step in [1,30,60]:model.save_pretrained(OUT/f'adapter_step_{step:03d}',safe_serialization=True)
 adapter=OUT/'adapter_final';model.save_pretrained(adapter,safe_serialization=True);write(OUT/'training_result.json',{'optimizer_steps':60,'microsteps':240,'training_seconds':time.monotonic()-trainstart,'trainable_parameters':ntrain,'losses':losses,'unique_consumed':len(expo),'exposures':dict(expo),'category_presentations':dict(cats),'fresh_original_Base':True,'GPU':mem(),'adapter_saved':str(adapter),'anomaly_execution':False});event('adapter_saved',steps=60)
 del optimizer;model.eval();base=model.unload();base.gradient_checkpointing_disable();model=PeftModel.from_pretrained(base,adapter,is_trainable=False);model.eval();assert not any(p.requires_grad for p in model.parameters());torch.cuda.empty_cache();new=evaluate('feature_adapter')
 r=evals[0];key=reserve('reload',[r['id']],'saved_feature_adapter',2048);g=gen_batch(model,tok,[r['messages'][:2]],2048)[0];g['id']=r['id'];complete(key,'reload',[g]);write(OUT/'reload_generation.json',{'adapter_saved_reloaded':True,'id':r['id'],**g,'same_as_frozen_output':g['raw_output']==new['outputs'][0]['raw_output']})
 for p in freeze['pins']+code:assert pin(ROOT/p['path'])==p
 write(OUT/'result.json',{'completed':True,'steps':60,'total_seconds':time.monotonic()-START,'base_loss':base_result['loss'],'feature_adapter_loss':new['loss'],'generation_budget':audit(),'SFT_runs':1,'anomaly_execution':False});event('complete')
if __name__=='__main__':
 try:run()
 except Exception as e:
  OUT.mkdir(exist_ok=True);write(OUT/'failure.json',{'error':str(e),'traceback':traceback.format_exc(),'seconds':time.monotonic()-START,'GPU':mem()if torch.cuda.is_initialized()else None});raise
