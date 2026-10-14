from feature_contract import *
from budget import audit
import html
def textfile(p,s):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:f.write(s)
def render():
 comp=read(TOP/'comparison.json');train=read(TOP/'training/training_result.json');training=read(TOP/'training/result.json');usage=read(TOP/'definition_usage71_review.json');teachers=read(TOP/'teacher_curation.json');rule=read(TOP/'review/root_rule_review.json');budget=audit();adopted=read(TOP/'review/adopted_new_features.json');summary=comp['summary'];a=summary['base'];b=summary['feature_adapter']
 counts={'existing_definitions':usage['counts'],'teachers':teachers,'post_training_teacher_review':read(TOP/'review/post_training_teacher_scope_audit.json'),'training':{k:train[k]for k in ['optimizer_steps','microsteps','unique_consumed','trainable_parameters','training_seconds','category_presentations','retained_weight_updates','unsaved_updates_lost','unique_with_retained_updates','AdamW_state_reset_after_external_interruption']},'comparison':summary,'new_features':{'actual_narrow_definitions':comp['adopted_actual'],'synthetic_narrow_definitions':comp['adopted_synthetic'],'not_added_to71_or_summed_across_datasets':True,'operational_usefulness_approved':0,'IF_approved':0},'fixed_Rule':rule['counts'],'generation':budget,'SFT_runs':1,'Rule_SFT_runs':0,'package_installs':0,'anomaly_runs':0,'GitHub_mutations':0}
 write(TOP/'counts.json',counts);mf=read(ROOT/'local_model/model_manifest.json');write(TOP/'model_manifest.json',{'Base':mf,'class':'Qwen3_5ForConditionalGeneration','feature_adapter':pin(TOP/'training/adapter_final/adapter_model.safetensors'),'feature_adapter_path':str((TOP/'training/adapter_final').relative_to(ROOT)),'Rule_adapter':pin(PARENT/'training/adapter_final/adapter_model.safetensors'),'Rule_adapter_path':str((PARENT/'training/adapter_final').relative_to(ROOT)),'NF4_doublequant':True,'bf16':True,'one_exposed_GPU':0,'LoRA':pin(TOP/'training/LoRA_identity.json'),'feature_training_from_original_Base':True,'Rule_training_unchanged_dirty_context_limitation':True})
 report=f'''# 피처·규칙 보완 v4

이번 실험은 Qwen Base에서 피처 전용 QLoRA를 **한 번** 학습하고, Rule 어댑터를 보완 v2로 고정했다. 이상탐지·추가 SFT·설치·GitHub 변경은 실행하지 않았다.

## 실제 결과

| 지표 | Base | 피처 어댑터 |
|---|---:|---:|
| 동결 입력 | 16 | 16 |
| JSON 계약 | {a['JSON_contract']} | {b['JSON_contract']} |
| 계산기 AST 통과 후보 | {a['candidate_compiler_valid']} | {b['candidate_compiler_valid']} |
| 정의·타입·단위·출처 명세 통과 후보 | {a['candidate_definition_type_unit_source_valid']} | {b['candidate_definition_type_unit_source_valid']} |
| 실제 측정 완료 후보 | {a['candidate_measured']} | {b['candidate_measured']} |
| 전체 입력 통과 | {a['pipeline_pass']} | {b['pipeline_pass']} |
| 올바른 후보0개 | {a['correct_zero']} | {b['correct_zero']} |
| 가능한 분석 포기 | {a['supported_analysis_abandoned']} | {b['supported_analysis_abandoned']} |
| 길이 상한 도달 | {a['length_limit_reached']} | {b['length_limit_reached']} |

계산 통과와 직접 의미 승인을 분리했다. `comparison.json`은 입력 단위, 후보 단위, 실제/합성/판단 사실 중복별 지표를 담는다. 지원되는 다른 계산 방향도 공개 목적과 의미가 맞으면 인정했으며, 교사 AST와 단순 일치하는지로만 채점하지 않았다. 모델 출력은 사후 수정하지 않았다.

피처 어댑터에서 기존71개와 다른 좁은 의미를 승인한 정의는 **실자료 {comp['adopted_actual']}개**, **합성 {comp['adopted_synthetic']}개**다. 서로 다른 데이터셋 정의를 누적 운영 피처 수로 합산하지 않는다. 현재 표본의 변동, 상수, 결측 및 표본 값 중복은 후보별 측정과 리뷰에 기록했다. 운영 유용성·IF 승인·장애 탐지 성능 승인은 0이다.

## 학습과 자료

교사144건은 assistant_authored_silver이며 후보0/1/2개가 20/97/27건이다. 양성 계산151개를 공통 코드와 독립 인터프리터로 측정·대조하고 좁은 의미를 직접 검토했다. Rule244를 피처 정답으로 재사용하지 않았다. 다만 학습 후 재검토에서 부모 STATUS=S 요청을 메시지 단위 계산식에 반영하지 않은 교사2건과 원인이 모호한 보류 설명4건을 확인했다. 실제 학습144건은 그대로 보존하고, 엄격한 후속 의미 선정은 승인138·범위 오류 제외2·설명 보완 보류4로 기록했다. 계산151개 통과를 교사 전체 정답성 승인으로 확대하지 않는다. 두 오류 사례를 소비한 이번 피처 어댑터도 깨끗한 교사 기반 모델로 승인하지 않는다. 실제 회사 업무 정답 또는 장애 라벨이 아니다. 학습 입력·정답은 `data_r04/train.jsonl`, 원천·측정·검토는 `teacher_validation_r06/`과 `review/pretraining_teacher_review.json`에 있다. 초기 길이 초과 자료와 준비 실패 로그는 보존하되 학습하지 않았다. 최종 최대 길이3465토큰이며 절단0이다.

새 어댑터는 원본 Base에서 초기화했다. 텍스트304개 모듈, LoRA608개 텐서·{train['trainable_parameters']:,}개 파라미터만 학습했다. NF4 doublequant·bf16·rank8/alpha16/dropout0.05·batch1/accumulation4·lr2e-4·gradient checkpointing을 사용했다. **{train['optimizer_steps']} optimizer steps / {train['microsteps']} microsteps**, 고유 학습 샘플{train['unique_consumed']}개를 소비했고 학습 시간은 {train['training_seconds']:.1f}초다. loss는 최초 {train['losses'][0]:.6f} → 마지막 {train['losses'][-1]:.6f}. 평가 정답 loss는 Base {training['base_loss']:.6f} / 새 어댑터 {training['feature_adapter_loss']:.6f}. loss 감소만으로 판단 성공을 승인하지 않았다. 순서·샘플 ID·파일/답안 SHA·노출·gradient·GPU 메모리는 `training/`에 기록했다. 첫step에서 유한gradient, LoRA 변경, Base 동결을 확인했다. 저장 후 재로드 생성도 실행했다.

대화 중단으로 최초 프로세스가7step 후 종료돼 저장된1step 어댑터에서 같은 실험을 재개했다. **6개 업데이트는 저장되지 않아 손실**, AdamW moment와 RNG 상태도 복원할 수 없었다. 추가53회만 실행해 누적 실행60회를 유지했지만 최종 가중치에 남은 업데이트는 **54회**다. 실제 소비 고유 샘플과 별도로 최종 가중치에 반영된 고유 샘플은 **{train['unique_with_retained_updates']}개**다. 학습 품질과 loss 비교에는 이 중단/optimizer 재초기화 제한이 있으며, 중단 없는60step 모델이라고 부르지 않는다. 최초 실행과 부분 microstep 기록을 보존했다. 재개 후에는5step마다 optimizer 상태도 저장했다. 새 어댑터 초기화/독립 SFT 실험은 한 번이다.

## 평가 원천과 제한

승인 원본에서 과거 사용 기록과 겹치지 않는 거래128가족을 선택했다: T128/P128/M262. 복원한 명시적 표본/행 기록과의 거래 가족·원천 행 중복0, 자식 절단0이며 승인 기간/Body/보류 계보 검사를 거쳤다. 관계는 포착 원본 파일 범위에서 보존됐으며 실제 업무 종료/완전성을 증명하지 않는다. 실제8건과 새로운 물리 이름·단위·정보 부족의 합성8건을 학습 전에 동결했다. 같은 회사·스키마의 미사용 거래 평가와 합성 평가이며 독립 기업 일반화가 아니다. 원본 파일과 기존 기간 범위·스키마 집계 정보는 이미 사용됐으며, 과거 모든 원천 열람과 전체 파일 집계 사용까지 가족 단위로 완전히 복원했다고 주장하지 않는다.

전체 인벤토리 사실 비교의 중복0 외에, 실제 판단에 필요한 원천만 비교한 `judgment_fact_overlap_r02.json`에서 합성5건의 중복을 확인했다. 이5건을 별도 집계했다. 16건 중15건은 계산 방법 패턴이 학습에서 다뤄졌으므로 새로운 분석 방법 발견 능력으로 확대하지 않는다. 평가 정답/응답은 이번 학습에 편입하지 않았고 FRESH23_2/3도 사용하지 않았다. 동결 수정 호출0이다.

## 71개 정의와 고정 Rule 연결

기존64개·HR 코드지표5개·v3 좁은 정의2개를 구분했다. 71개 중 상수48·변동20·유효 관측0인3개, 유효 관측이 있는 계산68개다. HR 지표는 위반0이어도 유지한다. 타입을 구분한 표본 값 중복13묶음은 정의 중복과 구분하며 다른 정의를 삭제하지 않았다. IF는 현재 표본 상수48개 제외·23개 보류·승인0이다. 이는 과거 v3 포착의 측정 검토로, v4 새 표본에서 71개를 모두 다시 계산했다는 뜻이 아니다.

피처 정의·독립 측정·직접 의미 검토를 통과한 대표 최대4개를 고정 Rule 어댑터에 전달했다. 결과/근거/기준선은 `rule_link/`과 `review/root_rule_review.json`에 있다. 새 피처에 확인되지 않은 HR 계약을 만들지 않았다. 수치 기준선은 이전 별도 참조 포착에서 코드로 계산하고 정의·단위·기간·모집단 SHA를 연결했다. Rule 통과는 피처의 운영 유용성 또는 탐지 성능 승인이 아니다. 고정 Rule 어댑터가 과거 문맥 오류 교사를 학습했던 제한도 유지한다.

## 실행과 결과 확인

브라우저에서 `comparison.html`을 열면 단계별 비교·71개 상태·실제 원문·측정·Rule 결과를 확인할 수 있다. `MODEL_CARD.md`에 어댑터 로드 방법, `README.md`에 대표 재현 명령이 있다. `run_one_pipeline.py`로 실제 저장 모델 두 개를 순서대로 로드해1피처 생성·3Rule 판단을 재현했다. 실제 실행은 `representative/live_run/result.json`에 있다. 재현 CLI는 기존 실제 모델 원문을 보존하며 CPU로 측정·근거 검증과 고정 Rule 결과를 다시 연결한다. 새 GPU 호출이나 학습을 자동으로 시작하지 않는다.

전체 생성 상한120 중 실제 응답 {budget['responses_returned']}개 사용. 한 번의 피처 SFT로 종료한다. 세부 남은 오류는 직접 리뷰와 각 원문 응답 파일에서 확인한다. 코드 검사가 자유로운 자연어의 모든 의미를 증명하지 않으므로 현재 시스템은 검토를 전제로 한 오프라인 연구 도구다.
'''
 textfile(TOP/'report.md',report)
 textfile(TOP/'MODEL_CARD.md',f'''# Qwen 피처 전용 어댑터 v4

Base: `{mf['snapshot_path']}` (revision `{mf['revision']}`).
실제 클래스 Qwen3_5ForConditionalGeneration. 어댑터 `{TOP/'training/adapter_final'}`.
텍스트 전용 QLoRA 한 번, Rule 가중치와 별도. assistant_authored_silver144건이며 업무·장애 정답이 아니다. 순서·노출·NF4 설정은 training/에 있다.

기존 프로젝트 .venv에서 `implementation/inference_v3.py`의 `load_model(relative_adapter_path)` 및 `gen_batch`를 재사용할 수 있다. GPU는 실행 전에 확인하고 CUDA_VISIBLE_DEVICES로 노출 장치1개만 사용한다. 고정 공개 피처 SYSTEM은 implementation/feature_contract.py에 있다. 입력 정의/SHA/원천 버전과 검증을 함께 사용한다. 학습 loss 감소 또는 계산 통과로 운영 품질을 보장하지 않는다. 학습 후 교사 범위 오류2건·모호한 종료 설명4건을 확인했으며 추가 학습 없이 제한을 남겼다. 대화 중단으로 실행60회의 일부가 손실돼54회 업데이트가 가중치에 남았고 AdamW 상태가 초기화됐다. 새 생성은 별도 승인된 실행 원장을 사용해야 하며 이번 닫힌 원장을 늘리지 않는다.
''')
 rep=read(TOP/'representative/result.json')
 textfile(TOP/'README.md',f'''# v4 결과 확인

1. comparison.html을 브라우저에서 연다.
2. report.md와 comparison.json을 확인한다.
3. 프로젝트 루트에서 기존 .venv로 대표 CPU 재현:

```bash
cd '<private-research-workspace>'
.venv/bin/python -B '{TOP.relative_to(ROOT)}/implementation/replay_pipeline.py' --case {rep['case']} --output '{TOP.relative_to(ROOT)}/representative/replay_user.json'
```

새 출력 경로가 필요하다. 기존 파일은 덮어쓰지 않는다. CPU 재현은 실제 저장된 AI 응답을 읽고 측정과 근거를 다시 검증한다. 대표 실제 GPU 생성은 이미 training/ 및 rule_link/에 원문으로 보존돼 있다. 새 SFT/Rule 재학습/이상탐지 실행을 시작하지 않는다.

권위 자료는 data_r04, teacher_validation_r06, evaluation_freeze, frozen_judgment_criteria와 최종 review다. 앞선 길이 초과 준비본/실패 로그는 미사용 기록이다. 실자료 거래 가족은 새롭지만 방법 패턴과 합성 판단 사실 중복이 남는다. 운영 유용성·IF 승인0, 과거 Rule 어댑터 교사 문맥 오류 제한 유지.
''')
 payload={'comparison':comp,'usage':usage,'teachers':teachers,'post_training_teacher_review':read(TOP/'review/post_training_teacher_scope_audit.json'),'training':train,'rule':rule,'representative':rep,'actual_live_representative':read(TOP/'representative/live_run/result.json'),'teacher_post_review':read(TOP/'review/post_training_teacher_scope_audit.json'),'responses':{phase:[read(x)for x in sorted((TOP/'training/responses'/phase).glob('*.json'))]for phase in ['base','feature_adapter']},'new_features':adopted}
 js=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')
 page='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>피처 보완 v4 비교</title><style>body{font:15px system-ui;background:#f7f8fc;color:#18243b;max-width:1250px;margin:32px auto;padding:0 20px}h1,h2{color:#173e6e}section,details{background:white;border:1px solid #d8dfea;border-radius:10px;margin:15px 0;padding:18px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5fb;padding:12px;font-size:12px}select,input{padding:10px;margin:8px}.ok{color:#087844}.bad{color:#b72f25}.note{color:#815311}</style><h1>피처 생성 보완 v4</h1><p>한 번의 피처 QLoRA · 고정 Rule v2 · 실제 모델 원문과 코드/직접 의미 검토 분리</p><p class="note">미사용 거래 가족 평가 + 합성 평가. 합성5건 판단 사실 중복, 15/16 계산 방법 패턴 재등장. 운영 유용성·IF 승인·탐지 성능 승인0.</p><section><h2>Base / 피처 어댑터</h2><div id="summary"></div></section><section><h2>동결16건 입력과 실제 출력</h2><select id="phase"><option>feature_adapter</option><option>base</option></select><select id="case"></select><div id="one"></div></section><section><h2>기존71개 사용 상태</h2><input id="search" placeholder="ID·계산·상태 검색"><div id="usage"></div></section><section><h2>새 피처 · 고정 Rule 연결</h2><div id="rules"></div></section><section><h2>대표 전체 연결</h2><div id="rep"></div></section><p>교사144건은 assistant_authored_silver. 후속 감사: 범위 오류2·설명 보완4, 깨끗한 학습 모델 승인 아님. 원본과 이전 결과는 보존. 재현/로드 방법: README.md · MODEL_CARD.md · report.md</p><script>const D=PAYLOAD;const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');const pre=x=>'<pre>'+esc(JSON.stringify(x,null,2))+'</pre>';let s='<table><tr><th>지표</th><th>Base</th><th>피처 어댑터</th></tr>';for(const k of ['inputs','proposals','JSON_contract','candidate_compiler_valid','candidate_definition_type_unit_source_valid','candidate_measured','meaning','required_analysis_coverage','pipeline_pass','correct_zero','supported_analysis_abandoned','length_limit_reached'])s+='<tr><td>'+k+'</td><td>'+D.comparison.summary.base[k]+'</td><td>'+D.comparison.summary.feature_adapter[k]+'</td></tr>';document.querySelector('#summary').innerHTML=s+'</table><p>학습60steps · '+D.training.trainable_parameters.toLocaleString()+' trainable parameters · '+D.training.unique_consumed+' 고유 샘플</p>';const ids=D.responses.base.map(x=>x.id);document.querySelector('#case').innerHTML=ids.map(x=>'<option>'+x+'</option>').join('');function show(){const phase=document.querySelector('#phase').value,id=document.querySelector('#case').value;const row=D.responses[phase].find(x=>x.id===id),review=D.comparison.inputs.find(x=>x.phase===phase&&x.id===id);document.querySelector('#one').innerHTML='<p class="'+(review.pipeline_pass?'ok':'bad')+'">전체 '+(review.pipeline_pass?'통과':'실패/보류')+'</p>'+pre(review)+'<details open><summary>실제 모델 출력 (수정 없음)</summary><pre>'+esc(row.raw_output)+'</pre></details>'+pre(row.checks)}function usage(){const q=document.querySelector('#search').value.toLowerCase();document.querySelector('#usage').innerHTML='<p>'+JSON.stringify(D.usage.counts)+'</p><table><tr><th>ID/출처</th><th>정의 · 단위</th><th>관측</th><th>IF</th></tr>'+D.usage.definitions.filter(x=>JSON.stringify(x).toLowerCase().includes(q)).map(x=>'<tr><td>'+esc(x.id)+'<br>'+esc(x.group)+'</td><td>'+esc(JSON.stringify(x.plan.expression))+'<br>'+esc(x.unit)+'</td><td>'+x.summary.valid_count+' valid / '+x.summary.missing_count+' unavailable<br>'+esc(x.summary.variation)+'</td><td>'+esc(x.IF_state)+'<br>'+esc(x.IF_reason)+'</td></tr>').join('')+'</table>'}document.querySelector('#phase').onchange=show;document.querySelector('#case').onchange=show;document.querySelector('#search').oninput=usage;show();usage();document.querySelector('#rules').innerHTML=pre(D.rule);document.querySelector('#rep').innerHTML='<details open><summary>실제 두 모델 재로드·생성 실행</summary>'+pre(D.actual_live_representative)+'</details><details><summary>CPU 재현</summary>'+pre(D.representative)+'</details><details><summary>학습 후 교사 오류 감사</summary>'+pre(D.teacher_post_review)+'</details>';</script></html>'''.replace('PAYLOAD',js)
 textfile(TOP/'comparison.html',page);print(json.dumps({'report':str(TOP/'report.md'),'counts':counts['new_features']}))
if __name__=='__main__':render()
