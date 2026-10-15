from prepare_v5 import *
from budget import audit
from collections import Counter

def main():
 tr=read(TOP/'training/training_result.json');comp=read(TOP/'comparison_r02.json');new=read(TOP/'review/adopted_new_features.json');rr=read(TOP/'review/root_rule_review.json')
 roles=Counter((x['prediction']['role'],x['prediction']['state'],x['prediction']['basis'])for x in rr['responses']if x.get('meaning_pass')and x.get('validation',{}).get('passed')and x.get('prediction'))
 counts={'teachers':{'prepared':160,'approved_silver':160,'specified':112,'discovery':48,'used_unique':tr['unique_consumed'],'effective_presentations':sum(tr['exposures'].values()),'authorship':'assistant_authored_silver'},'training':{'physical_optimizer_steps':60,'retained_updates':58,'lost_unsaved_updates':2,'continuation_steps':53,'trainable_parameters':tr['trainable_parameters'],'last_loss':tr['losses'][-1],'seconds':tr['training_seconds'],'one_SFT_experiment':True,'truncation':0},'comparison':{p:{k:v for k,v in o.items()if k!='rows'}for p,o in comp['results'].items()},'new_definitions':{'actual':sum(x['source_kind']=='approved_actual_capture'for x in new),'synthetic':sum(x['source_kind']=='controlled_synthetic'for x in new),'operational_usefulness_approved':0,'IF_approved':0,'existing71_v4actual4_not_summed':True},'Rule':{'responses':len(rr['responses']),'code_and_meaning_pass':sum(x.get('meaning_pass')and x.get('validation',{}).get('passed')for x in rr['responses']),'states':[{'role':r,'state':s,'basis':b,'count':n}for(r,s,b),n in roles.items()],'retrained':False},'generation':audit(),'evaluation_limits':read(TOP/'review/leakage_audit.json')}
 write(TOP/'counts.json',counts)
 write(TOP/'model_manifest.json',{'Base':read(ROOT/'local_model/model_manifest.json'),'feature_v4_adapter':pin(V4/'training/adapter_final/adapter_model.safetensors'),'feature_v5_adapter':pin(TOP/'training/adapter_final/adapter_model.safetensors'),'fixed_Rule_v2_adapter':pin(PARENT/'training/adapter_final/adapter_model.safetensors'),'identity':read(TOP/'training/LoRA_identity.json'),'original_Base_not_modified_or_merged':True})
 lines=['# 피처·규칙 보완 v5','','한 번의 원본 Base 피처 QLoRA 실험. 실제 optimizer60회 / 최종유지58 updates. 7-step 뒤 OOM에서 전체5-step 상태로 재개했다. 미저장2 updates를 결과와 분리하고 추가실행으로 보충하지 않았다. 답안 절단0.','','## 두 과제별 결과','']
 for phase,z in comp['results'].items():
  lines.append(f'{phase}: 전체 {z["full_pass"]}/20, 후보 {z["candidate_count"]}, JSON {z["JSON"]}/20, loss {z["loss"]:.6f}.')
  for group,s in z['by_group'].items():lines.append(f'- {group}: 전체 {s["full_pass"]}/{s["inputs"]}; 요청충족 {s["request_satisfied"]}; 후보 {s["candidate_count"]}; 정확한0개 {s["correct_zero"]}; 가능한분석 포기 {s["supported_analysis_abandoned"]}.')
 lines+=['','## 교사와 원천','',
 '사전 Root 검토·동결 silver160(A112/B48), 고유160건 사용. v4 144건과 이후선정138건을 요청·원천·계산 의미로 다시 검토했다. STATUS=S 누락2건은 원래 요청을 유지한 정확한 parent-lookup 지원한계 답안으로 바꿨고, 보류4건은 컬럼·단위·abs를 명시했다. v4 답안/선정/학습기록은 그대로 보존한다. silver는 업무·장애 정답이 아니다.',
 '새 실제 포착 T128/P128/M256, 거래128가족. 확인 가능한 과거 포착 가족/원천 행 중복0, 자식절단0, Body와 보류9/30~10/2 계보 제외. 같은 회사/원본파일/스키마이며 과거 전체파일 통계사용까지 미사용임을 증명하지 못한다. 정규화 판단사실 중복7, 동일 연산집합12건을 별도 집계했다. 합성은 기존 생성기 구조의 물리명/단위/값 변형이며 독립 기업 평가가 아니다.',
 '평가20은 생성 전 동결했고 최초응답만 채점, 수정0회. 자유발굴 입력에는 정답 후보 목록이나 특정 계산목적을 넣지 않았다. 다른 타당한 계산도 허용한다. 계산·요청·설명·중복·현재표본·유용성을 분리했다.','','## 새 정의와 Rule 연결','',
 f'v5 실제 새 정의 {counts["new_definitions"]["actual"]}개, 통제합성 {counts["new_definitions"]["synthetic"]}개. 기존71개·v4 실제4개는 별도 원천 목록으로 유지하고 합산하지 않는다. 새로운 물리정의는 새로운 분석 알고리즘 발견을 뜻하지 않는다. 운영상 유용성 승인0, IF 승인0. 상수/유효관측/표본값 중복/정의중복/가용성은 candidate_usage_v5.json 참조.',
 f'고정 Rule v2 {counts["Rule"]["responses"]}응답, 코드·직접의미 동시통과 {counts["Rule"]["code_and_meaning_pass"]}. HR은 확인된 계약만, 수치SR은 코드측정 참조 기준선만 사용한다. Rule 통과는 운영 유용성/장애탐지 성공이 아니다.','','## 재개와 잔여 제한','',
 'CPU 저차원 LoRA 대조는 가중치·AdamW·RNG·순서·노출의 정확한 복원을 확인했다. 실제 OOM 뒤 checkpoint5의 전체상태를 복원했고 재실행6-step loss가 같았다. GPU gradient norm은 미세하게 달랐으므로 비트동일 GPU 경로를 주장하지 않는다. 활성값 CPU보관/allocator 변경과 손실2 updates를 기록했다. 어댑터 저장·재로드와 실제 checkpoint 상태를 따로 감사했다.',
 'Rule 어댑터는 과거 문맥 오류 silver에 노출된 제한을 유지한다. 이번 새 입력/평가로 깨끗한 학습모델이라고 승인하지 않는다. 새 피처 teacher도 Root 작성 silver이며 독립 업무전문가 정답이 아니다.',
 '현재 사용범위는 승인 원천의 오프라인 메타데이터 후보 제안 → 코드 측정/검증 → 사람 의미검토 → 제한된 Rule 명세 생성이다. 실시간 가용시점/누출/업무계약/IF/운영 유용성은 미승인이다. 추가 SFT·Rule 재학습·이상탐지·GitHub 변경·패키지 설치는 하지 않았다.',
 'README CLI는 고정된 v5 후보와 모델 원문·Rule 원문을 재계산/연결하는 정확한 재현이다. 재현을 새 발굴로 세지 않는다. 새 공급 후보는 코드검증만 하며 의미 승인을 자동부여하지 않는다.',
 '기존파일 SHA4,765개가 유지된 보존 감사와 모든 교사/응답/실패/체크포인트/소비기록을 제공한다.']
 (TOP/'report.md').write_text('\n'.join(lines)+'\n')
 (TOP/'README.md').write_text("""# 보완 v5 결과와 재현

comparison.html을 브라우저에서 열어 두 과제의 결과·모델 원문·계산을 확인합니다.

프로젝트 폴더에서 기존 .venv로 CPU 재현:

~~~bash
cd '<private-research-workspace>'
.venv/bin/python -B data/eda/feature_rule_refinement_v5/20261014T014811+0900/implementation/reproduce_v5.py --output data/eda/feature_rule_refinement_v5/20261014T014811+0900/replay_user.json
~~~

기존 출력은 덮어쓰지 않습니다. --key는 review/adopted_new_features.json의 특정 후보를 선택합니다. 저장된 v5와 고정 Rule 응답을 보존하고 측정·참조를 재확인하는 재현이며 새 발굴/GPU 호출이 아닙니다.

새 공급 후보 코드검사:

~~~bash
.venv/bin/python -B data/eda/feature_rule_refinement_v5/20261014T014811+0900/implementation/reproduce_v5.py --provided-output output.json --context '컨텍스트_디렉터리' --output new_validation.json
~~~

코드통과는 의미/운영/IF 승인이 아닙니다. 종료된 원장으로 새 생성/학습을 실행하지 않습니다. model_manifest.json은 실제 Base·피처v5·고정Rulev2 경로와 SHA를 기록합니다.

실험은 OOM 뒤 전체5-step 상태에서 재개했으며 optimizer실행60 / 유지58 updates, 절단0입니다. 교사/평가/동일방법/판단사실/운영 한계는 report.md에 있습니다.
""")
 (TOP/'MODEL_CARD.md').write_text('# 피처 QLoRA v5\n\nQwen3.6-27B Base 새 초기화1회. 실제 Qwen3_5ForConditionalGeneration, 텍스트304모듈/608 LoRA텐서/23,760,896파라미터. rank8/alpha16/dropout0.05, NF4 double/bf16, batch1/accumulation4, AdamW2e-4 constant LR, nonreentrant checkpoint. 입력loss0, assistant+EOS만, 절단0.\n\nRoot 작성·검증 silver160(A112/B48). OOM 후 전체5-step 상태 재개, 실제실행60/최종58 updates. Vision/Base 동결. Rule 어댑터와 별도이며 Rule 재학습0. 오프라인 피처 계산 제안용; 업무·장애·기업 일반화·IF 승인 아님. 비교는 comparison.json, 경로는 model_manifest.json.\n')
 write(TOP/'generation_ledger/closed.json',{'event':'closed','reason':'one SFT and evaluation completed; no further automatic experiments','audit':audit(),'cap':120})
 print(json.dumps({'new':counts['new_definitions'],'comparison':{p:z['full_pass']for p,z in comp['results'].items()},'physical_steps':60,'retained':58}))
if __name__=='__main__':main()
