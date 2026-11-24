Created: 2026-10-21T00:00:00+09:00
Updated: 2026-11-25T12:00:00+09:00
Author: Claude Code (Trainer 세션 builder, 6·7·10차 LLM 세션 builder, 10차 PR ② WebUI builder)
Status: draft

# 소프트룰 임계값 추천 계약 v1 (초안, Issue #94 — #74 `method-recommendation-v1` 개정)

- 변경 요약(#94): LLM 추천을 **소프트룰(화면 용어: 이상 징후 규칙) 임계값 추천**으로 재정의. (1) `recommend_thresholds` — 켜진 소프트룰 대상 필드 전체를 한 번에 다루는 LLM 비동기 작업(일괄, R5 b). 1차 연결은 **(가) 코드 p95 + LLM 근거**(현재 코드 동작과 일치, 11/02 9.1 실행 조사); **(나) LLM 숫자 제안**은 `source: ai_proposal`과 확장 필드로만 예약. (2) 규칙별 **수식 추천**은 LLM 없는 동기 계산 API(p95 등 통계) — 작업 큐가 아니라 API 계약. `recommend_rule` DSL 생성은 **폐지 유지**, `recommend_methods`(ML 선택 추천)는 **보류**
- 2차 개정(사용자 11/02 9.4): 규칙별 수식 추천의 **기준 기간 선택** — (a) 현재 운영 ML 모델이 학습에 쓴 첫 기간 시작 ~ 현재 (b) 최근 1개월·3개월 (c) 사용자 지정 → `POST /api/rule-set/threshold-stats`에 `period.mode` 추가, 운영 모델이 없을 때 (a)는 오류 코드. 일괄 추천 대상은 **저장된 리비전의 켜진 소프트룰**임을 명시(WebUI 11/02 대조), ratio 필드 표시값은 유효숫자 기준(카탈로그 `displayPrecisionMode`)
- 3차 정정(Issue #116, 11/04, WebUI 요청): `recommend_thresholds`·`threshold-stats` 결과 `threshold`를 **유효숫자 15자리 이내로 반올림한 고정소수 문자열**로 형식 통일(rule-set.md 저장 검증과 일치, 40자 한도 유지, 두 경로의 반올림 구현 차이는 수식 추천 절), 원값은 `evidence.p95`에 보존, `ai_proposal` 제안값도 같은 정규화·검증; `input_spec`·결과 `ruleSet`에 `contentHashVersion` 동반
- 3차 정정(Issue #116, 11/04): 수식 추천 SQL 번역의 `datetime_diff` 원천을 `parsed_times`가 아니라 `data_observations.allowed_values`의 `start_time_raw`·`end_time_raw`로 정정(Detector 11/04 확인, #112). 결측·해석 실패 처리를 카탈로그 `sourceMissingPolicy`와 일치시킴
- 4차 정정(Issue #121, 11/05): API 접수 구현과 워커 실제 동작에 맞춤. 결과 형식 `threshold-recommendation-v1`은 그대로. 요청 예시를 화면과 같은 `persisted_at` 기간으로, `input_spec`·`llm_spec` 키와 값 고정(`input_evidence_hash` = API 요청 hash, `prompt_version` 고정 이름), `current_rules` 문맥 없음, 1단계에서 `request_evidence`·`deferred` 미도달, 정수 필드 `ROUND_HALF_UP`, 실패 표 코드 추가, `generation_timeout`은 이 작업 종류만
- 5차 정정(11/13, LLM 세션): 설명 전용 소비자 추가, 생성 서비스 GPU 차례를 대화형 설명 우선으로 — `explain` 대기 상한 `LLM_EXPLANATION_WAIT_SECONDS`·오류 코드 `generation_busy` 추가(아래 GPU 차례 절). 작업 종류·결과 형식·추천 생성 시간·출력 한도는 그대로
- 6차 정정(11/14, 사용자 결정·LLM 세션 초안): 생성 서비스 가용성 — `GET /api/llm/status` 신설, 꺼져 있으면 `POST /api/jobs`의 LLM 작업(`explain`·`recommend_thresholds`)을 503 `generation_service_unavailable`로 접수 거절(작업·멱등키 없음), 화면은 요청 버튼 비활성 + 코드(아래 가용성 절). 작업 형식·결과 형식은 그대로
- 7차 개정 초안(11/14, LLM 세션): **Rule v3 경로(`llm_spec.model_version=rule_v3`) 추가**. 결과 형식 `threshold-recommendation-v1`은 그대로이고 필드만 추가(`heldWindowExclusion`, 항목 `basis`·`attempts`, `data.referenceRatio`·`referenceRows`·`currentRows`, `evidence.currentN`). 실행 중 진행률 `resultSummary.progress`. rule_v2 경로 동작은 그대로(진행률·`basis`·`heldWindowExclusion: null`만 추가). 아래 "Rule v3 경로" 절. API의 `llm_spec` 값 전환은 API·WebUI 세션 몫(변경점은 마이그레이션·코드 영향 절) — 반영됨(11/14, `llm_spec` 행·공용 예시 [llm-recommendation-job.example.json](llm-recommendation-job.example.json))
- 9차 보강(11/15, 동작 설명만): Rule v3 필드 병렬 처리(선택, `LLM_REPLICA_GPUS`) — 아래 Rule v3 경로 절 "GPU 복제본". 작업·결과 형식·항목 내용과 순서·실패 코드는 그대로
- 11차 보강(11/16, 동작 설명만): GPU 복제본이 생성 중 메모리 부족·프로세스 종료로 꺼지면 그 묶음을 남은 실행기에서 다시 돌리고, 적재 전 여유 메모리 검사(`LLM_REPLICA_MIN_FREE_MB`)를 더함 — 아래 "GPU 복제본". 작업·결과 형식·항목 내용과 순서·실패 코드 집합은 그대로
- 10차 개정(11/15 사용자 결정 1~6, LLM 세션): **이상 징후 후보 대상 + 범위(scope)별 p95**. Trainer `recommend_relations`의 처리 시간 후보(`thresholdCandidates[]`)도 대상이 되고(요청 `scope.candidateIds`), 결과 항목 1건은 필드가 아니라 **(필드, 적용 범위 `scope`) 묶음**이다. `input_spec.targets[]` 새 모양 `{kind, id, field, scope, threshold}`, rule_v3 새 `llm_spec.evidence_version: soft-rule-threshold-v3`, 결과 `items[]`에 `scope`·`candidateIds[]` 추가(형식 버전 v1 유지), 상한 = 묶음 ≤ 32·대상 ≤ 64, 현재 쪽 유효 값 0개는 코드가 `no_applicable`, `threshold-stats` 요청 `scope`. 숫자는 여전히 코드 계산(후보도 워커가 다시 계산)이고 LLM은 상태·문장만. 아래 "10차" 절. **구현 상태: 워커는 PR ①로 구현(옛·새 모양 모두 받음), API 접수·후보 조회·`threshold-stats` `scope`·화면·공용 예시 json은 PR ②로 구현** — PR ② 뒤 API는 모든 `recommend_thresholds` 작업에 새 대상 모양과 `soft-rule-threshold-v3`를 기록한다(10차 절)
- 12차 개정(11/25, LLM 세션 — AI 근거 요약 `evidence-summary-v1` 워커 쪽): `explain` 새 형식(`llm_spec.evidence_version` `summary-sentences-v1`)은 생성 서비스 새 종류 `polish_explanation`(경량 다듬기 모델, 평문)으로 코드 문장을 한 문단으로 다듬고 워커가 확인한다. 생성 실패는 재시도 없는 실패(`generation_busy`·`generation_timeout`도 바로 `failed`), 새 오류 코드 `llm_model_mismatch`, `/health` 본문에 `polishModel`. 아래 "AI 근거 요약 다듬기" 절. 이전 형식(`saved-decision-v1`) 작업 동작은 그대로
- 8차 정정(11/14, LLM 세션): `input_spec.ruleSetContentHashVersion`에 3 허용 — 룰 세트 hash v3([rule-set.md](rule-set.md) 룰 세트 v3 절)로 저장된 리비전에서도 작업이 `invalid_rule_set_reference`로 끝나지 않게 워커 검사를 1·2·3으로 넓힘. 작업·결과 형식은 그대로
- 계약 버전: `threshold-recommendation-v1`. 근거 코드 origin/dev `7f24e03`: `worker/research_design.py:36-44`(summary·p95 `np.quantile` linear), `:154-199`(`rule_request` — 기준선 = train 분할·anomaly 라벨 제외 p95, `minimum_n 20`, 단위 `count|ratio|ms|s|us`), `:202-234`(`recommend_rules` — 변수당 LLM 1회 순차, 검증 탈락은 변수만 제외, `str(request["baseline"]["value"])`로 DSL 값 생성), `worker/generation_client.py:54-55`(`TimeoutError`를 `generation_service_unavailable`로 묶음), `backend/src/rules/ruleContract.ts:17`(DSL 십진수 정규식, 지수 표기 거부)
- 상위 결정: 사용자 11/02 결정 — 임계값 산출 주체(8.1), AI 추천 단위 일괄 + 규칙별 수식(R5), 현재 코드 실행 조사(9.1), 화면 설계(9.2: (가)(나) 모두 담고 (가)로 먼저 연결). 절 번호는 총괄 기록 기준
- 범위: 작업 요청·입력 범위·추천 1건 형식·상태·실패/시간 초과 구분·임계값 직렬화 규칙·수식 추천 API·마이그레이션 영향. 제외: 프롬프트·어댑터 연구(LLM 담당 팀원), GPU 배포, 자동 적용(추천은 입력칸 채우기까지, 운영 반영은 [rule-set.md](rule-set.md) 저장)
- 표기: **[확정]** 사용자 확정·코드 사실 / **[제안]** 이 초안이 정한 값

## 기존 추천과의 관계

| 작업 종류 | 상태 | 비고 |
|---|---|---|
| `recommend_rule` | **폐지 유지** [확정 10/21·11/02] | 새 Rule DSL 초안(`rule:sr_<hash>`, `SR_DIAGNOSTIC`) 생성 경로. 기존 `run_llm_results`·`adopt` Rule은 읽기 보존. 신규 요청은 API가 422 `job_kind_abolished`로 거절(11/13, 작업 행·큐 메시지 없음, rules-ui.md AI 규칙 추천 절). **내부 계산 경로(`rule_request`·`recommend_rules`의 p95·검증)는 `recommend_thresholds`가 재사용** — 산출물만 Rule 초안이 아니라 임계값 추천으로 바뀜 |
| `recommend_feature` | 폐지 유지 [확정 10/21] | 신규 요청은 API가 422 `job_kind_abolished`로 거절(11/13). 입력 변수 세트는 필드 선택 등록(`POST /api/jobs/feature-sets`, trainer-service.md) |
| `recommend_methods` | **보류** [제안] | #74 "등록 방법 중 선택 추천"의 ML 선택 부분. 모델 카탈로그·레지스트리·IF 파라미터화 Issue 뒤 재검토. `job_kind` 값(011)·`run_llm_results.kind` CHECK는 유지, API 접수·worker 분기는 구현하지 않음. 폐지가 아니라 "현 단계 범위 밖" |
| `recommend_thresholds` | **신규** | 이 문서 |
| `explain` | (12차) 새 형식 `summary-sentences-v1` 추가, 이전 형식 `saved-decision-v1` 그대로 | GPU 차례 우선·대기 상한·`generation_busy`는 아래 GPU 차례 절, 새 형식은 아래 "AI 근거 요약 다듬기" 절 |

- 폐지 두 종류의 남은 대기·임대 작업은 워커가 실행 없이(LLM 호출·스냅샷 없음) 재시도 없는 `failed`·`job_kind_abolished`로 끝낸다. 기존 `run_llm_results` 행은 읽기 보존

## 산출 주체 — (가) 코드 p95 + LLM 근거 [확정 11/02 9.2, 1차 연결]

- 숫자(임계값)는 **코드가 계산**: 대상 기간 데이터 중 `status=S`·명시 anomaly 라벨 제외 표본의 p95(`research_design.summary` `np.quantile(…, .95)` linear). LLM은 **제안 여부(상태)와 근거 문장**만. LLM이 숫자를 직접 내면 검증(`validate_rule`·`expected_target`)에서 탈락 → 해당 필드 `rejected`(작업은 계속)
- (나) LLM 숫자 제안 [확정 11/02 8.1 "우선 시도", 9.2 "LLM 팀원 변경 후 같은 화면에 붙임"]: 응답 `source: "ai_proposal"`과 `aiProposal{…}` 확장 필드로 **예약만**. 필요한 LLM 범위 변경(프롬프트·출력 스키마 숫자 허용, 범위·단위 검증, LoRA 재학습 또는 베이스+프롬프트, `adapterSha` 고정)은 11/02 9.1 기록. 이 계약은 (나)가 켜져도 응답 형식이 바뀌지 않게 필드를 미리 둠

## 작업 — `POST /api/jobs kind=recommend_thresholds` [신규, 관리자, 비동기]

```json
{
  "kind": "recommend_thresholds",
  "sourceId": "<source UUID>",
  "ruleSetRevision": 13,
  "scope": {"softRuleIds": null},
  "data": {"snapshotId": null, "from": "2026-08-05T00:00:00.000Z", "until": "2026-11-05T00:00:00.000Z", "timeBasis": "persisted_at"},
  "mode": "data_p95_with_llm_rationale"
}
```

- 위 키 밖이나 형식 위반은 400 `invalid_job_request`. 아래 표가 다른 코드를 정한 경우만 그 코드

| 키 | 규칙 |
|---|---|
| `ruleSetRevision` | 추천 기준 룰 세트 리비전([rule-set.md](rule-set.md)). 0 이상 정수. 현재 리비전과 다르면 409 `rule_set_revision_conflict`(화면 새로 불러오기, 없는 리비전도 같음. 응답은 `{code}`만). 룰 세트는 출처별이 아니라 전역 하나라 `sourceId`와 무관하게 현재 리비전과 대조. 같은 멱등키 재전송도 이 검사를 먼저 받음. 리비전 행이 하나도 없으면 빈 대상으로 대신하지 않고 503 `job_storage_unavailable`. 작업은 이 리비전(= **저장된** 상태)의 **켜진 소프트룰** 대상 필드를 입력으로 고정(`input_spec`에 `ruleSetContentHash`·`ruleSetContentHashVersion`(그 리비전 행의 `contentHash`·`contentHashVersion` 그대로, 재계산 없음 — [rule-set.md](rule-set.md) 리비전 형식 절, #116)·대상 목록 기록). **화면에서 편집 중이나 아직 저장하지 않은 규칙(추가·필드 변경·켬)은 대상이 아님** — 화면은 미저장 변경이 있으면 "저장 후 추천" 안내(WebUI 11/02 대조) |
| `scope.softRuleIds` | 키 필수. null = 켜진 소프트룰 전체(R5 b 일괄). 배열이면 그 중 부분집합(화면 선택): 중복 없는 소프트룰 id 최대 32개, 꺼졌거나 없는 id는 빠지고 대상만 남음. 같은 필드·같은 적용 범위(소프트룰 `scope`)를 쓰는 룰이 여러 개면 **(필드, scope)당 1건** 계산하고 응답에서 룰 id 목록으로 묶음(10차 — 이전 "필드당 1건"은 scope가 모두 null일 때와 같음). `[]`는 소프트룰 없음 — 유효한 후보가 없으면 `soft_rules_empty`(10차) |
| `scope.candidateIds` | (10차, PR ② 구현) 선택 키. 생략·null = 후보 없음(현행). 배열이면 Trainer 처리 시간 후보 id `<jobId>.t<n>` 1~32개(중복 없음, 대소문자 무시). `input_spec.scope`에는 늘 기록(없으면 null). 자격·출처·snapshot 규칙은 아래 10차 절 |
| `data.snapshotId` | 집계 출처(ready, `schema_version=process-v1`, `purpose` 무관하게 **train 분할**만 사용 — 현행 `rule_request` scope). 형식 오류 400 `invalid_snapshot_reference`. 조건에 맞지 않거나 함께 보낸 기간과 다르면 409 `snapshot_conditions_mismatch`(워커에도 같은 검사). null·생략 시 `from/until`로 snapshot 생성하는 현행 추천 경로 재사용 [유지 10/21] |
| `data.from`·`data.until` | `snapshotId`가 없으면 필수. 기존 작업 종류와 같은 규칙(UTC `Z` 표기, `from < until`, 366일 이내)에 달력에 없는 날짜 거부를 더함. 없거나 위반이면 400 `invalid_time_range` |
| `data.timeBasis` | `persisted_at`만 실행(생략·null도 같음). 기간으로 만드는 snapshot이 저장 시각 기준뿐이라, `snapshotId` 없이 `esb_occurred_at`이면 422 `recommendation_time_basis_unsupported`(워커에도 같은 검사, 다른 기준으로 조용히 바꾸지 않음). `snapshotId`가 있으면 요청 값은 쓰이지 않고 결과 `data.timeBasis`는 그 snapshot `selection_spec` 값. 그 밖의 값은 400 `invalid_job_request` |
| `mode` | `data_p95_with_llm_rationale`(가, 기본·유일 구현, 생략·null도 같음) / `ai_proposal`(나, 예약 — 어댑터가 없어 접수 단계에서 422 `recommendation_mode_not_available`, 작업을 만들지 않음. 워커에도 같은 검사). 그 밖의 값은 400 `invalid_job_request` |
| 권한·멱등키·재시도·상태 | 기존 `/api/jobs`(queued/running/succeeded/failed, `attemptCount`·`maxAttempts`·`nextRunAt`). 큐 `llm_recommendation`([queues.md](queues.md)) |
| `input_spec` | 키 고정: `kind`, `sourceId`, `ruleSetRevision`, `ruleSetContentHash`, `ruleSetContentHashVersion`, `scope`, `data{snapshotId, from, until, timeBasis}`, `mode`, `targets[]`. `targets[]`는 API가 채운 대상 규칙의 `{id, field, threshold}`(룰 세트 저장 순서, `name`·`operator`·`severity` 없음) — 워커는 이 목록만 읽고 룰 세트를 다시 읽지 않음. (10차) 새 모양은 `{kind, id, field, scope, threshold}`(소프트룰 → 후보 순, 아래 10차 절 `targets[]` 표). 워커는 `kind` 없음 = `soft_rule`, `scope` 없음 = null로 읽어 옛 모양도 그대로 받는다(PR ① 구현). API는 PR ② 뒤 모든 작업을 새 모양으로 기록한다(범위 없는 소프트룰도 `kind: soft_rule`·`scope: null` 명시). 요청 `scope`(대상 선택)와 `targets[].scope`(적용 범위)는 이름만 같고 뜻이 다르다. `ruleSetContentHash`·`ruleSetContentHashVersion`은 리비전 행 값 그대로이고 버전 1·2·3을 받음(그 밖의 값·bool·정수 아닌 값은 `invalid_rule_set_reference`). 워커는 hash를 재계산하지 않고 결과 `ruleSet`에 그대로 옮긴다. 예시는 [llm-recommendation-job.example.json](llm-recommendation-job.example.json)(API 시험과 워커 `parse_job` 시험이 같은 파일을 읽음). `input_config_version_id`·`input_snapshot_id`·`input_decision_id`는 NULL |
| `llm_spec` | (7차) 워커는 `(model_version, evidence_version)`이 `('rule_v2', 'soft-rule-threshold-v1')` 또는 `('rule_v3', 'soft-rule-threshold-v2')`인 작업만 실행하고 그 값으로 경로를 고른다(그 밖은 `recommendation_llm_spec_invalid`). (10차) 워커는 `('rule_v3', 'soft-rule-threshold-v3')`도 받는다(PR ① 구현). 새 대상 모양(`kind: candidate` 또는 null 아닌 `targets[].scope`)이 하나라도 있으면 이 조합만 실행하고, `soft-rule-threshold-v2`면 `recommendation_llm_spec_invalid`, rule_v2면 `recommendation_targets_require_rule_v3`(방어). 옛 모양만 쓰는 작업은 세 조합 모두 결과가 같다. `output_schema_version`은 둘 다 `threshold-recommendation-v1`. **API는 rule_v3 값을 기록한다**(7차 전환 11/14, 10차 PR ② 뒤 evidence v3): `evidence_version: 'soft-rule-threshold-v3'`, `model_version: 'rule_v3'`, `prompt_version: 'feature-rule-spec-rule-v3-ko'`(고정 이름, `reuse_key` 계산에도 들어감) — API 상수 하나(`thresholdRecommendationContract.ts`)라 범위 없는 작업도 이 값이다(PR ② 전 값은 `soft-rule-threshold-v2`). 나머지 키·규칙은 아래 rule_v2 설명과 같고, rule_v2 값으로 되돌리면 이전 경로로 돈다(그때 후보·범위 있는 대상은 접수 400 `recommendation_targets_require_rule_v3`). 아래는 rule_v2 값: `evidence_version: 'soft-rule-threshold-v1'`, `input_evidence_hash: <API 요청 hash>`(= `request_hash`, 위 `input_spec` 전체의 sha256. 입력 집계는 워커가 만들어 접수 시점에는 없음), `model_version: 'rule_v2'`, `prompt_version: 'feature-rule-design-v0.2'`(고정 이름 — 워커가 `recommend_rule`과 같은 번들의 rule 프롬프트를 쓰고 API는 번들을 읽지 않음), `output_schema_version: 'threshold-recommendation-v1'`, `reuse_key: sha256([actor, input, idempotencyKey, prompt_version])` — 011 트리거 필수 6키 그대로. `input`은 위 `input_spec` 전체라 리비전·hash·기간·대상이 하나라도 다르면 키가 달라지고, 멱등키가 들어 있어 재사용은 같은 실행의 재전송에만 일어남(같은 기간이라도 새 실행은 새 작업) |
| 접수 거부 | 켜진 소프트룰 0개(또는 `softRuleIds` 전부 꺼짐·미존재) 400 `soft_rules_empty`(10차: 유효한 후보도 없을 때만); 필드가 카탈로그에 없음 422 `soft_rule_field_unknown:<field>`; 없는 출처 404 `source_not_found`; 같은 멱등키에 다른 입력(대상이 바뀐 경우 포함) 409 `idempotency_payload_conflict`; 관리자가 아니면 403 `admin_required`. (10차) 후보·묶음 상한 코드와 검사 순서는 10차 절 요청 표 |

## 입력 범위 — GPU에 넘기는 것 [유지 10/21 Q-M: 집계값만]

- 필드마다 현행 `rule_request` 형식(`feature-evidence-bundle-4.0.0`) 그대로: `feature{id, dtype, unit, expression(카탈로그 AST)}`, `measurement{row_count, valid_count, min, max, p95, …}`, `baseline{state: verified|unavailable, value, value_exact, n, minimum_n: 20, method}`, `scope`, `reference_policy{operator: gt}`. 카탈로그 `softRuleFields[].source`를 `definition`(`{name: fieldId, expression: {op: "derived", ast, outputType: "number", unit}}`)으로 변환해 기존 함수에 넘김. 단 `rule_request`는 `row["features"][name]`(`research_design.py:163`)을 읽으므로 **Trainer가 카탈로그 AST로 행마다 값을 먼저 계산해 `features[fieldId]`에 넣는 작은 경로가 필요**(`derived_expression.evaluate` 재사용; 피처셋 materialization은 쓰지 않음). 그 뒤 LLM 호출·검증 경로는 변경 없음
- 추가 컨텍스트 없음: LLM 요청은 필드별 `rule_request` 하나뿐이고 현재 소프트룰 목록(`current_rules`)은 넣지 않는다. Rule v2 입력 계약(`feature-evidence-bundle-4.0.0`)에 자리가 없고 근거 문장은 검증기가 정한 형식이라, 현재 임계값은 결과 `currentThreshold`로만 쓴다
- 원문 BODY·토큰·관측 원문·고객 식별자 금지. 판정 결과·evidence 제외
- (7차) rule_v3 경로는 GPU 프롬프트에 여전히 집계값만 들어가지만(번들 Rule 입력), 번들이 측정을 직접 하므로 워커 컨테이너 임시 폴더에 묶음별(10차, 폴더 `g<n>`) 행 값 CSV(필요 열만)를 쓰고 작업이 끝나면 지운다. 영구 볼륨·결과에는 남기지 않는다. (10차) 범위 값(`processId` 등)은 범위 필터에만 쓰고 CSV·폴더 이름·`dataset_id`·프롬프트에 넣지 않는다

## 결과 JSON `threshold-recommendation-v1` — `GET /api/jobs/:id` `resultSummary`

```json
{
  "recommendationContractVersion": "threshold-recommendation-v1",
  "mode": "data_p95_with_llm_rationale",
  "ruleSet": {"revision": 13, "contentHash": "9c40…", "contentHashVersion": 2},
  "data": {"snapshotId": "<snapshot UUID>", "split": "train", "timeBasis": "persisted_at",
           "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained", "rowCount": 47120},
  "heldWindowExclusion": null,
  "provider": "rule_v2",
  "adapterSha": "…",
  "items": [
    {"field": "elapsed_ms", "scope": null, "softRuleIds": ["soft:elapsed_ms-3f9a2c1d"], "candidateIds": [],
     "currentThreshold": 5000,
     "operator": "gt", "threshold": "7210.5", "displayThreshold": "7211", "unit": "ms",
     "source": "data_p95", "basis": "train_p95", "status": "proposed",
     "evidence": {"n": 46980, "min": 12.0, "max": 184233.0, "p95": 7210.5, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": "b_…"},
     "rationale": "The 95th percentile of 46,980 successful process durations is 7,210.5 ms; durations above it are rare in the reference period.",
     "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}},
    {"field": "error_ratio", "softRuleIds": ["soft:error_ratio-5d2e0b19"], "currentThreshold": 0.01,
     "operator": "gt", "threshold": "0.00003125", "displayThreshold": "0.00003125", "unit": "ratio",
     "source": "data_p95", "status": "proposed",
     "evidence": {"n": 46980, "min": 0.0, "max": 0.5, "p95": 0.00003125, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": "b_…"},
     "rationale": "…", "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}},
    {"field": "retry_count", "softRuleIds": ["soft:retry_count-91ab77e0"], "currentThreshold": 3,
     "operator": null, "threshold": null, "displayThreshold": null, "unit": "count",
     "source": "data_p95", "status": "no_applicable",
     "evidence": {"n": 46980, "min": 0.0, "max": 0.0, "p95": 0.0, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": null},
     "rationale": "All reference values are 0; a percentile threshold has no discriminative value.",
     "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}},
    {"field": "total_count", "softRuleIds": ["soft:total_count-0a1b2c3d"], "currentThreshold": 10000,
     "operator": null, "threshold": null, "displayThreshold": null, "unit": "count",
     "source": "data_p95", "status": "rejected", "rejectedReason": "invalid_generation_output",
     "evidence": {"n": 46980, "min": 1.0, "max": 25100.0, "p95": 9800.0, "quantileMethod": "linear_(n-1)*p",
                  "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                  "measurementId": "m_…", "baselineId": "b_…"},
     "rationale": null, "aiProposal": null, "reviews": {"schema": false, "facts": false, "expectedTarget": false}}
  ],
  "counts": {"targets": 4, "proposed": 2, "requestEvidence": 0, "deferred": 0, "noApplicable": 1, "rejected": 1}
}
```

### 추천 1건(`items[]`) 필드

| 필드 | 규칙 |
|---|---|
| `field` | 카탈로그 `fieldId`. `softRuleIds[]` = 이 필드를 쓰는 켜진 소프트룰(화면이 행마다 [적용] 가능). (10차) 같은 `scope` 묶음의 소프트룰만, id 사전순. 후보만 있는 묶음이면 `[]` |
| `scope` | (10차 추가, 모든 항목) 묶음의 적용 범위 정규형(`targets[].scope`, 키 순서 `processId`·`adapterType`·`processHubId`) 또는 null(필드 전체). 옛 모양 대상이면 항상 null |
| `candidateIds` | (10차 추가, 모든 항목) 묶음에 든 후보 id(요청 순서), 없으면 `[]`. `targetKind` 키는 두지 않음 |
| `currentThreshold` | 요청 시점 룰의 임계값(참고 표시). 룰이 여러 개면 첫 룰 값(id 사전순)·나머지는 화면이 룰 세트에서 읽음. (10차) 소프트룰 없는 묶음(후보만)은 null |
| `operator` | 1차는 항상 `gt`(코드 `reference_policy.operator`, `>p95`). 화면 조건과 다르면 사람이 조정. `status≠proposed`면 null |
| `threshold` | **유효숫자 15자리 이내로 반올림한 고정소수 문자열**(#116, WebUI 요청): 계산 원값(numpy p95 등, 예 `956.4999999999995`)을 `Decimal`로 유효숫자 15자리(`ROUND_HALF_EVEN`)로 줄인 뒤 `format(d, "f")`·후행 0·소수점 제거(지수 표기 금지, 40자 이내·필드 `valueType=integer`면 15자리 반올림 뒤 `ROUND_HALF_UP`으로 정수) — [rule-set.md](rule-set.md) 저장 검증(`소프트룰.threshold` 유효 숫자 ≤ 15자리·`threshold` 변환 규칙)과 같은 규칙이라 화면이 [적용]한 값이 그대로 저장을 통과함. 원값은 `evidence.p95`에 반올림 없이 그대로 남김. 워커 출력과 저장 검증의 일치는 [threshold-values.example.json](threshold-values.example.json)으로 양쪽 시험이 확인. 11/02 9.1 결함 대응: p95가 1e-4 미만이어도 `"0.00003125"` 형태로 직렬화. 변환 실패(비유한·40자 초과 → 필드 `displayPrecision+6` 자리로 반올림 후 재시도, 그래도 실패)는 그 필드만 `rejected`(`threshold_serialization_failed`), **작업은 성공** |
| `displayThreshold` | 카탈로그 `displayPrecision`·`displayPrecisionMode`로 `ROUND_HALF_UP` 반올림해 만든 표시 문자열(원값 툴팁): `fixed`(count·ms)는 소수 자릿수, `significant`(ratio)는 **유효숫자** 자릿수 — `error_ratio` p95 `0.00003125`는 유효숫자 4로 `"0.00003125"`(고정 소수 4면 `"0.0000"`이 되어 값이 사라지는 문제 방지, WebUI 11/02 대조). 지수 표기 금지는 표시값에도 적용 |
| `unit` | 카탈로그 단위 |
| `source` | `data_p95`(가, 코드 계산) / `ai_proposal`(나, 예약) |
| `basis` | (7차 추가) 임계값 p95를 계산한 표본 코드. `train_p95` = train 분할 전체(rule_v2) / `train_reference_80_p95` = train 분할을 원천 시각으로 나눈 앞 80%(rule_v3). 모든 항목에 있음(상태 무관). 화면 문구는 WebUI 몫 |
| `attempts` | (7차 추가, rule_v3만) 그 항목(10차: 묶음)의 LLM 생성 시도 수 0~2(0 = 코드가 LLM 없이 `no_applicable`, 2 = 수리 1회) |
| `status` | `proposed`(적용 가능) / `request_evidence`(근거 부족 — LLM 상태) / `deferred`(보류 — LLM 상태) / `no_applicable`(해당 없음 — 기준선 `unavailable`: 표본 < 20·값 전부 결측·단위 미검증·분산 없음) / `rejected`(검증 탈락·형식 오류·직렬화 실패, `rejectedReason`) — 현행 `rule_v2` 출력 `state` 5종과 대응. 화면은 `proposed`만 선택 가능, 나머지는 사유와 함께 회색. **1단계(`data_p95_with_llm_rationale`)에서는 `request_evidence`·`deferred`에 도달하지 않음**: 기준선이 `unavailable`이거나 분산이 없으면 코드가 LLM 호출 없이 `no_applicable`로 끝내고, 그 밖의 경우 검증기 기대 상태는 항상 `proposed`라 다른 상태를 내면 `expected_target_mismatch`로 `rejected`. 이 두 상태와 `counts`의 두 값은 형식만 유지 |
| `rejectedReason` | `invalid_generation_output`(스키마 위반·`truncated`) / `expected_target_mismatch`(LLM이 숫자를 직접 냄 등) / `factual_review_failed` / `threshold_serialization_failed` / `generation_failed:<code>`(그 필드 호출만 실패, 아래 실패 절) |
| `evidence` | `n`(기준 표본 `valid_count`)·`min`·`max`·`p95`(number **원값** — `threshold`의 15자리 반올림 전 값, 예 `956.4999999999995` 그대로)·`quantileMethod`·`exclusionRule`·`measurementId`/`baselineId`(`m_…`/`b_…`, 입력 집계 ID — 근거 추적). 분위 보간이 넘쳐 p95가 비유한값이면 그 항목은 `threshold_serialization_failed`이고 `evidence.p95`·`measurementId`·`baselineId`가 null(JSON과 hash를 만들 수 없음), `n`·`min`·`max`는 유지. (10차) 범위 있는 묶음은 `n`·`min`·`max`·`p95`·`currentN` 모두 범위로 거른 뒤 값 |
| `rationale` | LLM 근거 문장. rule_v2는 영문 ≤ 500자, rule_v3는 **한국어**(번들 결정의 `reason`만, 1~320자 — 번들 스키마 상한). `rejected`는 null. rule_v3에서 LLM 없이 끝난 `no_applicable`은 코드가 쓴 한국어 문장 |
| `aiProposal` | (나) 예약. 켜지면 `{threshold: "<고정소수>", displayThreshold, operator, validation: {inRange: boolean, unitMatched: boolean}, rawText}`; 1차는 항상 null. LLM 제안 숫자도 위 `threshold`와 **같은 정규화**(유효숫자 15자리 반올림 → 고정소수 문자열)를 거친 뒤 [rule-set.md](rule-set.md) 저장 검증과 **동일 규칙**(유한값·15자리·정수 필드 정수·40자·DSL 정규식)으로 검사하고, 위반이면 그 항목 `rejected`(`rejectedReason`은 기존 코드 — 직렬화·정밀도 위반 `threshold_serialization_failed`, 범위·단위 불일치 `expected_target_mismatch`) (#116) |
| `reviews` | 서버 검증 단계 결과 `{schema, facts, expectedTarget}`(현행 `validate_rule`·`factual_review`·`expected_target`) |
| (없음) `severity` | **응답에 없음** [확정 11/02 9.2]: 위험도는 사람이 고름(화면 기본 `warning`) |

- 상한: `items` ≤ 32(10차: 묶음 수 상한 = 항목 수, 대상은 64개까지), `rationale` ≤ 500자(rule_v2), ≤ 320자(rule_v3)
- (10차) 순서: `field` 사전순 → 같은 필드 안에서 `scope` null 먼저, 그다음 정규형 JSON(키 정렬·공백 없음) 사전순. `counts.targets`·진행률 `total` = 묶음 수
- 저장: 성공 결과는 `run_llm_results`(kind `recommend_thresholds`) + `run_jobs.result_summary`(현행 `_finish` 경로). 항목별 `rejected`가 있어도 저장
- 적용: 화면 [적용] → 선택한 값만 룰 입력칸에 채움 → [저장]([rule-set.md](rule-set.md) `PUT /api/rule-set`)해야 운영 반영. 서버 자동 적용 없음 [확정]

## Rule v3 경로 — `llm_spec.model_version=rule_v3` [7차 초안 11/14]

- 켜기: 선택 기능. compose override(`infra/compose.rule-v3.yaml`)로 export를 마운트할 때만 켜진다. export 검증·Base 대조(Rule v3 묶음 Base 목록의 revision·메타데이터 SHA = 서비스 Base)·초기화가 실패하면 v3만 끄고 시작 로그에 `rule_v3_disabled: <코드>` 한 줄을 남긴다. 설명·rule_v2는 영향 없음. 꺼진 동안 rule_v3 작업은 `rule_v3_not_configured`
- 실행: `llm` 컨테이너 같은 프로세스에서 Rule v3 묶음(연구 묶음 `feature_rule_spec`을 `research_bundle.py --rule-v3`로 export, `RULE_V3_BUNDLE_ROOT` 읽기 전용)을 부른다. Base는 서비스가 이미 올린 것을 공유하고 Rule v3 LoRA만 더 올린다. HTTP `/generate`에는 없다(외부 모델 모드면 `rule_v3_not_configured`). 추가 학습 없음
- 표본: rule_v2와 같은 규칙(status=S·명시 anomaly 라벨 제외)으로 snapshot **train 분할**을 고른 뒤, 원천 시각(END 먼저, 없으면 START, 시간대는 출처 `clock_basis` 검증값 또는 Asia/Seoul 가정)으로 거래를 묶어 시간 블록 단위로 **앞 80% = 참조, 뒤 20% = 현재**로 나눈다. 같은 시각·한 거래는 갈라지지 않는다. (10차) 분할은 **작업당 1회**(묶음 범위와 무관 — `heldWindowExclusion`·`data.referenceRows`·`currentRows`는 작업 전체 값)이고, 범위 있는 묶음은 나눈 뒤 **참조·현재 쪽마다** 행을 범위로 거른다(10차 절 범위 필터). 뒤 20%에만 몰린 범위는 참조 부족, 앞 80%에만 있는 범위는 현재 0개로 `no_applicable`이 될 수 있다
- 숫자: `threshold`는 **코드가 참조 표본으로 계산한 p95**(linear (n-1)·0.95, Decimal)를 위 `threshold` 규칙으로 직렬화한 값. 번들 기준선 값과 같은 표기로 일치해야 하고 다르면 `expected_target_mismatch`. LLM은 상태·근거만 낸다. `evidence.n·min·max·p95`는 참조 표본 기준, `evidence.currentN`은 현재 쪽 유효 값 수, `measurementId`·`baselineId`는 번들 Rule 입력 ID(LLM 없이 끝난 항목은 null)
- 코드 선처리(LLM·번들 호출 없음, `no_applicable`, `attempts: 0`, 한국어 문장): 참조 유효 값 0개 / 20개 미만 / 전부 같은 값 / (10차, PR ① 구현) 참조는 충분한데 **현재 쪽 유효 값 0개** — 문장 "현재 기간에 유효 값이 없어 비교할 수 없습니다."(범위 있는 묶음은 "현재 기간에 이 범위의 유효 값이 없어 비교할 수 없습니다."). 번들 Rule 입력에는 현재 쪽 하한이 없고(`minimum_current_observations: null`) 0개면 입력 거부(작업 `recommendation_rule_input_rejected`) 또는 `proposed` 불가(`no_current_numeric_observations`)라 0개만 코드가 끝낸다. 범위 없는 대상에도 적용되므로, 이전에 번들 결정 `deferred`로 끝나던 "현재 쪽 유효 값 0" 경우는 이제 `no_applicable`이다(옛 요청에서 결과가 바뀌는 유일한 경우)
- 상태: 번들 결정 `state` 그대로 — `proposed`(operator `gt`, threshold 채움) / `deferred` / `request_evidence` / `no_applicable`. 이 경로에서는 `deferred`·`request_evidence`에 도달할 수 있다(위 1단계 미도달 문장은 rule_v2 기준). (10차) 현재 쪽 유효 값 0개는 위 코드 선처리라 `deferred`의 사유가 아니다
- 검증·수리: 번들 검증기가 출력을 확인하고 실패하면 **번들 안에서 수리 1회**(필드당 생성 최대 2회). 최종 실패면 `rejected`이며 마지막 시도 오류로 사유를 정한다: JSON·잘림·스키마 → `invalid_generation_output`(reviews 모두 false) / 상태·조건·근거 ID·기준선(`baseline_grounding`, `*_contract`, `unusable_reference`, `no_current_numeric_observations`, `structurally_unreachable` 등) → `expected_target_mismatch` / 설명 문장 검사만 → `factual_review_failed`
- 서버 근거 점검(번들 통과 뒤, 하나라도 실패하면 `rejected` + `factual_review_failed`, reviews `{schema: true, facts: false, expectedTarget: true}`): (a) 글자 중 한글 50% 이상 (b) 문장의 숫자가 모두 그 필드 Rule 입력 사실(피처·측정·기준선·범위 등)에 있는 숫자. 앞에 글자가 붙은 숫자(`p95`)는 세지 않고, 뒤에 붙은 단위(`7210.5ms`, `20개`)는 센다. 입력 키 `p95`류는 그 분위 숫자(95)를 사실에 더하고, ID·SHA·버전·기간 값과 ID·해시·시각 꼴 문자열에서 나온 숫자는 사실에서 뺀다. **근거의 퍼센트 표기(`%`·`퍼센트`·`백분율`)는 거절**한다(입력 사실은 0~1 비율이라 대조할 수 없음)
- 보류 창: 번들 입장 검사는 기간이 9/30~10/2 보류 창과 겹치면 거부한다. 워커는 겹치면 **회피 규칙**으로 진행한다: 해석된 START·END 시각의 [최소, 최대]가 창 `[2026-09-30T00:00+09:00, 2026-10-03T00:00Z)`(보류 날짜는 시간대 없는 KST 날짜라 하한을 KST로, 상한은 UTC로 둔 보수적 경계)와 겹치는 거래를 통째로 빼고, **창 뒤쪽(≥ 10/03 00:00Z)만** 남겨 나누며, 뒤쪽으로 나눌 수 없으면 **앞쪽(< 9/30 00:00 KST)만** 남긴다. 참조 기간은 첫 참조 시각부터 현재 시작까지라 창 양쪽 데이터를 함께 쓰면 기간이 창을 덮으므로 한쪽만 남긴다. 뺀 행(창 거래 행 + 버린 쪽 행) 수를 `heldWindowExclusion`에 적는다. 빼고 나서 한 필드 표본이 부족하면 그 필드 `no_applicable`, 어느 쪽도 나눌 수 없으면 작업 `failed` + `recommendation_input_empty`
- 시간: 생성 시도 1회 상한 `LLM_RECOMMENDATION_SECONDS`(기본 240초, 시도 동안만 Base `generation_config.max_time`으로 둠). 끊긴 시도는 수리로 넘기지 않고 작업 `failed` + `generation_timeout`(위 실패 절 규칙). (10차) 항목이 필드가 아니라 묶음이라 최대 32개다. GPU 1장·주말 실측 시도당 64~102초 기준 묶음 32개면 시도 1회씩 약 34~54분, 모두 수리까지 가면 최대 약 110분이고, 시도 상한(240초) 기준 이론 최악은 약 4시간 + CPU 처리(이전 문장 "필드 최대 6개 × 시도 2회 최악 약 48분"은 범위 없는 필드 6개일 때). GPU 복제본이 있으면 장수만큼 나뉜다
- GPU 차례: 생성 시도 1회마다 작업 차례를 따로 잡는다(설명이 시도 사이에 먼저 들어감). 시도가 길어 그 사이 설명 요청이 대기 상한 10초를 넘겨 `generation_busy`가 나는 일이 rule_v2보다 잦을 수 있다
- GPU 복제본(9차, 선택): `LLM_REPLICA_GPUS`(override `infra/compose.llm-multi-gpu.yaml`)가 있으면 GPU마다 별도 프로세스에 같은 모델(같은 Rule v3 export, 어댑터 SHA 대조)을 올리고 한 작업의 필드를 비어 있는 실행기에 나눠 동시에 처리한다. (10차) 이 단락의 "필드"는 결과 항목 단위인 **묶음**(필드 × 범위)으로 읽는다 — 나눠 처리·순서·진행률·실패 규칙 모두 묶음 단위이고 같은 필드의 묶음 여럿도 따로 나뉜다. 실행기 순서는 복제본 먼저, 주 프로세스(설명 담당 GPU)는 맨 뒤라 필드가 적으면 주 GPU를 설명에 비워 둔다. 결과 `items`는 직렬과 같은 필드 순서·내용이고 `adapterSha`도 같다. 진행률 `resultSummary.progress.processed`는 완료한 필드 수(필드 순서와 무관하게 하나씩 증가). 한 필드라도 작업 실패 예외가 나면 시작 안 한 필드는 돌리지 않고 실행 중인 필드를 기다린 뒤, 필드 순서상 가장 앞 필드의 실패 코드로 작업이 끝난다(직렬과 같은 코드 집합). (11차) 복제본이 묶음을 끝내지 못하고 꺼지면 — 생성 중 CUDA 메모리 부족(`replica_out_of_memory`) 또는 복제본 프로세스 종료·파이프 끊김(`replica_exited`) — 그 복제본은 이후 시도·작업에서 빠지고(로그 `rule_v3_replica_disabled: <gpu> <코드>`) 그 묶음은 이 시도 안에서 남은 실행기로 다시 돈다. 메모리 부족은 주 프로세스도 받을 수 있고, 프로세스 종료는 그 묶음이 원인일 수 있어 다른 복제본만 받는다. 꺼진 실행기는 다시 쓰지 않으므로 묶음마다 실행기당 최대 1회이고, 결과 항목 순서·내용과 진행률(묶음당 한 번)은 그대로다. 받을 실행기가 없으면 그 묶음은 생성 장애(`GenerationUnavailable`, 내부 코드 `generation_failed`)로 이 시도가 끝나 기존 생성 장애와 같은 재시도 규칙을 따른다(이전: 복제본이 죽으면 바로 이 실패). 그 밖의 묶음 실패(시간 초과·입력·모델·값)는 위 규칙 그대로. 복제본 적재 실패·어댑터 불일치·(11차) 적재 전 여유 메모리 부족(`LLM_REPLICA_MIN_FREE_MB`, `replica_insufficient_memory`)은 그 장만 끄고 시작 로그 `rule_v3_replica_disabled: <gpu> <코드>`, 준비 전·전부 꺼진 동안은 준비된 실행기(최소 주 프로세스)로만 돈다. 복제본은 Rule v3 필드만 받고 설명·rule_v2는 주 프로세스 그대로

### 결과 추가 필드(형식 버전 v1 유지)

| 위치 | 형식 | 뜻 |
|---|---|---|
| 최상위 `heldWindowExclusion` | `null` 또는 `{"from": "<ISO 8601 오프셋 포함>", "until": "<ISO 8601>", "excludedRows": <0 이상 정수>}` | 보류 창 회피로 뺀 행. 겹침이 없으면 `null`. rule_v2는 항상 `null`(회피 없음). `from`·`until`은 위 경계 그대로 `"2026-09-30T00:00:00+09:00"`·`"2026-10-03T00:00:00+00:00"` |
| `items[].basis` | `"train_p95"` / `"train_reference_80_p95"` | 위 항목 필드 표 |
| `items[].attempts` | 0~2 정수(rule_v3만) | 위 항목 필드 표 |
| `items[].evidence.currentN` | 정수(rule_v3만) | 현재(뒤 20%) 쪽 유효 값 수 |
| `items[].scope`·`items[].candidateIds` | 정규형 또는 null / 문자열 배열(두 경로 모두, 10차) | 위 항목 필드 표 |
| `data.referenceRatio`·`data.referenceRows`·`data.currentRows` | 0.8 / 정수 / 정수(rule_v3만) | 참조 비율과 회피 뒤 양쪽 행 수 |
| `provider`·`adapterSha` | `"rule_v3"` / Rule v3 LoRA 가중치 SHA | |

```json
{
  "recommendationContractVersion": "threshold-recommendation-v1",
  "data": {"snapshotId": "<snapshot UUID>", "split": "train", "timeBasis": "persisted_at", "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
           "rowCount": 47120, "referenceRatio": 0.8, "referenceRows": 30200, "currentRows": 7550},
  "heldWindowExclusion": {"from": "2026-09-30T00:00:00+09:00", "until": "2026-10-03T00:00:00+00:00", "excludedRows": 9370},
  "provider": "rule_v3", "adapterSha": "…",
  "items": [{"field": "elapsed_ms", "scope": null, "softRuleIds": ["soft:elapsed_ms-3f9a2c1d"], "candidateIds": [],
             "currentThreshold": 5000,
             "operator": "gt", "threshold": "7180", "displayThreshold": "7180", "unit": "ms",
             "source": "data_p95", "basis": "train_reference_80_p95", "status": "proposed", "attempts": 1,
             "evidence": {"n": 30100, "min": 12.0, "max": 184233.0, "p95": 7180.0, "quantileMethod": "linear_(n-1)*p",
                          "exclusionRule": "status=S, explicit anomaly labels excluded; unlabeled retained",
                          "measurementId": "m_…", "baselineId": "baseline_…", "currentN": 7530},
             "rationale": "참조 기간 유효 값 30100개의 p95보다 큰 현재 처리 시간을 경고 후보로 봅니다.",
             "aiProposal": null, "reviews": {"schema": true, "facts": true, "expectedTarget": true}}],
  "counts": {"targets": 1, "proposed": 1, "requestEvidence": 0, "deferred": 0, "noApplicable": 0, "rejected": 0}
}
```

### 진행률 — 실행 중 `resultSummary` [7차 초안, 두 경로 공통]

- 작업이 `running`인 동안 `resultSummary` = `{"progress": {"processed": <처리한 필드 수>, "total": <대상 필드 수>}}`. 시작 시 0, 필드마다 갱신. (10차) 단위는 묶음(= 결과 항목): `total` = 묶음 수, 범위 없는 대상만이면 이전과 같은 필드 수. `recommendationContractVersion` 키가 없으므로 결과 문서와 구분된다
- 성공하면 최종 결과로 바뀐다. 재시도 대기(`queued`), 새 임대(이전 시도 만료 뒤 다시 시작), `max_attempts`·`queue_exhausted` 종료에서는 NULL로 비우고, 그 밖의 종료 실패는 기존 규칙(전 항목 거절 요약 또는 NULL)
- 쓰기는 작업 소유권을 확인한 짧은 트랜잭션이라 생성 중에 열린 트랜잭션이 없다

## 실패·시간 초과 — 작업 전체 vs 필드 격리 [제안]

| 상황 | 처리 |
|---|---|
| 생성 서비스 미설정·연결 불가(`generation_service_not_configured`·`generation_service_unavailable`) — 첫 호출에서 | 작업 `failed` + 그 코드(재시도 규칙 기존) |
| 호출 시간 초과 | 작업 `failed` + **`generation_timeout`** [확정 10/21 N20·11/02 9.2 "시간 초과 별도 표시"]. **`recommend_thresholds`에만 적용**: 이 작업의 호출만 `report_timeout`을 켜고(HTTP는 `reportTimeout`, 504·`TimeoutError`·`URLError.reason`이 timeout), `recommend_rule`·`recommend_feature`·`explain`은 이전 분류(`generation_service_unavailable`) 그대로다. 기존 작업 종류로 넓히는 것은 LLM 세션 협의 대상 |
| 설명 GPU 대기 초과 | `explain`만: 아래 GPU 차례 절의 대기 상한 안에 생성을 시작하지 못하면 `generation_busy`. 성공으로 바꾸지 않음. 추천 작업은 대기 상한이 없어 이 코드를 받지 않음 |
| 일부 필드 처리 뒤 서비스 장애·시간 초과 | 작업 `failed`(부분 결과 없음, 현행과 동일) — 화면 [다시 시도]. **대안**(Q-L1): 처리된 항목을 `partial`로 저장하고 나머지 `rejected:generation_failed`로 격리해 성공 처리 |
| 한 필드의 출력 형식 깨짐·검증 탈락·직렬화 실패 | 그 필드만 `rejected` + `rejectedReason`, 나머지 계속, 작업 **성공** — 현행 "검증 탈락은 변수만 제외"를 "제외"가 아니라 "사유와 함께 표시"로 확장(11/02 9.1: `ValueError`로 작업 전체 failed였던 지수 표기 경우를 격리) |
| 모든 필드 `no_applicable`(데이터 사실: 표본 부족·분산 없음) | 작업 **성공**(항목마다 사유가 있으므로 빈 성공이 아님). 화면은 "제안 0건" + 사유 표. 대상 0건은 접수 단계 400 |
| 모든 필드가 **생성계 사유**로 `rejected`(`invalid_generation_output`·`expected_target_mismatch`·`factual_review_failed`·`generation_failed:*`) | [제안, reviewer 권고 4 — Q-L1에 묶음] 작업 `failed` + `recommendation_all_rejected`(항목별 사유는 `resultSummary`에 보존해 `GET /api/jobs/:id`가 그대로 돌려줌. 현재 화면은 실패 코드만 표시하고 이 표는 그리지 않음). 생성 서비스 전체가 깨진 상황을 "성공"으로 보이게 하지 않기 위함. `no_applicable`·`threshold_serialization_failed`가 섞여 있으면 성공 |
| (7차) rule_v3 구성·입력 | 작업 `failed`: `rule_v3_not_configured`(Rule v3 override 없음·초기화 실패로 꺼짐·같은 프로세스 모델 없음) / `recommendation_rule_input_rejected`(번들이 캡처 입력을 거부 — 입력 조립 문제라 전 필드 공통) / `rule_v3_model_failure`(번들 모델 검사 실패, 어댑터 설정·호출 예산 등) / `generation_adapter_version_mismatch`(시도 출력의 어댑터 SHA가 export와 다름) |
| 입력 집계 부족 | 작업 `failed` + `recommendation_input_empty`. (7차) rule_v3는 참조/현재로 나눌 거래 묶음·시간 블록이 모자라거나 보류 창 회피 뒤 남는 쪽이 없을 때도 이 코드. 기간 안의 실행별 최신 행이 **20건 미만**(snapshot 생성의 `snapshot_insufficient_process_rows`를 이 코드로 바꿈)이거나 snapshot의 train 분할이 0행. `status=S`로 거른 표본이 20건 미만인 필드는 이 실패가 아니라 그 필드 `no_applicable` |
| 기간은 찼지만 snapshot을 나눌 수 없음 | 작업 `failed` + 그 코드 그대로: `snapshot_insufficient_execution_groups`(실행 묶음 10개 미만) / `snapshot_time_split_unavailable`(시간 블록 3개 미만) / `snapshot_row_limit_exceeded`(기간 행 100만 초과) |
| 워커 입력 재검사 | 정상 접수 경로에서는 나오지 않는 방어 코드, 스냅샷을 만들기 전에 작업 `failed`: `recommendation_mode_invalid`·`recommendation_llm_spec_invalid`·`recommendation_data_range_missing`·`invalid_rule_set_reference`·`invalid_recommendation_targets`·`soft_rule_limit_exceeded`. (10차, PR ① 구현) `soft_rule_limit_exceeded` = 묶음 33개 이상, `invalid_recommendation_targets`에 대상 65개 이상·`targets[]` 새 모양 위반(10차 절 표)을 더함, `recommendation_targets_require_rule_v3`(rule_v2 작업에 새 대상 모양) 추가. 접수와 겹치는 `recommendation_mode_not_available`·`recommendation_time_basis_unsupported`·`soft_rules_empty`·`soft_rule_field_unknown:<field>`도 같음 |

## 10차 — 이상 징후 후보 대상·범위(scope)별 p95 [확정 사용자 11/15 결정 1~6]

- 배경: 켜진 소프트룰이 없어도(Trainer 처리 시간 후보만 있어도) 추천이 돌아야 하고, 소프트룰 `scope`가 있는데 필드 전체 p95를 내던 문제를 함께 고친다. 숫자는 코드, LLM은 상태·한국어 근거만(변경 없음)
- 사용자 결정(11/15): (1) 후보 p95는 **워커 재계산**(Rule v3 참조 80%, `basis: train_reference_80_p95`) — Trainer 후보 값(train 100%)과 공존하고 두 p95를 기준 표시 없이 나란히 두지 않음 (2) 요청은 `scope.candidateIds` (3) **분할 1회 → 쪽마다 범위 필터** (4) 묶음 상한 32 (5) 추천이 `proposed`가 아닐 때 승인 입력칸 값 = 후보의 Trainer 값(`ruleDraft.threshold`) + "train 전체 p95" 기준 표시(WebUI 몫) (6) adapter 없는 범위 `{processId}`는 **Detector 뜻**(그 processId의 모든 adapter) — 워커 재계산도 모든 adapter 행을 쓴다. Trainer는 adapter 있는 묶음과 겹치는 `{X}` 후보를 `rejected`·`scope_overlaps_adapter_groups`로 낸다(반영됨, [rule-candidates-ui.md](rule-candidates-ui.md) 4절) — `status=candidate`만 받으므로 대상이 되지 않는다

### 구현 상태

| 부분 | 상태 |
|---|---|
| 워커(`threshold_recommendation.py`·`rule_v3_capture.py`): `targets[]` 새 모양·묶음·상한·범위 필터·현재 쪽 0개·`evidence_version` v3 수락·묶음별 폴더·`dataset_id`·GPU 복제본 묶음 분배 | **PR ① 구현**(워커 단위 시험) |
| API 접수(`scope.candidateIds` 검사·후보 조회·`targets[]` 새 모양 기록·`llm_spec` v3 기록·묶음 상한 400), `threshold-stats` `scope`, 화면(후보 선택·`parseRecommendation` 빈 `softRuleIds`·결과 scope·basis 표시), 공용 예시 json([llm-recommendation-job.example.json](llm-recommendation-job.example.json)) | **PR ② 구현**(`thresholdRecommendationContract.ts`·`jobRoutes.ts`·`ruleCandidates.ts` `readRecommendationCandidates`·`thresholdStats.ts`, 화면 [rules-ui.md](rules-ui.md) AI 규칙 추천 절). 공용 예시 3건(`period`·`snapshot`·`candidates` — 마지막은 후보만, 접수 때 읽는 후보 작업 결과 `candidateJob` 포함) |

### 요청(API, PR ② 구현)

```json
{"kind": "recommend_thresholds", "sourceId": "…", "ruleSetRevision": 13,
 "scope": {"softRuleIds": [], "candidateIds": ["<jobId>.t3", "<jobId>.t7"]},
 "data": {"snapshotId": "<후보 결과 snapshotId>", "from": null, "until": null, "timeBasis": null},
 "mode": "data_p95_with_llm_rationale"}
```

| 규칙 | 내용 |
|---|---|
| `scope.candidateIds` | 선택 키, 생략·null = 후보 없음(현행). 배열은 중복 없는 `<jobId>.t<n>` 1~32개(관계 후보 `.r<n>`은 400 `invalid_job_request`) |
| 조합 | `softRuleIds` 키 필수 유지. 후보가 있으면 `softRuleIds: []` 허용(소프트룰 0개 + 후보). 둘 다 비면 400 `soft_rules_empty` |
| 자격 | 후보 `status=candidate`(n ≥ 30)이고 `operator`·`threshold`가 non-null이며 범위 값(`scope.processId`·`scope.adapterType`)이 룰 세트 저장과 같은 원천 값 규칙(`sourceValueValid` — 빈 값·공백만·`\N` 아님, 1~512자)인 것만, 아니면 422 `rule_candidate_not_eligible:<candidateId>`(범위 값 검사는 #215 검토 권고 2 — 그 범위로는 승인 저장도 거절되므로 접수에서 막는다) |
| 출처 | 성공한 `recommend_relations` 작업이면 최신이 아니어도 됨(결과·id 불변). 한 요청은 한 작업의 후보만 — 섞이면 422 `rule_candidate_job_mixed`. 없으면 404 `rule_candidate_not_found`, 후보 결과 머리 이상은 기존 503 코드 |
| snapshot | 후보가 있으면 `data.snapshotId` = 후보 결과 `snapshotId` 필수, 아니면 409 `rule_candidate_snapshot_mismatch`. 같은 요청의 소프트룰도 이 snapshot으로 계산 |
| 상한 | 대상 ≤ 64(소프트룰 32 + 후보 32), **묶음 ≤ 32**. 묶음 33개 이상은 400 `soft_rule_limit_exceeded`(이름 유지, 뜻이 묶음 수로 바뀜). 자동으로 자르지 않고 화면이 나눠 고르게 한다 |
| 검사 순서(API 구현) | 형식 400 → (기존) 출처 404 → `data.snapshotId` 조건 409 `snapshot_conditions_mismatch` → 리비전 409 → 후보: 작업 섞임 422 → 작업·위치 404 → 결과 머리·항목 503 → 자격 422(요청 순서로 첫 위반) → 후보 snapshot 409 → `soft_rules_empty` 400 → 필드 422 → rule_v2 400 → 묶음 상한 400. 거절은 작업·큐 메시지·멱등키를 남기지 않는다 |
| `llm_spec` | PR ② 뒤 API는 rule_v3 작업에 `evidence_version: soft-rule-threshold-v3`를 기록한다(`model_version: rule_v3` 짝, API 상수 하나 — 공용 예시 json 기존 두 예시도 이 값으로 바뀜). 새 대상 모양을 쓰는 작업은 반드시 이 값이어야 한다(워커 검사). `llm_spec`이 rule_v2 값(되돌림 경로)이면 새 대상 모양 요청은 400 `recommendation_targets_require_rule_v3`(되돌린 상태에서 조용히 틀리지 않게 하는 안전 규칙) |
| hash·멱등 | `input_spec`에 `scope.candidateIds`(없으면 null)와 새 `targets[]`가 들어가 `request_hash`·`reuse_key`가 자연히 바뀐다. 배포 전 작업을 같은 멱등키로 다시 보내면 409 `idempotency_payload_conflict`가 날 수 있다([다시 시도]는 새 멱등키) |

### `input_spec.targets[]` 새 모양(워커 PR ① 구현)

| 키 | 규칙 |
|---|---|
| `kind` | `soft_rule` / `candidate`. 키 없음 = `soft_rule`(옛 모양) |
| `id` | 소프트룰 id 또는 `candidateId`. 전체에서 중복 없음 |
| `field` | 카탈로그 `fieldId`(1차 후보는 `elapsed_ms`만 — Trainer 유일 필드). 키가 없거나 문자열이 아니면 `invalid_recommendation_targets`, 카탈로그에 없으면 `soft_rule_field_unknown:<field>` |
| `scope` | 적용 범위. null(필드 전체) 또는 v3 소프트룰 scope **정규형**: `processId` 필수, 키는 `processId`·`adapterType`·`processHubId` 안(표시용 `interfaceId` 제외), 값 있는 키만(null 값 키 없음), 값은 비지 않은 문자열. 키 없음 = null(옛 모양). 위반은 고쳐 쓰지 않고 `invalid_recommendation_targets` |
| `threshold` | 소프트룰은 현재 값(`currentThreshold`), 후보는 **null**(워커가 다시 계산, 값이 있으면 `invalid_recommendation_targets`) |
| 순서·재필터 | 소프트룰(저장 순서) → 후보(요청 순서). 워커는 `scope.softRuleIds` 재필터를 소프트룰에만, `scope.candidateIds` 재필터를 후보에만 적용한다. `candidateIds`가 null인데 후보 대상이 있으면 `invalid_recommendation_targets` |
| 묶음 | 같은 `(field, scope 정규형)`의 소프트룰·후보는 결과 항목 1건으로 합친다. 묶음 33개 이상 `soft_rule_limit_exceeded`, 대상 65개 이상 `invalid_recommendation_targets`, 필터 뒤 대상 0개 `soft_rules_empty` |
| 새 모양 표시 | `kind: candidate`이거나 null 아닌 `scope`가 하나라도 있으면 새 대상 모양 — `('rule_v3', 'soft-rule-threshold-v3')`만 실행. 같은 rule_v3에 `soft-rule-threshold-v2`면 `recommendation_llm_spec_invalid`, rule_v2면 `recommendation_targets_require_rule_v3`. `kind: soft_rule`·`scope: null`을 명시만 한 대상은 옛 모양과 같아 세 조합 모두 받는다 |

### 범위 필터·표본(워커 PR ① 구현)

- 범위 필터 = Detector 판정과 같은 함수(`detector.soft_rules.in_scope`)를 그대로 쓴다: 범위의 값 있는 판정 키(`processId`·`adapterType`·`processHubId`)마다 행의 PROCESS 원래 값(`process_id`·`adapter_type`·`process_hub_id`, trim 없음)과 **정확 일치**. 범위에 없는 키는 대조하지 않는다(adapter 없는 범위 = 모든 adapter 행, 결정 6). 행 값이 없음(`graph_value_absent`: null·빈 값·공백·`\N`)이거나 문자열이 아니면 그 행은 범위 밖
- rule_v3: 작업 전체 train 표본을 한 번 80/20으로 나눈 뒤(보류 창 회피 포함) 참조·현재 쪽마다 필터. 참조 유효 값 < 20 → `no_applicable`(현행 문장), 현재 쪽 유효 값 0개 → `no_applicable`(Rule v3 경로 절 코드 선처리). 둘 다 번들·LLM 호출 전이고 `attempts: 0`
- rule_v2: 같은 필터를 `reference_sample` 앞에 둔다. 새 대상 모양은 rule_v2에서 거절하므로 실제 범위는 늘 null(필터는 방어용)
- 후보 p95 표본은 Trainer와 다르다(결정 1): 워커 = 후보 snapshot train → 범위 필터 → 원천 시각 앞 80%, Trainer = train 100%. 결과 `basis`·`evidence.n`이 이 차이를 보이고, 화면은 두 값을 기준 표시 없이 나란히 두지 않는다
- 묶음별 작업 폴더 `g<n>`(n = 결과 항목 순번, 0부터). 번들 capture `dataset_id`는 범위 없는 묶음이면 현행 `snapshot_<snapshotId>_<field>` 그대로(번들 `source_version` hash를 거쳐 Rule 입력 ID에 들어가므로 옛 요청의 프롬프트를 바꾸지 않음), 범위 있는 묶음이면 `snapshot_<snapshotId>_<field>_g<n>`. 범위 값은 폴더·`dataset_id`·CSV·프롬프트 어디에도 넣지 않는다 — 프롬프트 틀·번들 형식은 그대로다

### 배포·되돌림 순서

- 배포: **워커(`llm` 이미지) 먼저 → API(`backend`)·화면(`frontend`)**. 새 워커는 `soft-rule-threshold-v2`·`v3`를 모두 받고 옛 모양 대상의 결과는 현재 쪽 0개 처리 외에는 같다(`scope: null`·`candidateIds: []`만 추가). Trainer·Detector 이미지도 `worker/`를 복사하지만 동작 변경은 없다
- 되돌림: **API·화면 먼저 → 워커 나중**. API가 새 대상 모양을 보내는데 워커만 옛 버전이면, 옛 워커는 `kind`·`scope`를 몰라 후보를 소프트룰로 읽는다(필드 전체 p95 또는 `soft_rules_empty`). 새 대상 모양 작업은 `soft-rule-threshold-v3`를 기록하므로 옛 워커는 `recommendation_llm_spec_invalid`로 **조용한 오답 대신 실패**한다
- `total_count` 후보(후속): Trainer 누적기 일반화 + API `thresholdItem` 확장이 필요하고 그 변경은 API 먼저(반대면 후보 목록 503). 이 절의 워커 먼저 순서와 별개
- GPU: 프롬프트·번들 형식 불변, 학습 없음. 다음 GPU 창에 후보 1~2개로 화면 확인 1회(미실행)

## 생성 서비스 GPU 차례 — 대화형 설명 우선 [확정 코드 사실]

- GPU 생성은 한 번에 하나(`generation_service.GenerationGate`). GPU가 비면 기다리는 `explain`(대화형)을 `rule`·`feature` 생성(작업: `recommend_*`·`recommend_thresholds`)보다 먼저 넣고, 같은 등급은 도착 순. 실행 중인 생성은 끊지 않음
- 대기 상한: `explain`만 `LLM_EXPLANATION_WAIT_SECONDS`(기본 10초). 생성 시간 상한 `LLM_EXPLANATION_SECONDS`(기본 60초)와 따로 셈. 작업 대기는 상한 없음(생성 상한 `LLM_RECOMMENDATION_SECONDS` 기본 240초·출력 한도 그대로)
- 상한 초과: `generation_busy` — HTTP 503 본문 `{"code":"generation_busy"}`(장애 503 `generation_failed`와 본문 코드로 구분), 클라이언트와 같은 프로세스 호출 모두 `GenerationUnavailable("generation_busy")`. 요청 검증·토큰 예산 거부는 대기 전에 판단(422 그대로)
- 작업 결과: 이전 형식(`saved-decision-v1`) `explain` 작업은 `error_code` `generation_busy`로 재시도(다른 재시도 실패의 `job_processing_failed`와 같은 흐름 — 5초 뒤 다시 대기, 최대 시도 3회 뒤 `failed` + `generation_busy`). 화면은 오류 코드를 그대로 보임. (12차) 새 형식(`summary-sentences-v1`)은 재시도 없이 바로 `failed` + `generation_busy`(아래 "AI 근거 요약 다듬기" 절)
- (12차) `polish_explanation`도 대화형 등급이다(같은 차례·대기 상한·생성 시간 상한 `LLM_EXPLANATION_SECONDS`·출력 512 토큰). 모델만 기반 모델이 아니라 따로 적재한 다듬기 모델이다
- 소비자: `llm_worker`는 같은 모델로 두 소비자를 돌림 — 주 소비자(역할 `llm`, 기존 교대 소비 `llm_recommendation`↔`llm_explanation` 그대로)와 설명 전용 소비자(역할 `llm_explanation`, `llm_explanation` 큐 + 이전 `training` 큐의 `explain`만, 자기 큐만 정리). 추천 생성이 GPU를 쓰는 동안 설명 전용 소비자가 받은 설명은 다음 차례에 먼저 들어감. 설명 전용 소비자가 예외로 끝나면 기록 후 5초 뒤 다시 시작하고, 그동안 설명은 주 소비자가 처리
- 같은 순서가 HTTP `/generate` 요청에도 적용됨(한 프로세스 안의 모든 생성 호출)

## AI 근거 요약 다듬기 — `explain` `summary-sentences-v1` [12차, 워커 쪽]

설명 작업의 새 형식이다. 백엔드가 저장 근거로 코드 문장을 만들고(문장 틀·문구는 백엔드), 워커는 경량 모델로 한 문단으로 다듬은 뒤 사실 보존을 확인만 한다. 문장 생성·근거 고르기·확인할 것 고르기는 LLM이 하지 않는다. 화면 동작·판정 상세 응답·접수 재사용은 [analysis-ui.md](analysis-ui.md)와 백엔드 계약 몫이다.

- `llm_spec`: `evidence_version` `summary-sentences-v1`, `prompt_version` `evidence-polish-v1`(지시문 + 아래 확인 규칙 집합, 하나라도 바꾸면 올림), `output_schema_version` `evidence-polish-result-v1`, `model_version` = 접수 때 생성 서비스 `/health`의 `polishModel`. 워커는 앞의 셋이 이 값이 아니거나 `model_version`이 빈 문자열이면 `explanation_input_rejected`
- 접수 입력(`input_spec`): `kind`(`explain`)·`sourceId`·`decisionId`·`templateVersion`(빈 문자열 아님), `configVersionId`(판정 구성 — 빈 문자열이 아닌 문자열, 구성 없는 판정(v04)은 null, 키는 늘 있음), `sentences`(문자열 1~8개, 각 300자·합 1,200자 이하 — 코드 포인트, 빈 문장·줄바꿈·사용 영역 문자 U+E000–U+F8FF 없음), `stateWords`(판정 낱말 순서열), `valueTokens`(0~16개, 각 1~120자, 각각 어떤 문장에 그대로 있음), 선택 `allowedNumbers`(숫자 문자열 — 쉼표·소수 허용, 단위 없음). 그 밖의 키가 있으면 거부. 워커는 원천 근거를 읽지 않고 `input_evidence_hash`를 다시 확인하지 않는다
- 워커 흐름: ① 입력 형식 확인 ② 코드 문단(`codeText` = `sentences`를 공백 1칸으로 이음)에서 아래 가림·순서열을 직접 뽑아 `stateWords`(순서열)·`allowedNumbers`(정규화한 숫자의 다중집합)와 대조 — 다르면 `explanation_input_rejected` ③ `polish_explanation` 호출(입력 `{sentences}`만) ④ 출력(앞뒤 공백 제거)을 확인 규칙으로 검사
- 생성 서비스 `polish_explanation`: 평문 출력(JSON 아님), 탐욕 생성. 응답 `{output: 문자열, truncated, modelVersion, promptVersion}`. `modelVersion` = `<모델 이름>@<모델 폴더 hash 앞 8자리>`(폴더 안 모델 파일의 상대 경로·SHA-256 목록의 SHA-256, 시작 때 1회 — 숨김 경로 `.cache/`·`.git*` 등과 `*.lock`·`*.incomplete` 같은 내려받기 부산물은 뺌). 다듬기 모델을 적재하지 않았으면(설정 없음·적재 실패, 시작 로그 `polish_disabled: <코드>`) 이 종류만 생성 서비스 꺼짐이고 `/health`는 `polishModel: null`
- 가림(추출 전에 이 순서로, 각 단계 안에서는 긴 것 먼저, 토큰마다 사용 영역 자리표 한 글자): (가) `valueTokens` 원문 (나) 시각 `\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?` (다) 이름표 `처리량(TOTAL_COUNT)`·`SUCCESS_COUNT`·`ERROR_COUNT`·`RETRY_COUNT`·`오류 비율 값`·`처리 시간`·`이상 규칙`·`이상 징후 규칙`·`관계 규칙`·`머신러닝 모델`·`정상 프로토콜`(라틴으로 시작·끝나는 이름표는 앞뒤에 `[A-Za-z0-9_]`가 붙으면 가리지 않음) (라) 라틴 식별자 `[A-Za-z](?:[A-Za-z0-9_]|[.:\-](?=[A-Za-z0-9_]))*` — 단 숫자 바로 뒤(공백 0~1칸)의 `ms`(뒤에 `[A-Za-z0-9_]` 없음)는 단위로 남김. 입력·출력에 원래 있던 사용 영역 문자는 자리표와 섞이지 않게 비문자로 바꿈(출력이면 검사 4 실패)
- 순서열(가린 문자열에서): 판정 낱말 `이상 신호 없음`·`정상 여부 미확인`·`이상 징후`·`평가 불가`·`이상`·`정상`(긴 것 먼저) / 숫자 `-?[0-9][0-9,]*(\.[0-9]+)?` + 바로 뒤(공백 0~1칸) 단위 `ms`·`%`·`초`·`분`·`시간`·`개`의 `(숫자, 단위)` 쌍(정규화: 쉼표 제거, 소수부 끝 0·끝 점 제거, 정수부 앞 0·부호 보존, 단위 없으면 빈 문자열) / 순서 낱말(부분 문자열) `이번`·`평소`·`기준`·`길어`·`짧아`·`커서`·`작아`·`늦어`·`높아`·`낮아`·`달라`·`같아`·`지연`·`단축`·`벗어나`·`해당해`·`없어`·`않`·`아니`·`못`
- 확인 규칙(이 순서, 첫 실패 코드만): 1 `polish_empty`(빈 출력) 2 `polish_multi_paragraph`(줄바꿈) 3 `polish_too_long`(코드 포인트 수 > `L + min(60, ⌈0.2·L⌉)`, L = `codeText` 길이) 4 `polish_not_korean`(가린 문자열에서 숫자의 부호·단위 `ms`를 뺀 뒤 한글·ASCII 숫자·공백·기호·자리표 밖 문자 — 라틴·한자·가나·제어 문자 등) 5 `polish_markup`(허용 밖 기호. 허용 = `. , ( )` + 가린 코드 문단에 있는 `≥ > ≤ < → · — % ~`. 코드 문단이 그렇게 시작하지 않는데 줄머리 목록 기호로 시작해도 실패) 6 `polish_name_changed`(가림 토큰 (종류, 원문) 다중집합이 다름) 7 `polish_number_changed`(`(숫자, 단위)` 순서열이 다름) 8 `polish_state_changed`(판정 낱말 순서열이 다름) 9 `polish_direction_changed`(순서 낱말 순서열이 다름) 10 `polish_forbidden`(아래 패턴 중 하나라도 코드 문단보다 많이 나옴)
- 금지 패턴: `원인`·`장애`·`때문`·`인해`·`인한`(앞 글자 `확`이면 제외)·`탓`·`추정`·`가능성`·`보장`·`위험`·`주의`·`심각`·`문제`·`필요`·`권장`·`하세요`·`바랍니다`·`참고용`·`담당자`·`아닙니다`·`많다`·`많아`·`많은`·`적다`·`적어`·`적은`, `건`(앞이 숫자·공백이고 뒤가 한글이 아닐 때만)
- 확인 규칙의 통과·실패·기대 대체 예는 공용 벡터 [evidence-polish-check-vectors.json](evidence-polish-check-vectors.json)(워커 시험 `test_evidence_polish`가 읽음). 활용 바꿈(`길었고`)·절 순서 바꿈·ISO가 아닌 원천 시각 값 바꿈은 확인 실패(코드 문장 표시)가 맞는 동작이다

| 상황 | 작업 | 결과 |
|---|---|---|
| 확인 통과 | `succeeded` | `paragraph.source=llm`, `check={status:"passed", code:null}` |
| 확인 실패 | `succeeded` | `paragraph = {text: codeText, source: "code"}`, `check={status:"failed", code:<첫 실패 코드>}` |
| 출력 잘림(출력 한도) | `succeeded` | 코드 문장, `check={status:"not_run", code:"generation_incomplete"}`(검사 안 함) |
| 생성 서비스 꺼짐·연결 실패·다듬기 모델 없음 | `failed` `generation_service_unavailable` | 없음 |
| GPU 차례 대기 초과 | `failed` `generation_busy`(재시도 없음) | 없음 |
| 생성 시간 초과 | `failed` `generation_timeout` | 없음 |
| 생성 응답 모델 id ≠ `model_version` | `failed` `llm_model_mismatch` | 없음 |
| 생성 응답 지시문 버전 ≠ `prompt_version` | `failed` `generation_prompt_version_mismatch` | 없음 |
| 생성 요청 거부(토큰 예산 등)·응답 형식 어긋남 | `failed` `generation_contract_rejected` | 없음 |
| 입력 형식·재계산 불일치·`llm_spec` 고정 값 다름 | `failed` `explanation_input_rejected` | 없음 |

- 결과(`result_summary` = `run_llm_results.output`): `{outputSchemaVersion: "evidence-polish-result-v1", decisionId, templateVersion, codeText, paragraph: {text, source}, check: {status, code}, modelVersion}`(`modelVersion` = `llm_spec.model_version`). 이전 형식 결과(`facts`·`nextChecks`·`causeStatus`·`contributionStatus`)와는 `outputSchemaVersion` 유무로 가른다
- 실패는 모두 워커가 종료 실패로 끝낸다(재시도 없음). 접수 쪽 `run_jobs.max_attempts = 1`과 같은 결과다
- 배포: 다듬기 모델은 선택 override `infra/compose.evidence-polish.yaml`로 켠다(환경 변수는 그 파일과 `infra/DOCKER_DEPLOYMENT.md`). 첫 배포 모델은 하나이고, 바꾸면 `model_version`이 바뀌어 재사용 키가 갈린다

## 생성 서비스 가용성 — 꺼져 있으면 LLM 작업 접수 거절 [확정 사용자 11/14]

- 배경: 생성 서비스(`llm` 컨테이너)가 꺼져 있으면 LLM 작업이 소비자 없이 `queued`로 남고 화면은 `요청 중`을 계속 보인다. 사용자 결정 = 요청 버튼 비활성 + 상태 코드, 안내 문구 추가 없음
- API 내부 확인: `GET ${LLM_HEALTH_URL:-http://llm:8010/health}`, 제한 1.5초(연결부터 응답 본문까지 전체). 가용 = HTTP 200이고 본문 `status`가 `"ready"`(`generation_service.serve`의 `/health` 응답 `{"status":"ready","polishModel":<다듬기 모델 id 또는 null>}`, (12차) `polishModel`은 AI 근거 요약 다듬기 절). 연결 거부·DNS 실패·시간 초과·200 아님(3xx는 따라가지 않음)·본문 형식 틀림(JSON 아님·4KB 초과·`status`가 다름)은 모두 꺼짐
- 의미: `llm_worker`는 모델을 적재한 뒤에야 HTTP를 띄우고 주 소비자가 끝나면 프로세스도 끝난다 → `/health` 200 = 모델 적재 + 소비자 살아 있음. 적재 중(`/health` 없음)은 꺼짐
- 캐시: 가용·꺼짐 결과 모두 확인이 끝난 시각부터 10초(API 프로세스 메모리, 재시작하면 비어 있음). 확인 중에 들어온 요청은 같은 확인을 기다린다 — 동시 요청이 몰려도 `/health`는 한 번
- `GET /api/llm/status`(로그인 사용자, 역할 무관, 미인증 401) → `{"available": boolean, "checkedAt": "<ISO>", "errorCode": null | "generation_service_unavailable"}`. `available=false`면 `errorCode`는 늘 `generation_service_unavailable`. `checkedAt`은 그 결과를 얻은 확인 시각(캐시된 결과면 캐시된 시각). `Cache-Control: no-store`
- 접수 거절: `POST /api/jobs` 중 LLM 작업 종류만 — `explain`·`recommend_thresholds`·`explain_protocol`(코드 상수 `LLM_JOB_KINDS` 하나, `backend/src/services/generationAvailability.ts`. 프로토콜 설명 등 새 LLM 작업 종류는 이 집합에 더한다). 확인 결과가 꺼짐이면 HTTP 503 `{"code":"generation_service_unavailable"}`
  - 순서: 요청 형식(400)·폐지 종류(422)·역할(403)·멱등키 형식(400) 검사 뒤, 트랜잭션·멱등 처리·출처·판정 조회 전. 작업 행·큐 메시지·멱등키를 남기지 않으므로 켜진 뒤 같은 `Idempotency-Key`로 다시 요청할 수 있다
  - 이미 작업이 만들어진 같은 `Idempotency-Key`로 다시 요청해도 꺼짐이면 503이다(멱등 처리보다 확인이 먼저). 기존 작업은 `GET /api/jobs/:id`로 읽는다
  - 기존 결과 재사용(`reuse_key`)도 확인 뒤이므로 꺼짐이면 같은 503이다. 이미 끝난 결과 읽기(`GET /api/jobs/:id`, 판정 상세 `explanation`)는 영향 없음
- 구분: `generation_busy`(GPU 차례 대기 초과, 서비스는 살아 있음, 작업 오류 코드 — GPU 차례 절)와 다르다. 워커 작업 실패 코드 `generation_service_unavailable`(실패 표 첫 줄 — 접수 뒤 생성 호출 실패)과 이름이 같지만, 이것은 접수 단계 HTTP 응답이고 작업이 생기지 않는다
- 막지 않는 것: `POST /api/rule-set/threshold-stats`(코드 p95, LLM 없음), LLM이 아닌 작업 종류(`train`·`evaluate`·`compare`·`recommend_relations` 등), 이미 `queued`·`running`인 LLM 작업(그대로 두고 생성 서비스가 켜지면 소비)
- 화면: LLM 요청 버튼이 있는 화면(이상 분석 판정 상세 `AI 판정 근거 요약`, 룰 설정 `AI 규칙 추천`)은 들어갈 때 1회 + 그 화면에 있는 동안 30초마다 조회한다. 꺼짐이면 요청 버튼 비활성 + `<항목> · generation_service_unavailable`(새 설명 문장·배지 없음), 이미 요청 중인 작업은 `요청 중` 표시를 그대로 두고 같은 자리에 코드를 함께 보인다. 상태 조회 자체가 실패하면(네트워크 등) 버튼을 막지 않는다 — 서버가 접수에서 같은 확인으로 503을 돌려준다. 세부는 [analysis-ui.md](analysis-ui.md)·[rules-ui.md](rules-ui.md)
- 배포: compose에 `LLM_HEALTH_URL`을 두지 않는다 — 기본값이 `infra/compose.yaml`의 `llm` 서비스(`api`와 같은 `runtime` 네트워크, `DESIGN_MODEL_HOST: 0.0.0.0`, `serve` 기본 포트 8010, healthcheck도 같은 `/health`)와 맞다. `llm` 서비스가 없는 구성(`compose.demo.yaml`)은 DNS 실패 → 꺼짐. 주소가 다를 때만 `api` 환경에 넣는다(빈 값은 기본값)
- 시험: `backend/tests/generation-availability.test.mjs`(가짜 `/health`: ready·503·loading·JSON 아님·응답 없음·연결 거부·DNS 실패·10초 캐시·확인 공유), `backend/tests/jobs.integration.mjs`(꺼짐 → 상태 조회·503·작업/큐/멱등키 없음·LLM 아닌 작업 접수, 켜진 뒤 같은 멱등키 201). LLM 작업을 접수하는 다른 통합 시험은 켜진 상태의 가짜 `/health`(`backend/tests/fake-llm-health.mjs`)를 쓴다

## 규칙별 수식 추천 — `POST /api/rule-set/threshold-stats` [신규, 관리자, 동기, LLM 없음]

- 용도: 규칙 행 [수식 추천] — 선택 기간 데이터의 p95를 바로 계산해 값·표본 수를 보여 주고 [적용]으로 입력칸에 채움(11/02 9.2). 작업 큐·LLM 호출 없음. 기준 기간은 **세 가지 중 선택** [확정 11/02 9.4]: (a) 현재 운영 ML 모델이 학습에 쓴 **첫 기간 시작 ~ 현재** (b) 최근 1개월·3개월 (c) 사용자 지정 기간
- **구현 주체: API(TypeScript) + PostgreSQL SQL** [제안]: `percentile_cont(0.95)`(선형 보간 — numpy `linear`와 같은 정의)를 `data_processes ⋈ data_observations`에 적용. 파생 필드(`elapsed_ms`·`error_ratio`)는 카탈로그 AST를 SQL 식으로 번역 — `datetime_diff` = **`data_observations.allowed_values`의 `end_time_raw`·`start_time_raw`를 timestamp로 해석한 차**(수집기는 `parsed_times`에 시각 값을 두지 않고([collector-metrics.md](collector-metrics.md) 1.1) Detector도 `allowed_values`만 읽으므로 `parsed_times`는 원천이 아님 — Detector 11/04 확인, #112; 워커 `derived_expression`의 `START_TIME`/`END_TIME` → `start_time_raw`/`end_time_raw` 매핑과 동일), `divide` = NULLIF 분모. `datetime_diff` 번역의 결측·실패 처리는 카탈로그 `sourceMissingPolicy`와 같은 뜻: `start_time_raw` 없음·timestamp 해석 실패 → `unavailable`, `end_time_raw` 없음 → `not_applicable` — 어느 쪽이든 그 행은 표본(`n`)에서 제외. '값 없음' 기준은 Detector 계약(detection-decision.md 4.1 원칙·5.2 실행, #112 정정 11/04 — dev 미병합, `feat/112-detection-decision-impl`)과 같게 — null뿐 아니라 빈 문자열, 공백만 있는 문자열, `\N`(앞뒤 공백 제외)도 값 없음으로 본다. 값 없음(빈 문자열·`\N` 등)과 해석 불가(형식 불일치) 어느 쪽도 SQL 오류 없이 NULL로 떨어지게 **안전한 변환**을 쓴다(값 없음은 앞 문장 정책대로 — END_TIME이면 `not_applicable`, START_TIME이면 `unavailable`; 해석 불가는 `unavailable`). `''`를 timestamp로 직접 cast하면 쿼리 전체가 실패(실제 END_TIME 빈 문자열 다수, Detector 11/04 권고). 구체 SQL 식은 API 구현 몫. Trainer(Python)는 비동기 워커라 동기 API에 부적합. 두 경로의 p95 원값(`evidence.p95`)이 같은 표본에서 일치해야 함(통합 시험 항목). `threshold`와 `status`는 아래 `threshold` 항목대로 다를 수 있음

```json
{
  "field": "elapsed_ms",
  "statistic": "p95",
  "period": {"mode": "custom", "from": "2026-10-01T00:00:00+09:00", "until": "2026-11-01T00:00:00+09:00", "timeBasis": "esb_occurred_at"},
  "sourceId": "<source UUID>",
  "excludedIntervals": []
}
```

- (10차, PR ② 구현) 선택 키 `scope`: 소프트룰 scope 정규형(`{processId, adapterType?, processHubId?}`, `processId` 필수, 값 있는 키만, `interfaceId` 없음) 또는 null·생략(= 필드 전체, 현행). 값은 룰 세트 저장 검증과 같은 원천 값 규칙(빈 값·공백만·`\N` 아님, 1~512자 — `sourceValueValid`). 모양 오류는 400 `invalid_threshold_stats_request`. SQL은 값 있는 키만 실행별 최신 PROCESS 관측의 **`data_observations.allowed_values` 원래 값**(`process_id`·`adapter_type`·`process_hub_id`)과 정확 일치 조건으로 더한다 — Detector `in_scope`가 읽는 같은 값이라(trim 없음, JSON 문자열이 아니거나 없으면 범위 밖) `data_processes` 열과의 차이 문제가 없다(10차 절 범위 필터). 응답은 **최상위 `scope`**에 확정값(정규형 또는 null)을 돌려준다(`recommend_thresholds` `items[].scope`와 같은 자리). `evidence.scope: "period"`(표본 구분)는 그대로라 겹치지 않는다. 화면 [수식 추천]은 범위 있는 규칙이면 그 규칙 `scope`의 판정 키를 보낸다([rules-ui.md](rules-ui.md) 수식 추천 절)
- `statistic` ∈ `p95`(기본) / `p99` / `median` / `max`. `excludedIntervals[]`는 학습 요청과 같은 형식([trainer-service.md](trainer-service.md) 변경 제안 절) — 장애 구간을 빼고 계산할 때(`period.mode=active_model_training_start`에서 활성 모델의 학습 제외 구간을 자동으로 넣을지는 Q-L6, 기본은 넣지 않음)
- `period.mode` [제안, 9.4 세 선택지의 형식]

| `mode` | 서버가 정하는 `[from, until)` | 오류 |
|---|---|---|
| `active_model_training_start` | `from` = 현재 **활성 모델 집합**의 각 버전 `trainingSelection.trainingPeriod.from`([model-version.md](model-version.md)) 중 **가장 이른 값**(활성 모델 N개면 최솟값), `until` = 요청 시각(서버). `from`·`until`은 요청에 없어야 함. **v1·v2 운영 상태**(활성 집합이 비어 있고 `ops_system_state.active_config_version_id`가 v1·v2 행): 그 행의 train 요청 `from`(`run_jobs.input_spec`)을 `trainingPeriod.from`으로 **투영**해 같은 규칙으로 사용(model-version.md v1·v2 투영 절); 투영값이 null(train 기간 없음)이면 422 `active_model_period_unknown` + `{hint: "custom"}` | 활성 모델 0개(v1·v2 활성도 없음) → 422 `active_model_missing`; v1·v2 투영 불가 → 422 `active_model_period_unknown`; 기간 상한은 Q-L5(추천: (a)만 366일 면제, 서버가 일 단위 표본화) |
| `recent_1m` / `recent_3m` | `until` = 요청 시각, `from` = `until` − 1개월 / 3개월(달력 월, `timezone` 기본 `Asia/Seoul`로 같은 날짜·시각) | — |
| `custom` | 요청의 `from`·`until`(ISO 8601 오프셋 필수, `from < until`) | 둘 중 하나라도 없음 400 `invalid_threshold_stats_request`; > 366일 400 `period_too_long` |

- `timeBasis` 기본 `esb_occurred_at`(생략 가능). `custom`·`recent_*`는 상한 366일(동기 API 응답 시간 보호); (a)의 상한은 Q-L5(추천: 면제 + 일 단위 표본화). 응답 `evidence.period`에 **서버가 확정한** `{mode, from, until, timeBasis, resolvedFrom}`를 돌려줌(`resolvedFrom`은 (a)에서 기준이 된 모델 버전 id, 그 외 null)
- 표본 규칙은 LLM 작업과 동일: `status=S`·명시 anomaly 라벨 제외·결측 제외(필드 값 null 행 — 예 `elapsed_ms`의 END_TIME 누락 — 도 표본에서 제외). snapshot 분할은 쓰지 않음(기간 전체) — `evidence.scope: "period"`로 구분

```json
{
  "field": "elapsed_ms", "statistic": "p95",
  "operator": "gt", "threshold": "7210.5", "displayThreshold": "7211", "unit": "ms",
  "source": "data_p95", "status": "proposed",
  "evidence": {"n": 51230, "min": 12.0, "max": 184233.0, "p95": 7210.5, "quantileMethod": "percentile_cont",
               "exclusionRule": "status=S, explicit anomaly labels excluded, excludedIntervals=0",
               "scope": "period", "period": {"mode": "active_model_training_start", "from": "…", "until": "…", "timeBasis": "esb_occurred_at", "resolvedFrom": "<config UUID>"}},
  "rationale": null
}
```

- `status`: `proposed` / `no_applicable`(n < 20 또는 분산 없음 — `minimum_n 20` 현행과 동일) / `rejected`(직렬화 실패). `rationale`은 항상 null(LLM 없음). 위험도 없음
- `threshold`는 고정소수 문자열이고 원값은 `evidence.p95`다 (#116). 형식은 위 `recommend_thresholds` 결과와 같지만 **계산 구현은 다르다**: 이 API는 number 연산(숫자 필드는 `Number(value.toPrecision(15))`로 15자리로 줄이고, 정수 필드는 `Math.round`만 하고 줄이지 않음), 워커는 `Decimal(str(value))`를 15자리 `ROUND_HALF_EVEN`으로 줄인 뒤 정수 필드를 `ROUND_HALF_UP`으로 정수로 만든다. 그래서 같은 원값도 동률·경계의 `threshold` 끝자리뿐 아니라 `status`까지 달라질 수 있다. 양쪽이 값을 낸 경우 어느 쪽 값이든 룰 세트 저장 검증은 통과한다. 값 예(이 API / 워커): 정수 `2.4999999999999996` → 2 / 3, 정수 `-2.5` → -2 / -3, 숫자 `1234567890123445` → 1234567890123450 / 1234567890123440, 숫자 `1.000000000000005` → 1.00000000000001 / 1. `status` 예(이 API / 워커): `retry_count` `1234567890123456` → `rejected`(`threshold_serialization_failed`) / `proposed` `1234567890123460`, `elapsed_ms` `5e-324` → `proposed` `"0"` / `rejected`(`threshold_serialization_failed`)
- 오류: 400 `invalid_threshold_stats_request`, 422 `soft_rule_field_unknown:<field>`, 400 `period_too_long`, 422 `active_model_missing`(mode (a)에서 활성 모델 집합이 비어 있고 v1·v2 활성도 없음), 422 `active_model_period_unknown`(mode (a)에서 v1·v2 활성 행의 학습 기간 투영 불가 — 응답 `{hint: "custom"}`)
- 응답 시간 목표 ≤ 3초(인덱스 `(source_id, source_log_occurred_at)` 요구 — [trainer-service.md](trainer-service.md) 거래량 API와 공유)

## 작업 상태 ↔ 화면(11/02 9.2)

| 화면 | API |
|---|---|
| [AI 규칙 추천](화면 이름, 사용자 11/04) 실행 중(진행 표시) | `status` queued/running; 같은 멱등키 재요청은 `reuse_key`로 기존 작업 |
| 생성 서비스 꺼짐: [AI 규칙 추천] 비활성 + `AI 규칙 추천 · generation_service_unavailable`, 실행 중이면 같은 줄에 코드 | `GET /api/llm/status` `available=false`, 접수는 503 `generation_service_unavailable`(가용성 절) |
| 결과 표 5열(선택/규칙(대상 필드)/임계값(현재 → 추천, 조건·단위 포함, 원값 툴팁)/상태/근거, 사용자 11/04) | `items[]` 그대로 받고 표시만 합침. 출처 `source`(데이터 p95 / AI 제안)는 근거 칸 첫 줄 |
| [적용] | 서버 호출 없음 — 입력칸 채움, 위험도 기본 주의, 저장해야 운영 반영 |
| 실패 + [다시 시도] | `failed` + `errorCode`(부분 결과 없음). 새 멱등키로 재요청 |
| 시간 초과 | `failed` + `generation_timeout` — 별도 문구 |
| [수식 추천](규칙 행) — 기간 선택(운영 모델 학습 시작~현재 / 최근 1개월 / 최근 3개월 / 직접 지정) | `POST /api/rule-set/threshold-stats` 동기 응답, `period.mode` |

## 마이그레이션·코드 영향(구현 Issue, 사실 기반)

| 대상 | 변경 |
|---|---|
| `job_kind` enum | `recommend_thresholds` 추가(`002:7`, 011에서 `recommend_methods` 등 추가 방식과 동일) — PostgreSQL. 이 표의 DB 세 행은 migration 017(#124)로 반영됨 |
| `run_jobs_v03_contract` CHECK(`011:13-32`) | `llm_recommendation` 분기 kind 목록에 `recommend_thresholds` 추가(PG 누락 1) |
| `erd_v03_validate_job`(`011:60, 76-77`)·`run_llm_results_kind_check`(`011:36-37`)·`run_llm_results_check`(`011:39-41`, `decision_id IS NULL` 분기) | llm_spec 필수·완료 결과 필수·kind CHECK 2개에 `recommend_thresholds` 추가(PG 누락 2) |
| `jobRoutes.ts`·`thresholdRecommendationContract.ts` | **구현(#121)**: 요청 형식·대상 고정·`llm_spec`은 `thresholdRecommendationContract.ts`, `jobRoutes.ts`는 `KINDS`·관리자 검사 2곳·큐 선택·리비전 읽기(`ruleSetStore.readCurrentSoftRules`). `POST /api/rule-set/threshold-stats`는 `ruleSetRoutes.ts` |
| `trainer/runner.py` | `_execute`에 `recommend_thresholds` 분기: 룰 세트 스냅샷 → 카탈로그 필드 → `definitions` 변환 → 현행 `recommend_rules` 재사용 → 결과를 추천 항목으로 변환(Rule 초안 생성 제거); `_finish` kind 집합에 추가 |
| `research_design.py:222-224` | DSL 값 `str(request["baseline"]["value"])` → `Decimal` 고정소수 직렬화(공용 함수), 실패 시 항목 `rejected` |
| `generation_client.py` | `generation_timeout` 분리: `report_timeout=True` 호출만(위 실패 절). 503 본문 `generation_busy`·같은 프로세스 `GenerationBusy` → `GenerationUnavailable("generation_busy")`(GPU 차례 절) |
| `llm_worker.py`·`generation_service.py` | `DesignModel.generate("rule", …)` 어댑터 그대로(입력 형식 불변). (나) 모드 어댑터는 LLM 팀원. GPU 구간은 `GenerationGate`(GPU 차례 절). (7차) `RULE_V3_BUNDLE_ROOT`가 있으면 같은 Base에 Rule v3 경로를 붙임(`rule_v3_runtime.py`·`rule_v3_capture.py`) |
| (7차, API·WebUI 세션) `backend/src/routes/thresholdRecommendationContract.ts` | rule_v3 전환 시 `llm_spec` 값: `model_version: 'rule_v3'`, `evidence_version: 'soft-rule-threshold-v2'`(10차 PR ② 뒤 `soft-rule-threshold-v3`), `prompt_version: 'feature-rule-spec-rule-v3-ko'`(`reuse_key` 계산에도 들어감, 반영됨 — 위 `llm_spec` 행). `output_schema_version`은 그대로. 워커는 두 조합을 모두 받으므로 API 값만 바꾸면 전환·되돌림이 된다. 화면: `basis`·`heldWindowExclusion`·`attempts` 표시, 실행 중 `resultSummary.progress` 표시, rule_v3에서 도달하는 `deferred`·`request_evidence` 문구 |
| (7차) 이미지·배포 | `Dockerfile.generation`: gcc·libc6-dev(xgrammar triton 커널 빌드), `PYTHONUTF8=1`, `TRITON_CACHE_DIR=/tmp/triton`. `requirements-generation.txt`: `xgrammar==0.2.3`, `huggingface-hub==1.33.0`. compose: 기본 `compose.yaml`은 그대로, 선택 override `compose.rule-v3.yaml`이 `llm`에 `RULE_V3_BUNDLE_PATH` → `/rule-v3-runtime` 읽기 전용과 `RULE_V3_BUNDLE_ROOT`를 더함 |
| `queues.md` 7행 | `recommend_thresholds` 추가([queues.md](queues.md) 변경 제안 절) |
| (10차, PR ① 구현) 워커 `threshold_recommendation.py`·`rule_v3_capture.py`·`rule_v3_check.py` | `targets[]` 새 모양·(필드, scope) 묶음(`TargetGroup`)·묶음 ≤ 32·대상 ≤ 64, `evidence_version` v3 수락, 범위 필터(Detector `in_scope`), 현재 쪽 0개 `no_applicable`, 묶음별 폴더·`dataset_id`, 복제본 분배 단위 = 묶음. 큐 payload·DB·migration 변경 없음 |
| (10차, PR ② 구현) API `thresholdRecommendationContract.ts`·`jobRoutes.ts`·`ruleCandidates.ts`(`readRecommendationCandidates`)·`thresholdStats.ts`, 화면 `ruleAiApi.ts`·`RuleAiPanel.tsx`·후보 승인 흐름, 공용 예시 json·`jobs.integration.mjs`·워커 시험의 예시 이름 고정 | 10차 절 요청·`llm_spec` v3 기록·`threshold-stats` `scope`. 화면은 항목의 `softRuleIds`가 비면 `candidateIds`가 1개 이상이고 요청한 후보 안에 있는지로 검사. 공용 예시 json 기존 두 예시의 `llm_spec.evidence_version`·위 `llm_spec` 행 문자열을 함께 바꿨다. 큐 payload·DB·migration 변경 없음 |

- GPU·외부 생성 서비스 호출은 사용자 승인 환경에서만. 이 계약은 호출을 요구하지 않음(11/02 9.1 조사도 가짜 LLM으로 서버 수용 범위만 확인)

## 확정 요약·결정 질문

- 확정: `recommend_rule`·`recommend_feature` 폐지 경로 / 집계값만 입력(Q-M) / `generation_timeout` 분리(N20) / 임계값 1차 = 코드 p95 + LLM 근거(11/02 9.2) / 위험도는 사람이(9.2) / 일괄 + 규칙별 수식(R5)
- 제안: 작업 종류 이름 `recommend_thresholds` / 추천 1건 필드·상태 5종·`source` 2종 / 필드 단위 격리·작업 성공 규칙 / 수식 추천 API 경로·구현 주체 API+SQL / `recommend_methods` 보류

| # | 질문 | 제안 |
|---|---|---|
| Q-L1 | (a) 일부 처리 뒤 서비스 장애: 작업 전체 failed(현행) vs partial 저장 (b) 전 항목이 생성계 사유로 rejected: 성공 vs `recommendation_all_rejected` failed | (a) 전체 failed(부분 결과 없음, 화면 설계 9.2와 일치) (b) **failed `recommendation_all_rejected`**(reviewer 권고 4). `no_applicable`만인 경우는 성공 유지 |
| Q-L2 | 수식 추천 구현 주체 API+SQL vs Trainer 동기 경로 신설 | API+SQL(동기, 큐 없음). p95 일치 시험 필수 |
| Q-L3 | `recommend_methods` 보류 vs 폐지 | 보류(모델 카탈로그 Issue 뒤 재검토) |
| Q-L4 | 1차 `operator` 항상 `gt` | 유지(코드 `reference_policy`). `lte/lt` 방향 필드가 생기면 카탈로그에 `recommendedOperator` 추가 |
| Q-L5 | 수식 추천 `active_model_training_start`가 366일을 넘을 때 — (i) 오류 400 `period_too_long`(이전 추천) (ii) **(a) 모드만 상한 면제, 집계는 서버가 일 단위 표본화**(날짜별 p95·표본 수를 선계산한 일별 집계 또는 `TABLESAMPLE`로 응답 시간 보호) (iii) 최근 366일로 자름 | **(ii) 추천**(3차 검토 반영). 장점: 모델 학습 시작이 1년 이전이면 (i)는 (a)가 **항상** 실패해 기능이 무의미해짐, (iii)은 "학습 시작~현재"라는 사용자 결정의 의미가 바뀜. 단점: 일 단위 표본화는 정확한 p95가 아니라 근사(응답 `evidence.quantileMethod: "percentile_cont_daily_sampled"`·`sampled: true`로 표시), 구현이 거래량 집계 API의 일별 집계와 겹쳐 DB 세션 부담. `custom`·`recent_*`는 상한 366일 유지 |
| Q-L6 | mode (a)에서 활성 모델의 학습 제외 구간(`excludedIntervals`)을 자동 적용할지 | 기본 미적용(요청 `excludedIntervals`만). 화면이 "학습 제외 구간 적용" 체크로 모델 버전 응답의 구간을 복사해 보내는 방식 제안 |
