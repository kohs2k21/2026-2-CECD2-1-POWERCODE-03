"""Bounded Base-Qwen pilot on previously unused saved ESB transactions.

All evidence remains archived and is presented once across bounded chunks.
The new chunk contract is deliberately separate from the final plan contract.
No detector fit/score or LoRA training takes place.
"""
from collections import Counter,defaultdict
from copy import deepcopy
import argparse,gzip,json,sys,time
from pathlib import Path
from jsonschema import Draft202012Validator,ValidationError
from .artifacts import PROJECT,XAI,DESIGN,RELEASE,InputError,canonical,digest,read,write,sha,seal,require
from .engine import SCHEMAS,default_plan,render,validate_packet,validate_plan

BASE=PROJECT/'.cache/huggingface/hub/models--Qwen--Qwen3.6-27B/snapshots/6a9e13bd6fc8f0983b9b99948120bc37f49c13e9'
PACKAGE=RELEASE/'vendor/numeric/research/models/feature_rule_spec'
SOURCE=PROJECT/'data/eda/final_hybrid_model_delivery/20261111T153800_597984+0900/verification/esb_actual_completed'
SYSTEM=(DESIGN/'prompts/xai_system_ko.txt').read_text()+'''\n이번 입력은 xai-chunk-evidence/1.0.0 분할 근거다. parent_packet_id를 packet_id로 복사한다.
이 분할의 모든 required=true 근거만 필수다. 다른 분할의 근거 ID는 참조하지 않는다.
출력 schema_version은 xai-chunk-plan/1.0.0이다. 필수가 없고 유용한 참고 근거가 없으면 items=[]도 허용한다.
전체 거래의 판정과 심각도는 immutable_context에 있으며 이 분할로 재판정하지 않는다.
새 숫자나 설명 문장은 출력하지 않는다. 최종 거래 설명은 코드가 모든 분할을 모아 검증한다.'''
CHUNK_SCHEMA=deepcopy(SCHEMAS['explanation_plan'].schema)
CHUNK_SCHEMA['properties']['schema_version']={'const':'xai-chunk-plan/1.0.0'}
CHUNK_SCHEMA['properties']['items']['minItems']=0
CHUNK_VALIDATOR=Draft202012Validator(CHUNK_SCHEMA)

def messages(view):
    return [{'role':'system','content':SYSTEM},{'role':'user','content':canonical(view)}]

def token_count(tokenizer,view):
    text=tokenizer.apply_chat_template(messages(view),tokenize=False,add_generation_prompt=True,enable_thinking=False)
    return len(tokenizer(text,add_special_tokens=False)['input_ids'])

def view_for(packet,facts,index,total):
    used={r for f in facts for r in f['source_refs']}
    return dict(contract='xai-chunk-evidence/1.0.0',parent_packet_id=packet['packet_id'],parent_packet_sha256=digest(packet),
      chunk=dict(index=index,count=total),immutable_context=dict(scope=packet['scope'],
        decision={k:packet['decision'][k] for k in ['state','policy','detector_version']},
        severity={k:packet['severity'][k] for k in ['status','level','label','policy_version']},provenance=packet['provenance']),
      facts=deepcopy(facts),source_refs=[deepcopy(s) for s in packet['source_refs'] if s['source_id'] in used],allowed_checks=packet['allowed_checks'])

def split_packet(packet,tokenizer,max_facts=8,max_input=6144):
    validate_packet(packet)
    groups=[packet['facts'][i:i+max_facts] for i in range(0,len(packet['facts']),max_facts)]
    # Rebuild after splitting so actual indices/count are token-checked too.
    while True:
        changed=False;next_groups=[]
        for i,facts in enumerate(groups):
            if token_count(tokenizer,view_for(packet,facts,i,len(groups)))>max_input:
                require(len(facts)>1,'single_evidence_over_token_budget')
                mid=len(facts)//2;next_groups.extend([facts[:mid],facts[mid:]]);changed=True
            else:next_groups.append(facts)
        groups=next_groups
        if not changed:break
    views=[view_for(packet,g,i,len(groups)) for i,g in enumerate(groups)]
    require([f for v in views for f in v['facts']]==packet['facts'],'chunk_evidence_changed_or_omitted')
    return views

def validate_chunk(view,plan):
    CHUNK_VALIDATOR.validate(plan)
    require(plan['packet_id']==view['parent_packet_id'],'cross_packet_plan')
    facts={f['id']:f for f in view['facts']};ids=[x['evidence_id'] for x in plan['items']]
    require(len(set(ids))==len(ids) and set(ids)<=set(facts),'unknown_duplicate_or_cross_chunk_fact')
    require({f['id'] for f in facts.values() if f['required']}<=set(ids),'required_fact_omitted')
    require(all(x['template_id']==facts[x['evidence_id']]['kind'] for x in plan['items']),'wrong_template')
    require(set(plan['next_check_codes'])<=set(view['allowed_checks']),'disallowed_check')

def merge_plans(packet,views,plans):
    require(len(views)==len(plans),'missing_chunk_response')
    items=[];checks=[]
    for view,plan in zip(views,plans):
        validate_chunk(view,plan);items.extend(plan['items']);checks.extend(plan['next_check_codes'])
    combined=dict(schema_version='xai-plan/1.0.0',packet_id=packet['packet_id'],items=items,
      next_check_codes=list(dict.fromkeys(checks))[:3])
    validate_plan(packet,combined)
    return combined

def prepare(out):
    from .saved_esb import SavedESB
    from transformers import AutoTokenizer
    require(not (out/'plan.json').exists(),'preparation_already_exists')
    excluded=set();old_files=[]
    for f in sorted((PROJECT/'data/eda/xai_grounded_baseline').glob('*/evidence_packets.jsonl.gz')):
        old_files.append(dict(path=str(f),sha256=sha(f)))
        with gzip.open(f,'rt') as stream:
            excluded.update(json.loads(line)['scope']['transaction_ref'] for line in stream if line.strip())
    source=SavedESB(SOURCE)
    groups=defaultdict(list)
    for row in source.decisions:
        # Same source namespace, transaction ID; physical T row only for missing ID.
        key=row['transaction_id'] or row['entity_id']
        if key not in excluded:
            groups[tuple(row[k] for k in ['logical_SR','numeric_SR','ML','combined'])].append(row)
    selected=[]
    for key in sorted(groups):
        candidates=sorted(groups[key],key=lambda r:digest(dict(namespace='inzent-esb-source',transaction=r['transaction_id'] or r['entity_id'],selection_seed=42)))
        selected.extend(candidates[:2])
    require(len(selected)>0,'no_new_transaction_groups')
    require(len({r['transaction_id'] or r['entity_id'] for r in selected})==len(selected),'duplicate_transaction_group')
    source.prepare(selected)
    tokenizer=AutoTokenizer.from_pretrained(BASE,local_files_only=True)
    cases=[];jobs=[];diagnostic=[];totalfacts=0
    for index,row in enumerate(selected):
        name=f'case_{index:02d}';folder=out/'cases'/name;folder.mkdir(parents=True,exist_ok=False)
        packet,audit=source.packet(row);validate_packet(packet)
        require(packet['scope']['transaction_ref'] not in excluded,'development_group_overlap')
        views=split_packet(packet,tokenizer);totalfacts+=len(packet['facts'])
        write(folder/'packet.json',packet);write(folder/'packet_audit.json',audit);write(folder/'baseline_plan.json',default_plan(packet));write(folder/'baseline_result.json',render(packet))
        for j,v in enumerate(views):
            file=folder/f'chunk_{j:03d}.json';write(file,v)
            job=dict(id=f'{name}_chunk_{j:03d}',case=name,chunk=j,input=str(file.relative_to(out)),input_sha256=sha(file),input_tokens=token_count(tokenizer,v),deadline=30,arm='primary_30s')
            jobs.append(job)
        pattern=[row[k] for k in ['logical_SR','numeric_SR','ML','combined']]
        cases.append(dict(id=name,entity_id=row['entity_id'],transaction_group_sha256=digest(dict(namespace='inzent-esb-source',transaction=packet['scope']['transaction_ref'])),pattern=pattern,
          packet_sha256=digest(packet),facts=len(packet['facts']),required=sum(f['required'] for f in packet['facts']),chunks=len(views)))
        # Exactly one preselected case per state group, independent of model outcomes.
        if index%2==0:
            j=next(i for i,v in enumerate(views) if any(f['required'] for f in v['facts']))
            diagnostic.append(dict(jobs[-len(views)+j],id=f'{name}_chunk_{j:03d}_diagnostic',deadline=120,arm='diagnostic_120s'))
    source.assert_unchanged()
    require(len(jobs)+len(diagnostic)<=128,'predeclared_call_budget_exceeded')
    paths=list((XAI/'xai_runtime').glob('*.py'))+[XAI/'evaluate_base.py',XAI/'tests/test_base_eval.py']
    paths+=list((DESIGN/'contracts').glob('*.json'))+[DESIGN/'prompts/xai_system_ko.txt',PACKAGE/'specmodel/local_models.py',PACKAGE/'models/base_files.json']
    write(out/'source_freeze.json',dict(files=[dict(path=str(f),sha256=sha(f)) for f in paths]))
    write(out/'plan.json',dict(version='xai-base-pilot/1.0.0',source=str(SOURCE),source_manifest_sha256=sha(SOURCE/'manifest.json'),
      previous_XAI_packet_files=old_files,previous_unique_transaction_refs=len(excluded),available_unused_transactions=sum(map(len,groups.values())),
      selection='Two deterministic hash-seed42 transactions per previously unused saved component-state group; no model-output selection.',
      namespace='same approved ESB source dataset; entity/source T row fallback when transaction ID missing',cases=cases,jobs=jobs,diagnostic_jobs=diagnostic,
      primary_deadline_seconds=30,diagnostic_deadline_seconds=120,diagnostic_selection='First case per state group, first chunk with required evidence, fixed before any LLM generation.',
      context_tokens=8192,max_input_tokens=6144,max_output_tokens=2048,max_facts_per_chunk=8,call_cap=128,
      all_facts_presented_once=True,chunk_contract='xai-chunk-evidence/1.0.0; xai-chunk-plan/1.0.0',
      cross_chunk_order='Original chunk order; LLM may reorder/select within each chunk. No claim of globally optimal explanation ordering.',
      check_merge='Stable union, first3 permitted codes; full per-chunk choices preserved.',
      severity_policy='not_configured; preserve unrated',SFT_steps=0,fit_calls=0,detector_score_calls=0,
      evaluation_scope='Small, disjoint-transaction Base pilot on existing saved detector outputs, not independent detector performance or trained-XAI performance.',
      superiority_criterion=None,notes=['Exploration/diagnostic results are not training-approved targets.','Primary timeout/failure is not erased by diagnostic success.']))
    print('PREPARED',len(cases),'transactions',totalfacts,'facts',len(jobs),'primary',len(diagnostic),'diagnostic jobs',flush=True)

def generate(out,shard,shards):
    import gc
    import torch
    from transformers import StoppingCriteria,StoppingCriteriaList,LogitsProcessorList
    from xgrammar.contrib.hf import LogitsProcessor
    sys.path.insert(0,str(PACKAGE))
    from specmodel.local_models import LocalModels
    from peft import PeftModel
    plan=read(out/'plan.json')
    for p in read(out/'source_freeze.json')['files']:require(sha(p['path'])==p['sha256'],'frozen_source_changed')
    worker=out/f'worker_{shard}';worker.mkdir(exist_ok=False)
    model=LocalModels(BASE);started=time.monotonic();model._cpu();model._load(worker)
    require(not isinstance(model.model,PeftModel),'Base_must_have_no_LoRA')
    write(worker/'load.json',dict(seconds=time.monotonic()-started,Base=model.integrity,adapters_loaded=0,all_weights_frozen=not any(p.requires_grad for p in model.model.parameters()),
      quantization=model.model.config.quantization_config.to_dict()))
    class Deadline(StoppingCriteria):
        def __init__(self,end):self.end=end;self.hit=False
        def __call__(self,input_ids,scores,**kwargs):
            self.hit=time.monotonic()>=self.end
            return self.hit
    records=[]
    alljobs=plan['jobs']+plan['diagnostic_jobs']
    for index,job in enumerate(alljobs):
        if index%shards!=shard:continue
        folder=worker/job['id'];folder.mkdir()
        require(sha(out/job['input'])==job['input_sha256'],'input_view_changed')
        view=read(out/job['input']);msg=messages(view);text,grammar,tokens=model.preflight(msg,CHUNK_SCHEMA,2048)
        require(tokens['input_tokens']<=6144 and tokens['input_tokens']+2048<=8192,'XAI_context_budget_exceeded')
        require(tokens['input_tokens']==job['input_tokens'],'prepared_prompt_token_count_changed')
        write(folder/'input.json',dict(messages=msg,schema=CHUNK_SCHEMA,tokens=tokens,job=job))
        encoded=model.tokenizer(text,return_tensors='pt',add_special_tokens=False).to('cuda:0')
        start=time.monotonic();deadline=Deadline(start+job['deadline']);failure=None;parsed=None;raw='';ids=[]
        try:
            with torch.inference_mode():
                answer=model.model.generate(**encoded,max_new_tokens=2048,do_sample=False,use_cache=True,
                  logits_processor=LogitsProcessorList([LogitsProcessor(grammar)]),stopping_criteria=StoppingCriteriaList([deadline]),
                  eos_token_id=model.tokenizer.eos_token_id,pad_token_id=model.tokenizer.pad_token_id)
            torch.cuda.synchronize();seconds=time.monotonic()-start
            ids=answer[0,encoded['input_ids'].shape[1]:].tolist();raw=model.tokenizer.decode(ids,skip_special_tokens=True)
            try:parsed=json.loads(raw)
            except json.JSONDecodeError:failure='incomplete_or_invalid_JSON'
            if deadline.hit or seconds>job['deadline']:failure='timeout'
            elif len(ids)>=2048 and model.tokenizer.eos_token_id not in ids:failure='output_length_limit'
            if failure is None:
                try:validate_chunk(view,parsed)
                except (InputError,ValidationError) as exc:failure='plan_validation:'+str(exc).split('\n')[0][:300]
            del answer
        except Exception as exc:
            seconds=time.monotonic()-start;failure='execution:'+repr(exc)
        record=dict(**job,passed=failure is None,failure=failure,seconds=seconds,output_tokens=len(ids),deadline_reached=deadline.hit,
          raw=raw,parsed=parsed,Base=model.integrity,adapter=None,greedy=True,thinking=False)
        write(folder/'output.json',record);records.append({k:v for k,v in record.items() if k not in ['raw','parsed']})
        (worker/'progress.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
        print('BASE',job['id'],job['arm'],record['passed'],failure,round(seconds,2),flush=True)
        del encoded;gc.collect();torch.cuda.empty_cache()
    write(worker/'summary.json',dict(shard=shard,records=records))
    model.model=None;gc.collect();torch.cuda.empty_cache()

def summarize(out,shards):
    import numpy as np
    import csv
    plan=read(out/'plan.json');outputs={}
    for shard in range(shards):
        require((out/f'worker_{shard}/summary.json').exists(),'worker_not_complete')
        for f in (out/f'worker_{shard}').glob('*/output.json'):
            r=read(f);require(r['id'] not in outputs,'duplicate_generated_job');outputs[r['id']]=r
    require(set(outputs)=={j['id'] for j in plan['jobs']+plan['diagnostic_jobs']},'model_job_coverage_mismatch')
    comparisons=[];results=[];text=[]
    for case in plan['cases']:
        packet=read(out/'cases'/case['id']/'packet.json');baseline=read(out/'cases'/case['id']/'baseline_result.json')
        jobs=[j for j in plan['jobs'] if j['case']==case['id']];answers=[outputs[j['id']] for j in jobs]
        views=[read(out/j['input']) for j in jobs];success=all(x['passed'] for x in answers);failure=None
        if success:
            try:merged=merge_plans(packet,views,[x['parsed'] for x in answers]);actual=render(packet,supplied_plan=merged)
            except (InputError,ValidationError) as exc:success=False;failure='merge:'+str(exc)
        if not success:actual=render(packet,failure_reason=failure or 'base_chunk_failure')
        require(actual['decision']==baseline['decision'] and actual['severity']==baseline['severity'],'saved_decision_or_severity_changed')
        require({f['id'] for f in packet['facts'] if f['required']}<={s['evidence_id'] for s in actual['sentences']},'final_required_omission')
        facts={f['id']:f for f in packet['facts']};expected={s['evidence_id']:s['text'] for s in render(packet,supplied_plan=dict(schema_version='xai-plan/1.0.0',packet_id=packet['packet_id'],items=[dict(template_id=f['kind'],evidence_id=f['id']) for f in packet['facts']],next_check_codes=[]))['sentences']}
        require(all(s['text']==expected[s['evidence_id']] for s in actual['sentences']),'rendered_text_changed')
        comparison=dict(**case,Base_success=success,fallback=not success,baseline_sentences=len(baseline['sentences']),Base_final_sentences=len(actual['sentences']),
          raw_failed_chunks=sum(not a['passed'] for a in answers),sum_generation_seconds=sum(a['seconds'] for a in answers),
          final_required_preserved=True,decision_preserved=True,severity_preserved=True,code_values_preserved=True,
          Base_optional_selected=sum(not facts[s['evidence_id']]['required'] for s in actual['sentences']) if success else None,
          baseline_checks=[c['code'] for c in baseline['next_checks']],Base_final_checks=[c['code'] for c in actual['next_checks']],
          order_equal_to_baseline=[s['evidence_id'] for s in actual['sentences']]==[s['evidence_id'] for s in baseline['sentences']])
        comparisons.append(comparison);results.append(dict(case=case['id'],result=actual))
        text.extend([case['id']+' / '+str(case['pattern'])+' / Base성공='+str(success),'CODE:']+[s['text'] for s in baseline['sentences']]+['BASE FINAL:']+[s['text'] for s in actual['sentences']]+[''])
    write(out/'comparisons.json',comparisons);write(out/'final_explanations.json',results)
    (out/'paired_explanations.txt').write_text('\n'.join(text)+'\n')
    with (out/'comparison.csv').open('x',newline='') as f:
        rows=[{k:canonical(v) if isinstance(v,(dict,list)) else v for k,v in x.items()} for x in comparisons];w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    primary=[outputs[j['id']] for j in plan['jobs']];diagnostic=[outputs[j['id']] for j in plan['diagnostic_jobs']]
    def counts(rows):
        seconds=[r['seconds'] for r in rows]
        return dict(calls=len(rows),valid=sum(r['passed'] for r in rows),failures=dict(Counter(r['failure'] for r in rows if not r['passed'])),
          median_seconds=float(np.median(seconds)),p95_seconds=float(np.quantile(seconds,.95)),max_seconds=max(seconds),tokens=sum(r['output_tokens'] for r in rows))
    summary=dict(status='completed',transactions=len(comparisons),primary_chunks=counts(primary),diagnostic_chunks=counts(diagnostic),
      Base_primary_transaction_success=sum(x['Base_success'] for x in comparisons),template_fallback_transactions=sum(x['fallback'] for x in comparisons),
      final_required_preservation=sum(x['final_required_preserved'] for x in comparisons),
      final_decision_severity_values_preserved=True,SFT_steps=0,fit_calls=0,detector_score_calls=0,
      approved_teacher_targets=0,LLM_better_than_code='not_established',independent_detector_test_claim=False,
      limitation='Small disjoint-transaction pilot, same detector/source period; no new logical-warning transactions remained after development exclusion; chunk-local ordering only.')
    for f in read(out/'source_freeze.json')['files']:require(sha(f['path'])==f['sha256'],'source_changed_during_evaluation')
    write(out/'summary.json',summary)
    report=['# XAI Base 설명 계획 평가','','기존 XAI 설명 표본과 다른 ESB 거래를 먼저 선택하고 코드 기본설명과 Base Qwen을 비교했다. 전용 XAI LoRA·교사 정답 승인·탐지 재실행·학습은 수행하지 않았다.','',
      f"- 새 거래 {len(comparisons)}건, 전체 근거 {sum(c['facts'] for c in comparisons)}개, 필수 근거 {sum(c['required'] for c in comparisons)}개",
      f"- 30초 주 평가: 거래 성공 {summary['Base_primary_transaction_success']}/{len(comparisons)}, 분할 성공 {summary['primary_chunks']['valid']}/{len(primary)}",
      f"- 고정 문구 fallback 거래 {summary['template_fallback_transactions']}건. fallback 성공을 LLM 성공으로 세지 않음",
      f"- 사전 지정 120초 진단: {summary['diagnostic_chunks']['valid']}/{len(diagnostic)}. 주 평가 실패를 바꾸지 않음",
      '- 최종 판정·심각도·수치·모든 필수 근거 보존 확인. 심각도 정책 미설정은 미정으로 유지',
      '- 전체 근거를 분할해 모두 제공했으며 입력 최대6144/전체8192/출력2048 tokens를 실제 tokenizer로 확인',
      '- 새 분할 계약: xai-chunk-evidence/1.0.0, xai-chunk-plan/1.0.0. 최종 결과는 기존 xai-result/1.1.0',
      '- 분할 내부 순서와 참고 근거·확인 항목 선택을 비교하며, 분할을 넘는 전체 거래 순서 최적화는 구현하지 않음',
      '- 설명 순서의 정답 문자열은 없음. 코드와 달라졌다고 더 좋은 설명으로 판정하지 않음',
      '- 120초 결과·fallback 결과와 원문 Base 성공률을 분리. 작은 표본을 운영 일반화·LoRA 성능으로 확대하지 않음',
      '- 기존 개발 표본 제외 후 새 논리 SR 경고 거래가 남지 않았으므로 그 종류의 독립 생성 성능은 미평가',
      '- paired_explanations.txt, comparisons.json, worker_*/ 각 입력/원문/실패 원인 보존','', '```json',json.dumps(summary,ensure_ascii=False,indent=2),'```','']
    (out/'report.md').write_text('\n'.join(report))
    seal(out);print('BASE_EVALUATION_COMPLETE',json.dumps(summary,ensure_ascii=False),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','generate','summarize']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--shard',type=int,default=0);p.add_argument('--shards',type=int,default=2);a=p.parse_args();out=a.output.resolve()
    require(out.is_relative_to(PROJECT) and out.is_dir(),'new_output_directory_required')
    if a.action=='prepare':prepare(out)
    elif a.action=='generate':generate(out,a.shard,a.shards)
    else:summarize(out,a.shards)

if __name__=='__main__':main()
