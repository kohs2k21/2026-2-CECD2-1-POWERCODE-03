"""Portable optional LoRA evidence selection. Never fit or auto-download a Base.

Loading/generation matches the frozen text-only Qwen3.6-27B pilot recipe. The
publication was checked on CPU; this portable wrapper has not been GPU-rerun.
"""
import argparse
import json
import os
from pathlib import Path
import time

from xai_runtime.artifacts import digest,read,require,sha,write
from xai_runtime.evidence_selection import selection_messages,selection_schema,validate_selection

ROOT=Path(__file__).resolve().parent


def verify_adapter(adapter):
    adapter=Path(adapter)
    card=read(ROOT/'models/model_manifest.json')
    require(sha(adapter/'adapter_model.safetensors')==card['adapter']['weights_sha256'],'adapter_weights_changed')
    require(sha(adapter/'adapter_config.json')==card['adapter']['published_config_sha256'],'adapter_config_changed')
    return card


def verify_base(base):
    base=Path(base)
    manifest=read(ROOT/'models/base_files.json')
    for pin in manifest['files']:
        path=base/pin['name']
        require(path.is_file() and sha(path)==pin['sha256'],'pinned_base_mismatch:'+pin['name'])
    return manifest


def generate(view,base,adapter,timeout=90):
    # No host/device manipulation: caller selects an assigned visible GPU.
    require(bool(os.environ.get('CUDA_VISIBLE_DEVICES')) and ',' not in os.environ['CUDA_VISIBLE_DEVICES'],
            'select_exactly_one_assigned_visible_GPU')
    card=verify_adapter(adapter);identity=verify_base(base)
    import torch
    from transformers import (AutoConfig,AutoTokenizer,BitsAndBytesConfig,Qwen3_5ForConditionalGeneration,
                              StoppingCriteria,StoppingCriteriaList,LogitsProcessorList)
    from peft import PeftModel
    import xgrammar
    from xgrammar.contrib.hf import LogitsProcessor
    require(torch.cuda.device_count()==1 and torch.cuda.is_bf16_supported(),'one_BF16_GPU_required')
    free,_=torch.cuda.mem_get_info(0)
    require(free>=20_000_000_000,'assigned_GPU_free_memory_below_pilot_load_requirement')
    config=AutoConfig.from_pretrained(base,local_files_only=True)
    require(config.architectures==['Qwen3_5ForConditionalGeneration'],'unexpected_base_architecture')
    tokenizer=AutoTokenizer.from_pretrained(base,local_files_only=True)
    messages,schema=selection_messages(view),selection_schema(view)
    text=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
    ids=tokenizer(text,add_special_tokens=False)['input_ids']
    require(len(ids)<=3500,'selection_input_exceeds_validated_budget_no_truncation')
    compiler=xgrammar.GrammarCompiler(xgrammar.TokenizerInfo.from_huggingface(tokenizer,
        vocab_size=config.text_config.vocab_size),max_threads=4)
    grammar=compiler.compile_json_schema(schema)
    quant=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,
                            bnb_4bit_compute_dtype=torch.bfloat16)
    model=Qwen3_5ForConditionalGeneration.from_pretrained(base,local_files_only=True,
        quantization_config=quant,dtype=torch.bfloat16,device_map={'':0},attn_implementation='sdpa',low_cpu_mem_usage=True)
    for name,param in model.named_parameters():
        param.requires_grad_(False)
        if param.ndim==1 and 'norm' in name:param.data=param.data.float()
    emb=model.model.language_model.embed_tokens
    emb.to('cpu')
    emb.register_forward_pre_hook(lambda module,args:tuple(x.to('cpu') if torch.is_tensor(x) else x for x in args))
    emb.register_forward_hook(lambda module,args,out:out.to('cuda:0'))
    model=PeftModel.from_pretrained(model,str(adapter),is_trainable=False,local_files_only=True)
    model.eval()
    class Deadline(StoppingCriteria):
        def __init__(self):self.end=time.perf_counter()+timeout;self.hit=False
        def __call__(self,*args,**kwargs):
            self.hit=time.perf_counter()>=self.end
            return self.hit
    encoded=tokenizer(text,return_tensors='pt',add_special_tokens=False).to('cuda:0')
    deadline=Deadline();started=time.perf_counter()
    with torch.inference_mode():
        answer=model.generate(**encoded,max_new_tokens=512,do_sample=False,use_cache=True,
            logits_processor=LogitsProcessorList([LogitsProcessor(grammar)]),
            stopping_criteria=StoppingCriteriaList([deadline]),
            eos_token_id=tokenizer.eos_token_id,pad_token_id=tokenizer.pad_token_id)
    torch.cuda.synchronize()
    require(not deadline.hit,'selection_generation_timeout')
    raw=tokenizer.decode(answer[0,encoded['input_ids'].shape[1]:],skip_special_tokens=True)
    plan=json.loads(raw);validate_selection(view,plan)
    return dict(selection=plan,input_sha256=digest(view),raw=raw,input_tokens=len(ids),
        generation_seconds=time.perf_counter()-started,base=dict(repo_id=identity['repo_id'],revision=identity['revision']),
        adapter_sha256=card['adapter']['weights_sha256'],new_fit=False,new_training=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--view',required=True);p.add_argument('--output',required=True)
    p.add_argument('--base-dir',required=True);p.add_argument('--adapter',default=str(ROOT/'models/xai_summary_lora_v1'))
    a=p.parse_args();write(a.output,generate(read(a.view),a.base_dir,a.adapter))
