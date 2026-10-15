"""ONE fresh Base feature SFT, full state checkpoints, same v4/v5 frozen comparison."""
from prepare_v5 import *
from identity_scope_v5 import bind_candidates,scope_check
from inference_v3 import load_model,gen_batch
from budget import reserve,complete,audit
from full_checkpoint import save_checkpoint,load_checkpoint
from peft import get_peft_model,LoraConfig,PeftModel
import torch,torch.nn.functional as F,time,subprocess,traceback,os,sys
OUT=TOP/'training';START=time.monotonic()
CFG={'max_steps':60,'batch':1,'accumulation':4,'rank':8,'alpha':16,'dropout':.05,'lr':2e-4,'max_length':4096,'NF4_double':True,'bf16':True,'scheduler':'none_constant','seed':59,'gradient_checkpointing':'nonreentrant','assistant_EOS_loss_only':True,'max_new_tokens':2048}
def mem():return {'allocated_MiB':round(torch.cuda.memory_allocated()/2**20,1),'peak_MiB':round(torch.cuda.max_memory_allocated()/2**20,1),'reserved_MiB':round(torch.cuda.memory_reserved()/2**20,1)}
def event(kind,**kw):
 with (OUT/'events.jsonl').open('a')as f:f.write(json.dumps({'event':kind,'seconds':round(time.monotonic()-START,2),**kw})+'\n')
 print(json.dumps({'event':kind,**kw}),flush=True)
def rows(p):return [json.loads(s)for s in p.read_text().splitlines()]
def run(resume=None):
 OUT.mkdir(exist_ok=bool(resume));freeze=read(TOP/'evaluation_freeze.json');assert read(TOP/'pretraining_admission_r02.json')['passed']
 for pinx in freeze['pins']:assert pin(ROOT/pinx['path'])==pinx
 gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True)
 write(OUT/('GPU_preflight_resume.json'if resume else 'GPU_preflight.json'),{'GPU':gpu,'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES'),'torch_visible':torch.cuda.device_count(),'bf16':torch.cuda.is_bf16_supported()});assert torch.cuda.device_count()==1
 write(OUT/'configuration.json',CFG)
 if not resume:write(OUT/'execution_freeze.json',{'code':[pin(p)for p in (TOP/'implementation').glob('*.py')],'admission':pin(TOP/'pretraining_admission_r02.json'),'fresh_initializations':1,'maximum_steps':60,'complete_state_checkpoints':True,'Rule_retrained':False})
 torch.manual_seed(CFG['seed']);random.seed(CFG['seed']);import numpy as np;np.random.seed(CFG['seed'])
 base,tok=load_model(None);event('Base_loaded',actual_class=type(base).__name__,**mem())
 train=rows(DATA/'train.jsonl');evals=rows(DATA/'eval_private.jsonl');encoded={}
 for r in train+evals:
  prefix=tok(tok.apply_chat_template(r['messages'][:2],tokenize=False,add_generation_prompt=True,enable_thinking=False),add_special_tokens=False)['input_ids'];ans=tok(r['messages'][2]['content']+tok.eos_token,add_special_tokens=False)['input_ids'];encoded[r['id']]={'prefix':prefix,'answer':ans,'full':prefix+ans};assert len(prefix+ans)<=CFG['max_length']
 write(OUT/'loss_mask_audit.json',{'assistant_EOS_only':True,'input_loss_tokens':0,'truncated_samples':0,'max_actual_length':max(len(e['full'])for e in encoded.values()),'limit':4096})
 model=base
 def loss(r):
  e=encoded[r['id']];ids=torch.tensor([e['full']],device='cuda:0');pos=torch.arange(len(e['prefix'])-1,len(e['full'])-1,device='cuda:0');o=model(input_ids=ids,attention_mask=torch.ones_like(ids),use_cache=False,logits_to_keep=pos,return_dict=True);return F.cross_entropy(o.logits[0].float(),torch.tensor(e['answer'],device=o.logits.device)),len(e['answer'])
 def evaluate(phase):
  model.eval();ls=[];outputs=[]
  with torch.no_grad():
   for r in evals:
    v,n=loss(r);assert torch.isfinite(v);ls.append({'id':r['id'],'loss':float(v),'answer_tokens':n})
  for r in evals:
   c=load_context((ROOT/r['source_context']['path']).parent);assert pin(ROOT/r['source_context']['path'])==r['source_context'];admission(c,'model_input');key=reserve(phase+'_frozen',[r['id']],phase,2048);g=gen_batch(model,tok,[r['messages'][:2]],2048)[0]
   try:
    o=json.loads(g['raw_output']);check=bind_candidates(c,o,phase+'|'+r['id']);scope=scope_check(o,r['private_request_check']);visible={(t['table'],z['name'])for t in r['input']['tables']for z in t['columns']};vr={x['id']for x in r['input']['relationships']}
    for x in check['candidates']:
     q=x['candidate'];x['visible_sources']=set(tuple(z)for z in q.get('sources',{}).get('columns',[]))<=visible and set(q.get('sources',{}).get('relationships',[]))<=vr
     if not x['visible_sources']:x['errors'].append('source not in supplied physical projection')
   except Exception as e:o=None;check={'JSON_contract':False,'candidates':[],'zero':False,'errors':[str(e)]};scope={'passed':False,'errors':[str(e)]}
   item={'id':r['id'],'phase':phase,'source_kind':r['source_scope']['source_kind'],'category':r['category'],'task_kind':r['task_kind'],'input_sha':digest(r['input']),'prediction':o,'checks':check,'request_checks':scope,'direct_meaning_status':'pending_root_review',**g};write(OUT/'responses'/phase/(r['id']+'.json'),item);complete(key,phase+'_frozen',[item]);outputs.append(item);event('generation',phase=phase,id=r['id'],JSON=check['JSON_contract'],candidates=len(check['candidates']),request=scope['passed'],tokens=g['generated_tokens'])
  result={'phase':phase,'loss':sum(x['loss']*x['answer_tokens']for x in ls)/sum(x['answer_tokens']for x in ls),'loss_items':ls,'outputs':outputs,'conditions':{'NF4':True,'greedy':True,'thinking':False,'max_new_tokens':2048,'grammar':False,'repairs':0}};write(OUT/(phase+'_evaluation.json'),result);event('evaluation_complete',phase=phase,loss=result['loss']);return result
 if not resume:
  model=PeftModel.from_pretrained(base,V4/'training/adapter_final',is_trainable=False);v4_result=evaluate('v4');base=model.unload();del model;torch.cuda.empty_cache()
 suffixes=['o_proj','q_proj','k_proj','v_proj','in_proj_a','in_proj_b','in_proj_qkv','in_proj_z','out_proj'];targets=[n for n,m in base.named_modules()if 'language_model'in n and n.rsplit('.',1)[-1]in suffixes and hasattr(m,'weight')];assert len(targets)==304
 if resume:model=PeftModel.from_pretrained(base,Path(resume)/'adapter',is_trainable=True)
 else:model=get_peft_model(base,LoraConfig(r=8,lora_alpha=16,lora_dropout=.05,target_modules=targets,bias='none',task_type='CAUSAL_LM'))
 model.config.use_cache=False;model.config.text_config.use_cache=False;model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False});params=[(n,p)for n,p in model.named_parameters()if p.requires_grad];assert len(params)==608 and all('lora_'in n and 'language_model'in n for n,p in params);ntrain=sum(p.numel()for n,p in params);assert ntrain==23760896
 write(OUT/'LoRA_identity.json',{'actual_class':type(base).__name__,'targets':targets,'target_suffixes':suffixes,'target_modules':304,'LoRA_tensors':608,'trainable_parameters':ntrain,'vision_Base_frozen':True,'fresh_Base_not_v4_continuation':True,'v4_unloaded_without_merging':True,'rank':8,'alpha':16,'dropout':.05})
 optimizer=torch.optim.AdamW([p for n,p in params],lr=CFG['lr']);optimizer.zero_grad(set_to_none=True);schedule=read(DATA/'training_schedule.json');byid={r['id']:r for r in train};trainpin=pin(DATA/'train.jsonl');identity={'dataset_sha':trainpin['sha256'],'configuration_sha':digest(CFG),'schedule_sha':digest(schedule)};expo=Counter();cats=Counter();losses=[];startstep=0;prior_seconds=0.
 if resume:
  s=load_checkpoint(resume,model,optimizer,identity);assert s['sample_order']==schedule;startstep=s['step'];assert s['sampler_cursor']==startstep*4;expo=Counter(s['exposures']);cats=Counter(s['category_presentations']);losses=s['losses'];prior_seconds=s['training_seconds'];event('complete_state_restored',step=startstep,cursor=s['sampler_cursor'],exposures=sum(expo.values()),optimizer_RNG_restored=True)
 before={n:p.detach().cpu().clone()for n,p in params}if startstep==0 else None;frozen={n:p.detach().flatten()[:16].cpu().clone()for n,p in model.named_parameters()if not p.requires_grad and any(z in n for z in ['embed_tokens.weight','lm_head.weight','language_model.norm.weight'])};model.train();torch.cuda.reset_peak_memory_stats();trainstart=time.monotonic()
 for step in range(startstep+1,61):
  ms=[]
  for micro in range(4):
   seq=(step-1)*4+micro;s=schedule[seq];r=byid[s['id']];assert digest(r)==s['record_sha']and digest(r['target'])==s['answer_sha'];v,n=loss(r);assert torch.isfinite(v);ms.append(float(v.detach()));(v/4).backward();expo[r['id']]+=1;cats[r['category']]+=1
   receipt={'sequence':seq,'optimizer_step':step,'microstep':micro+1,'sample_id':r['id'],'family':r['family'],'category':r['category'],'task_kind':r['task_kind'],'train_file':trainpin,'record_sha':digest(r),'answer_sha':digest(r['target']),'input_sha':digest(r['input']),'source_context':r['source_context'],'loss':ms[-1],'answer_tokens':n,'exposure':expo[r['id']]}
   with (OUT/'consumed_samples.jsonl').open('a')as stream:stream.write(json.dumps(receipt)+'\n')
  assert all(p.grad is None or torch.isfinite(p.grad).all().item()for n,p in params);assert all(p.grad is None for p in model.parameters()if not p.requires_grad);norm=torch.nn.utils.clip_grad_norm_([p for n,p in params],1.0);assert torch.isfinite(norm);optimizer.step();optimizer.zero_grad(set_to_none=True);losses.append(sum(ms)/4);event('optimizer_step',step=step,loss=losses[-1],gradient_norm=float(norm),**mem())
  if step==1:
   changed=sum(not torch.equal(before[n],p.detach().cpu())for n,p in params);unchanged=all(torch.equal(v,dict(model.named_parameters())[n].detach().flatten()[:16].cpu())for n,v in frozen.items());assert changed>0 and unchanged;write(OUT/'first_step_health.json',{'forward_backward':True,'finite_loss_gradient':True,'LoRA_tensors_changed':changed,'Base_sample_values_unchanged':unchanged,'Base_gradients_none':True,'GPU':mem()});del before
  if step==1 or step%5==0:
   state={**identity,'step':step,'sampler_cursor':step*4,'sample_order':schedule,'exposures':dict(expo),'category_presentations':dict(cats),'losses':losses,'training_seconds':prior_seconds+time.monotonic()-trainstart};p=save_checkpoint(OUT/'checkpoints'/f'step_{step:03d}',model,optimizer,state);event('complete_checkpoint_saved',step=step,path=str(p))
 adapter=OUT/'adapter_final';model.save_pretrained(adapter,safe_serialization=True);write(OUT/'training_result.json',{'optimizer_steps':60,'retained_weight_updates':60,'microsteps':240,'unique_consumed':len(expo),'exposures':dict(expo),'category_presentations':dict(cats),'task_presentations':dict(Counter(byid[x]['task_kind']for x in [s['id']for s in schedule])),'training_seconds':prior_seconds+time.monotonic()-trainstart,'losses':losses,'trainable_parameters':ntrain,'fresh_Base':True,'resume_from_step':startstep,'complete_state_checkpoint':True,'GPU':mem()});event('adapter_saved',steps=60)
 del optimizer;model.eval();base=model.unload();base.gradient_checkpointing_disable();model=PeftModel.from_pretrained(base,adapter,is_trainable=False);model.eval();assert not any(p.requires_grad for p in model.parameters());torch.cuda.empty_cache();v5_result=evaluate('v5')
 r=evals[0];key=reserve('adapter_reload',[r['id']],'saved_v5',2048);g=gen_batch(model,tok,[r['messages'][:2]],2048)[0];g['id']=r['id'];complete(key,'adapter_reload',[g]);write(OUT/'reload_generation.json',{'adapter_saved_and_reloaded':True,'same_as_first_frozen':g['raw_output']==v5_result['outputs'][0]['raw_output'],**g})
 for pinx in freeze['pins']:assert pin(ROOT/pinx['path'])==pinx
 write(OUT/'result.json',{'completed':True,'steps':60,'retained_weight_updates':60,'SFT_trials':1,'generation_budget':audit(),'total_seconds':time.monotonic()-START,'v4_loss':read(OUT/'v4_evaluation.json')['loss'],'v5_loss':v5_result['loss'],'no_Rule_or_anomaly_training':True});event('complete')
if __name__=='__main__':
 try:run(sys.argv[1]if len(sys.argv)>1 else None)
 except Exception as e:
  OUT.mkdir(exist_ok=True);event('failure',error=repr(e),traceback=traceback.format_exc());raise
