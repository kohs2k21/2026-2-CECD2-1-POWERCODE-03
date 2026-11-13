Created: 2026-11-14T18:00:00+09:00
Updated: 2026-11-15T01:00:00+09:00
Author: Claude Code (LLM 세션 builder)
Status: draft

# 프로토콜 경로 설명 작업 `explain_protocol` v1 (초안)

- 무엇: 프로토콜 설정 상세(관측 경로 vs 허용 경로)와 프로토콜 알림 상세에서 관리자가 요청하는 **대화형 LLM 설명**. LLM은 코드가 만든 사실을 한국어 문장 `{explanation, reviewPoints}`로 풀어 쓰기만 한다. 경로 집계·허용 여부·판정은 코드 값이다([protocol-rules.md](protocol-rules.md) 1절 "LLM은 설명만")
- 근거: 시험 키트(`research/experiments/protocol-explain`, 기반 모델 Qwen3.6-27B NF4 어댑터 없음 + 프롬프트 v3 → 합성 7/8, 실데이터 7/8·7/8, 시험 사례 모두 60초 생성 상한 안, 지어낸 숫자·경로·표기 0건). 이 작업은 v3의 남은 실패(사유 이름표 뒤 "로 인해")와 약점(근거 부족 미언급)을 고친 **프롬프트 v4**로 시작한다(아래 프롬프트 절)
- 합의(WebUI 총괄): 새 작업 종류 `explain_protocol`(기존 `explain` 입력 확장 아님) / 출력은 `{explanation, reviewPoints[≤3]}`이고 판정·숫자 필드 없음 / 대화형 상한(`LLM_EXPLANATION_WAIT_SECONDS` 10초·`LLM_EXPLANATION_SECONDS` 60초)과 GPU 차례는 `explain`과 같은 대화형 등급 / API 접수·검증과 화면은 WebUI·API 담당
- 표기: **[확정]** 합의·코드 사실 / **[제안]** 이 초안이 정한 값 / **[승인 대상]** 공통 계약(migration·API 요청·큐 payload·결과 형식)이라 사용자 승인 뒤 확정 / **[확정 11/14 사용자]** 12절 결정 / **[후보]** 화면 문구(사용자 승인 대상)
- 구현 위치(이 계약과 같은 PR): 워커 `server/worker/protocol_explanation_job.py`(작업), `protocol_explanation.py`(사실·이름표·프롬프트·점검 순수 함수, 키트와 공용), `generation_service.py`·`generation_client.py`(새 kind), `trainer/runner.py`(소비자 경로), migration `027_explain_protocol.sql`·역할 스크립트 1줄([확정 11/14 사용자], 9절). API(`server/backend`)·화면(`server/frontend`)은 이 PR에서 바꾸지 않는다(아래 API 쪽 변경점 표)

## 1. 요청 — `POST /api/jobs kind=explain_protocol` [승인 대상]

대상 두 종류. 화면 위치와 1:1이다.

| `target.kind` | 화면 | 본문 |
|---|---|---|
| `observed_paths` | 룰 설정 › 프로토콜 설정 상세 — 관측 경로 초안과 허용 경로 비교([protocol-settings-ui.md](protocol-settings-ui.md) 2·3절) | `{"kind":"explain_protocol","sourceId":"<uuid>","target":{"kind":"observed_paths","jobId":"<recommend_relations 작업 uuid>","interfaceId":"<원래 문자열>","ruleSetRevision":15}}` |
| `alert` | 알림 상세 — 정상 프로토콜 확인(처음 보는 경로·등록되지 않은 인터페이스, [protocol-settings-ui.md](protocol-settings-ui.md) 5절) | `{"kind":"explain_protocol","sourceId":"<uuid>","target":{"kind":"alert","decisionId":"<판정 uuid>"}}` |

- 예시 파일: [protocol-explanation-job.example.json](protocol-explanation-job.example.json)(API 접수 시험과 워커 `parse_input` 시험이 같은 파일을 읽음)
- 키는 위에 적은 것만. 그 밖의 키·형식 위반은 400 `invalid_protocol_explanation_request` [제안]

| 키 | 규칙 |
|---|---|
| `sourceId` | 기존 `/api/jobs`와 같이 필수 UUID. 후보 작업 결과의 `sourceId`, 판정 관측의 `source_id`와 같아야 한다(다르면 대상 없음) |
| `target.jobId` | 화면이 보여 주는 관측 경로 초안의 원천 작업(`GET /api/rule-set/protocol-drafts` 응답 `job`). `recommend_relations`·`succeeded`. 최신 작업일 필요는 없다(요청이 고정) |
| `target.interfaceId` | 1~512자, 원래 문자열 그대로(상세 화면 주소의 인터페이스 ID를 되돌린 값). (a) 그 작업 결과의 초안 `protocolPathDrafts.interfaces[]`에 있거나 (b) `ruleSetRevision` 버전의 `protocolRules[]`에 등록돼 있어야 한다 — 학습 기간에 관측되지 않았어도 등록돼 있으면 받는다. 둘 다 아니면 400 `protocol_explanation_facts_unavailable`(10절 접수 검증, 워커 판정과 같은 조건) |
| `target.ruleSetRevision` | 비교할 룰 세트 버전(0 이상 정수, 화면의 현재 운영 버전). 현재 버전일 필요는 없고 리비전 행이 있어야 한다. 화면 표시는 "버전 N"([rule-set.md](rule-set.md) 용어) |
| `target.decisionId` | 알림이 가리키는 판정(알림 묶음의 첫 판정 등 화면이 고른 판정). 그 판정의 `evidence.protocolReview`와 그 안의 `ruleSetRevision`을 쓴다 |
| 권한 | **관리자만**(403 `admin_required`) [확정 11/14 사용자]. 프로토콜 설정·초안 API와 프로토콜 알림이 모두 관리자 전용이기 때문. migration CHECK도 `requester_role='admin'` |
| 멱등키·재시도·상태 | 기존 `/api/jobs` 그대로(`Idempotency-Key`, queued/running/succeeded/failed, `attemptCount`·`maxAttempts` 3·`nextRunAt`). 같은 멱등키에 다른 입력 409 `idempotency_payload_conflict` |
| `input_spec` | 요청 본문 그대로(키 `kind`·`sourceId`·`target`). 워커는 이것만 읽는다. `input_config_version_id`·`input_snapshot_id`·`input_manifest_id` NULL. `input_decision_id` = 알림 대상의 `decisionId`, 관측 경로 대상은 NULL |
| `llm_spec` | `evidence_version: "protocol-explain-facts-v1"`, `input_evidence_hash: <request_hash>`(= `input_spec` sha256, recommend_thresholds와 같은 방식 — 사실은 워커가 만들어 접수 시점에는 없음), `model_version: "qwen-base-6a9e13bd"`(explain과 같은 기반 모델 이름), `prompt_version: "protocol-explain-prompt-v4"`, `output_schema_version: "protocol-explanation-v1"`, `reuse_key: sha256([actor, input_spec, null, prompt_version])`(explain과 같은 꼴). 워커는 `evidence_version`·`prompt_version`·`output_schema_version`이 위 값과 다르면 `protocol_explanation_llm_spec_invalid`로 멈춘다 |
| 재사용 | 같은 요청자·같은 대상(`input_spec`)·같은 프롬프트의 queued/running/succeeded 작업이 있으면 그 작업을 돌려준다(explain 접수 흐름 그대로). 대상이 작업 id·리비전·판정 id로 고정되어 사실이 같고 생성이 greedy라 같은 결과다. 실패 작업은 재사용하지 않는다(다시 요청 가능) |

## 2. 사실 만들기 — 워커가 DB에서 [제안, 원천·선택 규칙은 키트 export와 같음]

- 사실 형식 `protocol-explain-facts-v1`(키트와 같음, `protocol_explanation.observed_paths_facts`·`alert_facts`). 워커는 경로를 다시 집계하지 않는다
- 읽기: 시도마다 **REPEATABLE READ, READ ONLY 트랜잭션 하나**에서 필요한 행만 읽고 생성 전에 끝낸다(60초 생성 동안 트랜잭션을 열어 두지 않음)

| 대상 | 읽는 것 | 사실 |
|---|---|---|
| `observed_paths` | `run_jobs`(id = `jobId`)의 `kind`·`status`·`result_summary.protocolPathDrafts`·`period`·`sourceId` / `ops_rule_set_revisions`(revision = `ruleSetRevision`)의 `snapshot.protocolRules[]` | `protocolPathDrafts.interfaces[]` 중 `interfaceId`가 같은 항목(없으면 학습 기간 관측 없음) + 같은 인터페이스의 룰 세트 항목(없으면 미등록). `parameters`·`period`는 결과 값 그대로 |
| `alert` | `run_process_decisions` ⋈ `data_observations`(id = `decisionId`, `source_id` = `sourceId`)의 `evidence.protocolReview` / 그 `ruleSetRevision`의 `protocolRules[]` | `protocolReview` v1(종류 2가지·`interfaceId` 비지 않은 문자열 — 알림 목록과 같은 판별) + 그 리비전의 같은 인터페이스 항목 |

- `protocolPathDrafts`는 `version` 1만 받는다. `protocolRules[]`가 없거나 v2 스냅샷이면 빈 목록(모든 인터페이스 미등록)으로 읽는다(키트와 같음)
- 관측 경로 대상에서 인터페이스가 **초안에도 룰 세트에도 없으면** 설명할 사실이 없으므로 `protocol_explanation_facts_unavailable`
- 알림 대상에서 판정 근거의 룰 세트 버전 행이 없으면: 처음 보는 경로 알림은 허용 경로를 알 수 없어 `protocol_explanation_facts_unavailable`(결과 없음, 화면은 7절 사실 부족 문구) / 등록되지 않은 인터페이스 알림은 허용 경로가 필요 없어 그대로 설명하고 결과 `ruleSetRevision`은 근거에 적힌 값을 그대로 둔다(행 유무와 무관)
- 사실 크기 상한은 키트 그대로: 관측 경로 20개(허용 경로에 없는 경로 먼저)·허용 경로 20개, 나머지는 건수 요약
- 사실 요약 해시 `facts.sha256` = 모델에 넣은 사실 JSON(별칭 상태, 키 순서 유지·공백 없음 — 사용자 메시지와 같은 직렬화)의 UTF-8 SHA-256

## 3. 별칭 — LLM에는 별칭만 [확정 키트 규칙 / 운영 연결 제안]

- 사실의 실제 ID는 별칭으로 바꾼다: 인터페이스 `IF-01`, 채널 `CH-01`…(경로에 처음 나온 순서), 경로 id `P1`…(같은 채널 배열 = 같은 id). 별칭 표는 워커 메모리에만 있고 사실·모델 입력·로그에 넣지 않는다
- **점검은 별칭 상태로 한다**(모델 원문 그대로). 통과한 뒤에만 코드가 `explanation`·`reviewPoints`의 `IF-xx`·`CH-xx`를 실제 ID로 한 번에 바꾼다(한 번에 바꿔 실제 ID가 별칭 모양이어도 다시 바뀌지 않음, 표기 경계 밖 일치는 바꾸지 않음)
- 경로 id(`P1`…)는 문장에 그대로 두고 결과 `paths`에 실제 채널 배열을 함께 준다(화면은 칩·화살표로 그림). 별칭 되돌리기는 저장 전에 하므로 저장·표시 값은 실제 ID다

## 4. 생성 — 운영 `generation_service` [제안]

- 작업 종류 이름이 생성 kind다: `generate("explain_protocol", {"facts": <사실>}, report_timeout=True)`. HTTP(`generation_client` → `/generate`)와 같은 프로세스(`set_local_model`) 두 경로가 같다
- 서비스가 프롬프트를 만든다: `protocol_explanation.build_messages(facts)`(system + 이름표·사실 JSON). 입력은 `{"facts"}` 하나이고 `facts.format`이 `protocol-explain-facts-v1`이 아니면 GPU 차례를 기다리기 전에 거부한다 — 서비스 안 `ValueError("invalid_generation_request")`, HTTP 422 본문 `generation_contract_rejected`, 작업 코드 `generation_contract_rejected`(같은 프로세스 경로도 같은 코드로 맞춤)
- explain과 같은 대화형 설정: 어댑터 끔(`disable_adapter`), `enable_thinking=False`, greedy, seed 42, 출력 토큰 512, 입력 토큰 상한은 번들 manifest `maximum_input_tokens` 그대로(초과는 서비스 안 `ValueError("generation_token_budget_exceeded")` → HTTP 422 본문·작업 코드 `generation_contract_rejected`, 같은 프로세스 경로도 같은 작업 코드 — 7절 표와 같음). 응답 `adapterSha` = 기반 모델 revision, `promptVersion` = 서비스가 쓴 프롬프트 버전(워커가 대조, 다르면 `generation_prompt_version_mismatch`)
- 출력은 explain과 같이 **전체를 `json.loads`로 엄격하게** 읽는다. 코드 블록·앞뒤 글이 있으면 HTTP 422 `invalid_generation_output` / 같은 프로세스는 `JSONDecodeError` → 작업 `protocol_explanation_json_invalid`. 그래서 키트 점검의 `json_not_strict`는 운영에서 나오지 않는다

### GPU 차례 [확정 합의]

- `explain_protocol`은 `explain`과 같은 대화형 등급(`GenerationGate` interactive): GPU가 비면 추천 작업(`rule`·`feature`)보다 먼저, 대화형끼리는 도착 순. 실행 중인 생성은 끊지 않음
- 대기 상한 `LLM_EXPLANATION_WAIT_SECONDS`(기본 10초) → `generation_busy`, 생성 상한 `LLM_EXPLANATION_SECONDS`(기본 60초) → `generation_timeout`(이 작업은 `report_timeout`을 켬). 두 상한은 explain과 같은 환경 변수를 공유한다(따로 두지 않음)

## 5. 프롬프트 v4와 점검 [제안 — 이 PR 구현]

- `protocol-explain-prompt-v4` = v3 + "초안 상태가 근거 부족이면 그 사실을 explanation에 '근거 부족'이라는 이름으로 적는다"(v3 약점: 근거 부족인데 언급하지 않음). 지시 문장에는 출력 금지 표현(지시·추정·원인 낱말)을 쓰지 않고, 금지 표현은 따옴표 안의 "쓰지 않을 표현" 예시에만 둔다(1번 지시도 같은 기준으로 다듬음 — 시험이 확인)
- 점검(`protocol_explanation.check_output`, 키트와 같은 함수, PASS = 전부): `json_parsed`·`schema_ok`·`length_ok`(설명 1~600자, 확인할 점 0~3개·각 1~200자)·`numbers_subset`·`identifiers_subset`·`paths_in_facts`·`korean_ok`·`no_forbidden`. 상세 기준은 키트 README 3절
- v4 점검 변경: **사실에 실린 사유 이름표**(경로를 만들지 못한 거래 사유·알림 경로 사유의 화면 이름, 예 "선행 사슬 끊김") **바로 뒤의 "(으)로 인해·인하여·인한"은 허용**한다(사실의 코드 사유를 다시 쓴 것 — v3 남은 실패). 이름표는 정의된 사유 코드(protocol-rules.md 3.4)의 화면 이름만 본다(모르는 코드는 이름표가 코드 문자열 그대로라 허용 대상 아님). 이름표와 사이에 공백이 있거나, 사실에 없는 사유 이름이거나, 그 밖의 "로 인해"는 계속 금지. "때문"·"원인" 등 다른 원인 표현은 이름표 뒤여도 금지
- 정보용 `coverage`(근거 부족·허용 경로에 없는 경로·미등록 등을 언급했는지)는 통과 기준이 아니며 결과 `check.coverage`에 참/거짓만 남긴다

## 6. 결과 — `protocol-explanation-v1` [승인 대상]

`GET /api/jobs/:id` `resultSummary` = `run_llm_results.output`(같은 값).

```json
{"format": "protocol-explanation-v1",
 "target": {"kind": "observed_paths", "jobId": "…", "interfaceId": "IF_A", "ruleSetRevision": 15},
 "interfaceId": "IF_A", "ruleSetRevision": 15,
 "explanation": "인터페이스 IF_A의 학습 기간 관측 경로 P2(CH_A → CH_C)는 허용 경로에 없습니다. …",
 "reviewPoints": ["경로 P2가 정상 거래 흐름인지 확인합니다."],
 "paths": {"P1": ["CH_A", "CH_B"], "P2": ["CH_A", "CH_C"]},
 "facts": {"format": "protocol-explain-facts-v1", "kind": "observed_paths", "sha256": "<64자>"},
 "promptVersion": "protocol-explain-prompt-v4", "modelVersion": "qwen-base-6a9e13bd", "baseRevision": "<기반 모델 revision>",
 "check": {"passed": true, "failures": [], "coverage": {"mentions_not_allowed_path": true}}}
```

| 필드 | 뜻 |
|---|---|
| `target` | 요청 대상 그대로 |
| `interfaceId` | 실제 인터페이스 ID(알림 대상은 판정 근거의 값) |
| `ruleSetRevision` | 사실에 쓴 룰 세트 버전(관측 경로 = 요청 값, 알림 = 판정 근거의 값, 미등록 알림이면 null일 수 있음). 화면 표시는 "버전 N" |
| `explanation`·`reviewPoints` | 점검을 통과한 문장, 실제 ID로 되돌린 값. **판정·숫자 필드는 없다**(문장 속 숫자는 사실 값 그대로임을 점검이 보장) |
| `paths` | 사실의 경로 id → 실제 채널 배열(문장의 `P1`… 표시용) |
| `facts` | 사실 형식·종류·요약 해시(사실 본문은 저장하지 않음) |
| `promptVersion`·`modelVersion`·`baseRevision` | 프롬프트 버전, `llm_spec.model_version`, 생성 서비스가 돌려준 기반 모델 revision |
| `check` | `passed`, `failures`(점검 이름 코드), `coverage`(정보용 참/거짓) |

- **점검 실패**: 작업 `failed` + `error_code` `protocol_explanation_check_failed`, **재시도 없음**(같은 사실·greedy라 같은 출력). `resultSummary`에는 위 형식에서 `explanation`·`reviewPoints`·`paths`를 **뺀** 요약만 남긴다(`check.passed: false`, `check.failures` = 사유 코드). 문장은 어디에도 저장하지 않고 화면에 보이지 않는다. `run_llm_results` 행은 없다
- 엄격 JSON 실패·생성 실패는 `resultSummary` 없음(`error_code`만)

## 7. 실패 코드 [제안]

| 코드 | 상황 | 재시도 | 화면 문구 [후보] |
|---|---|---|---|
| `protocol_explanation_target_not_found` | 관측 경로: 작업 없음·`recommend_relations` 아님·성공 아님·출처 다름, 룰 세트 리비전 없음 / 알림: 판정 없음·출처 다름 | 없음 | "설명할 대상을 찾지 못했습니다." |
| `protocol_explanation_facts_unavailable` | 결과에 경로 집계 없음(집계 전 Trainer 결과)·버전 다름·형식 다름, 인터페이스가 초안에도 룰 세트에도 없음, 판정에 프로토콜 근거 없음, 처음 보는 경로 알림인데 그 버전에 항목 없음 | 없음 | "설명에 쓸 사실이 부족합니다." |
| `generation_busy` | 대기 상한 안에 GPU 차례를 얻지 못함 | 있음(explain과 같음: 5초 뒤, 최대 3회 뒤 `failed` + 이 코드) | "LLM이 다른 생성을 처리 중입니다. 잠시 뒤 다시 요청해 주세요." |
| `generation_timeout` | 생성 시간 상한 초과 | 없음 | "설명 생성 시간이 초과되었습니다." |
| `generation_service_unavailable` | 생성 서비스 연결 불가·장애 | 없음 [확정 11/14 사용자 — explain은 `job_processing_failed`로 재시도하지만 이 작업은 바로 실패] | "LLM을 사용할 수 없습니다." |
| `generation_service_not_configured` | 생성 서비스 설정 없음(HTTP 주소·토큰 없음 등, 코드 그대로 저장) | 없음(위와 같음) | "LLM을 사용할 수 없습니다." |
| `protocol_explanation_check_failed` | 자동 점검 실패(`check.failures`에 사유) | 없음 | "자동 점검을 통과하지 못해 설명을 보이지 않습니다." + 사유 이름(아래) |
| `protocol_explanation_json_invalid` | 출력이 JSON 객체 하나가 아님(코드 블록·앞뒤 글·객체 아님)·출력 한도에서 끊김 | 없음 | "설명 형식이 올바르지 않아 보이지 않습니다." |
| `generation_contract_rejected` | 입력 토큰 예산 초과 등 생성 서비스 요청 거부 | 없음 | "설명을 만들 수 없는 요청입니다." |
| `generation_prompt_version_mismatch` | 생성 서비스 배포본의 프롬프트 버전이 워커와 다름 | 없음 | (운영 오류 — 코드 그대로) |
| `protocol_explanation_input_invalid`·`protocol_explanation_llm_spec_invalid`·`job_role_not_authorized` | 워커 방어(정상 접수에서는 나오지 않음) | 없음 | 코드 그대로 |
| `job_processing_failed` | 그 밖의 예외(기존 흐름) | 있음 | 기존 문구 |

- 접수와 워커의 구분: 접수 단계(10절)에서는 `protocol_explanation_target_not_found`(대상 없음)와 `protocol_explanation_facts_unavailable`(초안 v1 없음, 인터페이스가 초안에도 그 버전 룰 세트에도 없음, 판정에 프로토콜 근거 없음)이 **400**으로 오고 작업은 만들어지지 않는다. 위 표의 같은 두 코드(작업 `failed`)는 접수 뒤 워커 방어 검사에서 나는 경우다. 그중 "처음 보는 경로 알림인데 판정 근거의 룰 세트 버전에 그 인터페이스 항목이 없음(행이 없는 경우 포함)"과 "원천 형식이 사실 함수 기준과 다름(`protocol_explain_*` 검증 실패)"은 접수가 보지 않으므로 **워커에서만** 나온다
- 점검 사유 이름 [후보]: `numbers_subset` 사실에 없는 숫자 · `identifiers_subset` 사실에 없는 표기 · `paths_in_facts` 사실에 없는 경로 · `no_forbidden` 원인 단정·추정·지시 표현 · `korean_ok` 한국어 문장 아님 · `length_ok` 길이 · `schema_ok` 형식 · `json_parsed` 형식

## 8. 큐 [제안 — 기존 `llm_explanation` 재사용 추천]

- 큐 `llm_explanation`(`powercode_queue.llm_explanation_jobs`), payload `{schemaVersion: 1, jobId, enqueuedAt}` 불변. 소비자는 지금 그대로: 설명 전용 소비자(`llm_worker.consume_explanations`, 역할 `llm_explanation`)와 주 소비자(역할 `llm`, `llm_recommendation`↔`llm_explanation` 교대)가 모두 받고 `trainer/runner.py` `_execute`가 kind로 나눈다. 옛 `training` 큐 배수 경로(`explain`만)에는 넣지 않는다
- 근거: GPU 순서를 정하는 것은 큐가 아니라 생성 서비스 `GenerationGate`이고 두 작업이 같은 대화형 등급이다. 대화형 작업은 생성 상한 60초 안에서 끝나 같은 큐에서 도착 순으로 처리해도 explain을 오래 막지 않는다
- 대안(채택 안 함): 새 큐 `llm_protocol_explanation` — `queue_kind` enum 값·새 물리 표·역할 GRANT·소비자 claim 경로가 늘지만 GPU 차례가 같아 처리 순서는 달라지지 않는다. 프로토콜 설명이 많아져 explain 대기를 눈에 띄게 늘리면 그때 다시 본다

## 9. DB 영향 — migration 027 [확정 11/14 사용자]

`server/infra/migrations/`를 정적으로 대조한 결과 **migration이 필요하다**(새 enum 값 + kind 목록을 가진 CHECK·함수 3곳). `027_explain_protocol.sql`과 역할 GRANT 1줄을 이 PR에 넣었고 사용자가 승인했다(11/14). 실제 PostgreSQL 시험은 아래 미검증 줄

| 대상(최신 정의) | 변경 |
|---|---|
| `job_kind` enum | `explain_protocol` 추가(트랜잭션 밖 `ADD VALUE IF NOT EXISTS`, 파일 안에서는 `kind::text`만 — 017·021 방식) |
| `run_jobs_v03_contract`(021본) | legacy 금지 목록에 추가 + 새 분기: `queue='llm_explanation'`·`producer_kind='user'`·`requester_role='admin'`·`llm_spec` 필수·`input_manifest_id`·`input_config_version_id` NULL(판정은 알림 대상만이라 `input_decision_id` NULL 허용) |
| `run_llm_results_kind_check`·`run_llm_results_check`(017본) | kind 목록 추가, `explain_protocol` 분기(decision 조건 없음 — 결과 판정 = 작업 판정은 `erd_v03_validate_result`가 이미 강제) |
| `erd_v03_validate_job`(017본) | llm_spec 필수 목록·LLM 완료 결과 필수 목록에 추가(2줄). explain 전용 판정 구성 대조는 적용하지 않음(프로토콜 판정은 v04 구성 NULL) |
| `erd_v03_validate_result` | 변경 없음(종류 목록 없음) |
| `scripts/database-roles.sql` | `GRANT SELECT ON ops_rule_set_revisions TO powercode_llm` 1줄(사실에 룰 세트 리비전을 읽음, 표 단위 SELECT) [확정 11/14 사용자]. 다른 읽기·쓰기 권한은 이미 있음 |
| `schema.dbml`·`database/README.md`·`tests/prepare-runtime-db.py` | enum 값·주석, 027 절, 적용 목록 |

- 적용 순서: 작업자를 멈추고 027 → 역할 스크립트 재적용 → LLM 워커 → API(접수). 기존 코드는 새 값을 쓰지 않아 027 뒤에도 그대로 동작
- 미검증: 실제 PostgreSQL 적용·재실행·역할 경계 시험(`erd-v03-*.mjs` 027 전용)은 아직 없다. CI의 `prepare-runtime-db.py`(한 문자열 실행)가 적용만 확인한다

## 10. API 쪽 변경점 (WebUI·API 담당, 이 PR에서 고치지 않음) [제안, 접수 검증 행은 확정 11/14 사용자]

| 위치 | 변경 |
|---|---|
| `server/backend/src/services/generationAvailability.ts` `LLM_JOB_KINDS` | `'explain_protocol'` 한 줄 추가 — 생성 서비스가 꺼져 있으면 접수 503 `generation_service_unavailable`(LLM 가용성 브랜치 기준) |
| `routes/jobRoutes.ts` `KINDS`·관리자 검사 | `explain_protocol` 추가, 본문 해석 전 관리자 검사(403 `admin_required`) |
| 접수 검증 — 형식 | 1절 키 규칙. 위반은 400 `invalid_protocol_explanation_request` [제안] |
| 접수 검증 — 대상·사실 [확정 11/14 사용자] | 접수 때 대상과 사실이 있는지 확인하고, 없으면 **400**으로 거절한다(작업·큐 메시지를 만들지 않음). 응답은 `{code}`만<br>관측 경로 대상: (1) `target.jobId` 작업이 있고 `kind = recommend_relations`·`status = succeeded`·결과 `sourceId` = 요청 `sourceId` (2) `target.ruleSetRevision` 리비전 행이 있음 — 둘 중 하나라도 아니면 400 `protocol_explanation_target_not_found` (3) 그 결과의 `protocolPathDrafts`가 `version` 1(`interfaces[]` 목록 있음) (4) `target.interfaceId`가 (a) 초안 `interfaces[]`에 있거나 (b) 그 리비전 `protocolRules[]`에 등록됨(`interfaceId` 원래 문자열 정확 일치) — (3)·(4) 중 하나라도 아니면 400 `protocol_explanation_facts_unavailable`. (1)~(4)는 워커 판정 `protocol_explanation_job.build_facts`(관측 경로 분기)·`rules_by_interface`와 같은 조건이다. API는 이 규칙을 그대로 구현한다<br>알림 대상: (1) 판정이 있고 그 관측이 요청 `sourceId` 출처 — 아니면 400 `protocol_explanation_target_not_found` (2) `evidence.protocolReview`가 있고 알림 목록과 같은 판별(`version` 1, `kind` ∈ {`path_unmatched`, `interface_unregistered`}, `interfaceId` 비지 않은 문자열)을 통과 — 아니면 400 `protocol_explanation_facts_unavailable`. 알림 대상은 판정 근거의 룰 세트 버전 행·항목을 접수에서 보지 않는다. "처음 보는 경로 알림인데 그 버전에 항목이 없음"은 워커 방어에서만 나오는 경우다(작업 `failed` + `protocol_explanation_facts_unavailable`, 2절)<br>워커의 같은 검사(2절·7절, 작업 `failed` + 같은 코드)는 방어용으로 그대로 둔다 |
| `llm_spec`·재사용 | 1절 고정 값, `reuse_key`·멱등 처리는 explain 흐름 재사용 |
| `input_decision_id` | 알림 대상은 `target.decisionId`와 **같은 값**, 관측 경로 대상은 NULL. 이 대조는 **API 접수 책임**이다. 워커는 결과 행 판정 id를 `target.decisionId`로 쓰고 DB(`erd_v03_validate_result`)가 결과 판정 = 작업 `input_decision_id`를 강제하므로, 둘이 다르면 완료 단계에서 거부되어 작업이 `job_processing_failed`로 끝난다 |
| 큐 선택 | `llm_explanation`(INSERT 대상 `powercode_queue.llm_explanation_jobs`) |
| `services/jobReadModel.ts` `LISTABLE_JOB_KINDS` | `explain_protocol: 'llm_explanation'`(작업 목록 kind 필터·큐 조건) |
| 작업 상세 | `resultSummary` 그대로(성공 = 6절 결과, 점검 실패 = 문장 없는 요약). 실패 코드 표시는 7절 |

## 11. 화면 문구 [후보 — 사용자 승인 대상]

- 버튼: 프로토콜 설정 상세 [경로 설명 보기], 알림 상세 [설명 보기]. 진행 중 "설명을 만드는 중입니다…"
- 결과 칸 제목 "LLM 설명"(본문 `explanation`), "확인할 점"(`reviewPoints` 목록, 없으면 칸 숨김). 경로 id(`P1`)는 `paths`로 채널 칩·화살표를 함께 보임. 아래 작은 글씨 "코드가 집계한 사실만으로 쓴 설명입니다 · 버전 N 기준"
- 실패 코드 표시: 7절 후보 문구. 점검 실패는 사유 이름만 보이고 생성 문장은 보이지 않음

## 12. 결정 결과 (11/14 사용자, WebUI 경유 — 추천안대로)

| # | 질문 | 결정 |
|---|---|---|
| Q-P1 | 권한: 관리자만 vs 인증 사용자 | **관리자만** [확정 11/14 사용자] (원천 화면·API가 관리자 전용) |
| Q-P2 | `generation_service_unavailable` 재시도: 없음(즉시 실패) vs explain처럼 재시도 | **재시도 없이 실패** [확정 11/14 사용자] — 접수 가용성 차단(503)이 먼저 막고, 대화형이라 빨리 실패해 [다시 요청]이 낫다(현재 구현 그대로) |
| Q-P3 | 큐: `llm_explanation` 재사용 vs 새 큐 | **재사용**(8절). 따로 묻지 않았고, 승인된 migration 027의 CHECK가 이 작업을 `llm_explanation` 큐로 고정하므로 그 승인으로 함께 정해짐 |
| Q-P4 | migration 027·역할 GRANT 1줄 | **승인** [확정 11/14 사용자]. GRANT는 표 단위 SELECT. 대안 열 단위 `GRANT SELECT(revision, snapshot) ON ops_rule_set_revisions TO powercode_llm`(필요한 열만, 단 015 권한 시험이 열 권한을 보도록 바꿔야 하고 열이 늘면 다시 GRANT)은 **채택 안 함**. 실제 PostgreSQL 시험은 9절 미검증 줄대로 남음 |
| Q-P5 | 화면 문구(11절)·실패 코드 문구(7절) | 미정 — 이번 결정 범위 밖. WebUI가 사용자 승인을 받아 확정(그때까지 [후보]) |
| Q-P6 | API 접수에서 대상·사실 존재를 확인할지 | **확인하고 없으면 400** [확정 11/14 사용자] — 10절 접수 검증 행(코드 `protocol_explanation_target_not_found`·`protocol_explanation_facts_unavailable`), 워커 검사는 방어용으로 유지 |
