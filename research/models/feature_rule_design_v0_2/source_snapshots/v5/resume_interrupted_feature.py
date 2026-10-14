"""Continue ONE interrupted experiment from saved step1, cumulative cap60.
Six executed updates had no checkpoint and are lost. AdamW state was not saved.
No repeat Base evaluation, no fresh adapter initialization, no hidden reset of cap.
"""
from train_feature_once import *
from peft import PeftModel
START=time.monotonic()
def event(kind,**kw):
 with (OUT/'events_resume.jsonl').open('a')as f:f.write(json.dumps({'event':kind,'seconds':round(time.monotonic()-START,2),**kw})+'\n')
 print(json.dumps({'event':kind,**kw}),flush=True)
def resume():
 assert not(OUT/'resume_started.json').exists(),'one controlled continuation only'
 events=[json.loads(s)for s in (OUT/'events.jsonl').read_text().splitlines()];oldsteps=[e for e in events if e['event']=='optimizer_step'];assert len(oldsteps)==7;checkpoint=OUT/'adapter_step_001';assert checkpoint.exists()and not(OUT/'adapter_final').exists()
 freeze=read(TOP/'evaluation_freeze.json');oldfreeze=read(OUT/'execution_freeze.json')
 for p in freeze['pins']+oldfreeze['code']:assert pin(ROOT/p['path'])==p
 write(OUT/'interruption_r01.json',{'completed_optimizer_steps':7,'last_checkpoint_step':1,'executed_updates_not_saved':6,'optimizer_state_not_saved':True,'one_partial_microstep_consumed':True,'old_execution_log':pin(OUT/'events.jsonl'),'old_consumption_log':pin(OUT/'consumed_samples.jsonl'),'resume_policy':'load step1 weights, reset unavailable AdamW moments; execute only cumulative steps8..60; retained adapter has54 weight updates. No repeated steps2..7 and no fresh experiment.','Base_evaluation_not_repeated':True,'global_SFT_optimizer_cap':60})
 write(OUT/'resume_started.json',{'checkpoint':pin(checkpoint/'adapter_model.safetensors'),'additional_optimizer_steps':53,'cumulative_executed_max':60,'retained_weight_updates_after_completion':54,'fresh_adapter_initializations_total':1,'new_code':pin(Path(__file__))})
 write(OUT/'GPU_preflight_resume.json',{'query':subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True),'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES'),'torch_visible':torch.cuda.device_count()});torch.manual_seed(42);base,tok=load_model(None);model=PeftModel.from_pretrained(base,checkpoint,is_trainable=True);model.config.use_cache=False;model.config.text_config.use_cache=False;model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False});params=[(n,p)for n,p in model.named_parameters()if p.requires_grad];assert len(params)==608 and sum(p.numel()for n,p in params)==23760896
 train=rows(TOP/'data_r04/train.jsonl');evals=rows(TOP/'data_r04/eval_private.jsonl');encoded={};schedule=read(TOP/'data_r04/training_schedule.json')
 for r in train+evals:
  prefix=tok(tok.apply_chat_template(r['messages'][:2],tokenize=False,add_generation_prompt=True,enable_thinking=False),add_special_tokens=False)['input_ids'];answer=tok(r['messages'][2]['content']+tok.eos_token,add_special_tokens=False)['input_ids'];encoded[r['id']]={'prefix':prefix,'answer':answer,'full':prefix+answer};assert len(prefix+answer)<=4096
 def loss(r):
  e=encoded[r['id']];ids=torch.tensor([e['full']],device='cuda:0');pos=torch.arange(len(e['prefix'])-1,len(e['full'])-1,device='cuda:0');o=model(input_ids=ids,attention_mask=torch.ones_like(ids),use_cache=False,logits_to_keep=pos,return_dict=True);return F.cross_entropy(o.logits[0].float(),torch.tensor(e['answer'],device=o.logits.device)),len(e['answer'])
 optimizer=torch.optim.AdamW([p for n,p in params],lr=2e-4);optimizer.zero_grad(set_to_none=True);before={n:p.detach().cpu().clone()for n,p in params};frozen={n:p.detach().flatten()[:16].cpu().clone()for n,p in model.named_parameters()if not p.requires_grad and any(z in n for z in ['embed_tokens.weight','lm_head.weight','language_model.norm.weight'])};byid={r['id']:r for r in train};prior=rows(OUT/'consumed_samples.jsonl');expo=Counter(x['sample_id']for x in prior);cats=Counter(x['category']for x in prior);effective=Counter(x['sample_id']for x in prior[:4]);losses=[x['loss']for x in oldsteps];trainpin=pin(TOP/'data_r04/train.jsonl');model.train();torch.cuda.reset_peak_memory_stats();trainstart=time.monotonic()
 for step in range(8,61):
  ms=[]
  for micro in range(4):
   s=schedule[(step-1)*4+micro];r=byid[s['id']];assert digest(r)==s['record_sha']and digest(r['target'])==s['answer_sha'];v,n=loss(r);assert torch.isfinite(v);ms.append(float(v.detach()));(v/4).backward();expo[r['id']]+=1;cats[r['category']]+=1;effective[r['id']]+=1
   receipt={'sequence':(step-1)*4+micro,'optimizer_step':step,'microstep':micro+1,'sample_id':r['id'],'family':r['family'],'category':r['category'],'train_file':trainpin,'record_sha':digest(r),'answer_sha':digest(r['target']),'input_sha':digest(r['input']),'source_context':r['source_context'],'source_version':r['input']['source_version'],'loss':ms[-1],'answer_tokens':n,'exposure_including_interrupted':expo[r['id']],'continuation':True}
   with (OUT/'consumed_samples_resume.jsonl').open('a')as stream:stream.write(json.dumps(receipt)+'\n')
  assert all(p.grad is None or torch.isfinite(p.grad).all().item()for n,p in params);assert all(p.grad is None for p in model.parameters()if not p.requires_grad);norm=torch.nn.utils.clip_grad_norm_([p for n,p in params],1.0);assert torch.isfinite(norm);optimizer.step();optimizer.zero_grad(set_to_none=True);losses.append(sum(ms)/4);event('optimizer_step',step=step,loss=losses[-1],gradient_norm=float(norm),cumulative_executed_steps=step,retained_weight_updates=step-6,**mem())
  if step==8:
   changed=sum(not torch.equal(before[n],p.detach().cpu())for n,p in params);unchanged=all(torch.equal(v,dict(model.named_parameters())[n].detach().flatten()[:16].cpu())for n,v in frozen.items());assert changed>0 and unchanged;write(OUT/'resume_first_step_health.json',{'finite_loss_grad':True,'LoRA_tensors_changed':changed,'Base_samples_unchanged':True,'Base_gradients_none':True,'GPU':mem()});del before
  if step%5==0 or step==60:
   ck=OUT/f'resumed_adapter_step_{step:03d}';model.save_pretrained(ck,safe_serialization=True);torch.save({'optimizer':optimizer.state_dict(),'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state(),'cumulative_step':step,'retained_updates':step-6},ck/'optimizer_state.pt')
 adapter=OUT/'adapter_final';model.save_pretrained(adapter,safe_serialization=True);write(OUT/'training_result.json',{'optimizer_steps':60,'optimizer_steps_initial':7,'optimizer_steps_continuation':53,'retained_weight_updates':54,'unsaved_updates_lost':6,'AdamW_state_reset_after_external_interruption':True,'microsteps':240,'microstep_receipts_including_partial':len(prior)+212,'training_seconds':sum(oldsteps[i]['seconds']-(oldsteps[i-1]['seconds']if i else 1001.92)for i in range(7))+time.monotonic()-trainstart,'trainable_parameters':23760896,'losses':losses,'unique_consumed':len(expo),'unique_with_retained_updates':len(effective),'exposures':dict(expo),'effective_exposures_after_checkpoint':dict(effective),'category_presentations':dict(cats),'fresh_original_Base':True,'fresh_adapter_initializations':1,'GPU':mem(),'adapter_saved':str(adapter),'anomaly_execution':False});event('adapter_saved',cumulative_steps=60,retained_weight_updates=54)
 del optimizer;model.eval();base=model.unload();base.gradient_checkpointing_disable();model=PeftModel.from_pretrained(base,adapter,is_trainable=False);model.eval();torch.cuda.empty_cache();ls=[];outputs=[]
 with torch.no_grad():
  for r in evals:v,n=loss(r);assert torch.isfinite(v);ls.append({'id':r['id'],'loss':float(v),'answer_tokens':n})
 for r in evals:
  c=load_context((ROOT/r['source_context']['path']).parent);admission(c,'model_input');key=reserve('frozen_feature_adapter',[r['id']],'feature_adapter',2048);g=gen_batch(model,tok,[r['messages'][:2]],2048)[0]
  try:
   o=json.loads(g['raw_output']);check=evaluate_candidates(c,o);visible={(t['table'],z['name'])for t in r['input']['tables']for z in t['columns']};vr={z['id']for z in r['input']['relationships']}
   for x in check['candidates']:q=x['candidate'];x['visible_sources']=set(tuple(z)for z in q.get('sources',{}).get('columns',[]))<=visible and set(q.get('sources',{}).get('relationships',[]))<=vr
  except Exception as e:o=None;check={'JSON_contract':False,'candidates':[],'zero':False,'errors':[str(e)]}
  item={'id':r['id'],'phase':'feature_adapter','source_kind':r['source_scope']['source_kind'],'category':r['category'],'input_sha':digest(r['input']),'prediction':o,'checks':check,'direct_meaning_status':'pending_root_review',**g};write(OUT/'responses/feature_adapter'/(r['id']+'.json'),item);outputs.append(item);complete(key,'frozen_feature_adapter',[item]);event('generation',id=r['id'],candidates=len(check['candidates']),calculated=sum(x['measurement_completed']for x in check['candidates']),tokens=g['generated_tokens'])
 eloss=sum(x['loss']*x['answer_tokens']for x in ls)/sum(x['answer_tokens']for x in ls);write(OUT/'feature_adapter_evaluation.json',{'phase':'feature_adapter','loss':eloss,'loss_items':ls,'outputs':outputs,'same_conditions':{'NF4':True,'greedy':True,'thinking':False,'max_new_tokens':2048,'grammar':False,'repairs':0}})
 r=evals[0];key=reserve('reload',[r['id']],'saved_feature_adapter',2048);g=gen_batch(model,tok,[r['messages'][:2]],2048)[0];g['id']=r['id'];complete(key,'reload',[g]);write(OUT/'reload_generation.json',{'adapter_saved_reloaded':True,'id':r['id'],**g,'same_as_frozen_output':g['raw_output']==outputs[0]['raw_output']})
 for p in freeze['pins']+oldfreeze['code']:assert pin(ROOT/p['path'])==p
 write(OUT/'result.json',{'completed':True,'steps':60,'retained_weight_updates':54,'total_seconds':events[-1]['seconds']+time.monotonic()-START,'base_loss':read(OUT/'base_evaluation.json')['loss'],'feature_adapter_loss':eloss,'generation_budget':audit(),'SFT_runs':1,'continuation_after_interruption':True,'new_trials':0,'anomaly_execution':False});event('complete')
if __name__=='__main__':
 try:resume()
 except Exception as e:write(OUT/'resume_failure.json',{'error':str(e),'traceback':traceback.format_exc(),'GPU':mem()if torch.cuda.is_initialized()else None});raise
