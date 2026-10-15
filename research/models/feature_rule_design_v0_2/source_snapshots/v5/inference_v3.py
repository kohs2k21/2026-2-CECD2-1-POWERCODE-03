"""Frozen adapter inference only. No optimizer, no output correction."""
from policy_v3 import *
from budget import reserve,complete,audit
import os,time,subprocess,traceback,sys
import torch
from transformers import AutoTokenizer,BitsAndBytesConfig,Qwen3_5ForConditionalGeneration
from peft import PeftModel
from contextlib import nullcontext

def load_model(adapter):
 assert torch.cuda.device_count()==1 and torch.cuda.is_bf16_supported()
 mf=read(ROOT/'local_model/model_manifest.json');tok=AutoTokenizer.from_pretrained(mf['snapshot_path'],local_files_only=True)
 quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=torch.bfloat16)
 base=Qwen3_5ForConditionalGeneration.from_pretrained(mf['snapshot_path'],local_files_only=True,quantization_config=quant,dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa',low_cpu_mem_usage=True)
 for name,p in base.named_parameters():
  p.requires_grad_(False)
  if p.ndim==1 and 'norm'in name:p.data=p.data.to(torch.float32)
 model=PeftModel.from_pretrained(base,ROOT/adapter,is_trainable=False)if adapter else base;model.eval();assert not any(p.requires_grad for p in model.parameters());return model,tok

def gen_batch(model,tok,messages,max_tokens,base=False):
 ps=[tok(tok.apply_chat_template(m,tokenize=False,add_generation_prompt=True,enable_thinking=False),add_special_tokens=False)['input_ids']for m in messages];w=max(map(len,ps));assert w+max_tokens<=16384,'no_truncation_context_limit'
 ids=torch.tensor([[tok.eos_token_id]*(w-len(p))+p for p in ps],device='cuda:0');mask=torch.tensor([[0]*(w-len(p))+[1]*len(p)for p in ps],device='cuda:0');start=time.monotonic()
 with model.disable_adapter()if base and hasattr(model,'disable_adapter')else nullcontext():
  with torch.no_grad():g=model.generate(input_ids=ids,attention_mask=mask,max_new_tokens=max_tokens,do_sample=False,use_cache=True,logits_to_keep=1,pad_token_id=tok.eos_token_id)
 result=[]
 for p,s in zip(ps,g):
  tail=s[w:];eos=(tail==tok.eos_token_id).nonzero().flatten();n=int(eos[0])+1 if len(eos)else len(tail);raw=tok.decode(tail,skip_special_tokens=True)
  result.append({'raw_output':raw,'raw_sha':digest(raw),'input_tokens':len(p),'generated_tokens':n,'length_limit_reached':not len(eos)and n==max_tokens,'batch_seconds':time.monotonic()-start})
 return result

def admission_record(r):
 e=r['evidence'];bp=e['application_bundle'];assert pin(ROOT/bp['path'])==bp
 b=read(ROOT/bp['path']);rp=e.get('reference_bundle');ref=read(ROOT/rp['path'])if rp else None
 if rp:assert pin(ROOT/rp['path'])==rp
 v=admit_input(r['input'],b,ref);assert v['passed'],v;return v

def run(phase):
 out=TOP/'model_comparison'/phase;out.mkdir(parents=True);started=time.monotonic();freeze=read(TOP/'evaluation_freeze.json')
 for p in freeze['pins']:assert pin(ROOT/p['path'])==p
 records=rows(TOP/'data/frozen40.jsonl');assert len(records)==40
 if phase=='v2':records+=rows(TOP/'data/HR_remaining8.jsonl')
 assert phase in ['v1','v2'];mf=read(PARENT/'model_manifest.json');adapter=mf['current_adapter'if phase=='v1'else 'new_adapter']
 admissions={r['id']:admission_record(r)for r in records}
 gpu=subprocess.check_output(['nvidia-smi','--query-gpu=index,name,memory.used,memory.total,utilization.gpu','--format=csv,noheader'],text=True)
 write(out/'execution_freeze.json',{'pins':freeze['pins'],'adapter':pin(ROOT/adapter/'adapter_model.safetensors'),'GPU_preflight':gpu,'CUDA_VISIBLE_DEVICES':os.getenv('CUDA_VISIBLE_DEVICES'),'source_admission':admissions,'SFT':False,'model_evaluation':'corrected development reevaluation'})
 torch.manual_seed(42);model,tok=load_model(adapter);outputs=[]
 for offset in range(0,len(records),2):
  batch=records[offset:offset+2];group='HR_remaining'if offset>=40 else 'evaluation_'+phase;key=reserve(group,[r['id']for r in batch],adapter,512)
  messages=[r['messages'][:2]for r in batch];generated=gen_batch(model,tok,messages,512);returned=[]
  for r,g in zip(batch,generated):
   item={'id':r['id'],'phase':phase,'category':r['category'],'source_kind':r['source_kind'],'input_sha':digest(r['input']),'source_context_admission':admissions[r['id']],**g,**assess(r['input'],g['raw_output'],r.get('values')),'direct_meaning_status':'pending_root_review'}
   write(out/'responses'/(r['id']+'.json'),item);returned.append(item);outputs.append(item)
   print(json.dumps({'phase':phase,'id':r['id'],'code_pass':item['validation']['passed'],'tokens':g['generated_tokens']}),flush=True)
  complete(key,group,returned)
 for p in freeze['pins']:assert pin(ROOT/p['path'])==p
 write(out/'results.json',{'completed':True,'outputs':outputs,'seconds':time.monotonic()-started,'peak_GPU_MiB':round(torch.cuda.max_memory_allocated()/2**20,1),'generation_budget':audit(),'frozen_repairs':0,'optimizer_steps':0});print(json.dumps({'phase':phase,'done':len(outputs),'seconds':time.monotonic()-started}),flush=True)

if __name__=='__main__':
 try:run(sys.argv[1])
 except Exception as e:write(TOP/'model_comparison'/sys.argv[1]/'failure.json',{'error':str(e),'traceback':traceback.format_exc()});raise
