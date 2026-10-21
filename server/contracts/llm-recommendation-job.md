# LLM 방법 추천 작업 계약 v1 (초안, Issue #74)

- 계약 버전: `method-recommendation-v1`. 근거 코드 origin/dev `872f540`
- 상위 결정: 플러그형 설계 방향 1절(SR은 LLM 추천 입력 가능), WebUI 화면 흐름 3절(버튼 → 로딩 → JSON → 등록 목록과 AND 자동 선택 → 사람이 적용 여부만 선택)·2.3절(탐지 버전 화면 내부 탭), Q-M=★A(집계값만·파라미터 제안 허용), Q-N → Q2 [확정 10/21, 설계 방향 4.5절](`recommend_rule` 폐지 경로, `recommend_methods`만 유지)
- 표기: **[확정]** / **★** 제안(결정 대기) / **충돌**
- 범위: 구성 초안·카탈로그·데이터 집계를 입력으로 **카탈로그 등록 방법 중** 추천하는 비동기 작업. 상태·입력 범위·결과 JSON·서버 대조·`llm_worker` 영향
- 제외: 새 방법 생성(`recommend_rule` Rule DSL 초안 생성은 **[확정] 폐지 대상** — 기존 결과·adopt Rule은 읽기 보존 ★, 관계 절 참조), 프롬프트·어댑터 연구, GPU 배포, 자동 적용

## 기존 추천과의 관계

- 현행 `recommend_rule`(`jobRoutes.ts:15`, `trainer/runner.py:325-338`, `research_design.py:202-234`)은 변수별 p95 기준선으로 **새 Rule 초안**(`sr_<hash>`, `SR_DIAGNOSTIC`, severity warning)을 만들고 관리자가 `adopt`로 등록. 등록 목록 중 고르는 작업이 아님 [사실]
- **[확정 — 사용자 10/21, Detector Q2·설계 방향 4.5절] LLM 신규 Rule·파생변수 생성 기능은 제거 예정**: `recommend_rule`(및 `recommend_feature`)은 **폐지 대상**, LLM 추천은 `recommend_methods`(등록 목록 안 선택)만 유지. 기존 `recommend_rule` 결과·`run_llm_results` 행·`adopt`로 등록된 Rule은 **읽기 보존** ★(삭제·재해석 없음); 신규 `recommend_rule` 요청 차단 시점·방식(422 코드, `job_kind` enum 값 유지)은 LLM 담당 세션 결정
- 등록 Rule(`rule:<rule_key>`)은 HR 세부 방법으로 그대로 유지(변경 없음). 새 `recommend_methods`는 별도 `job_kind`
- WebUI AND 규칙 [확정]: 추천 결과 ∩ 시스템 등록 목록(카탈로그 + 등록 Rule) → 교집합만 자동 선택, 목록 밖 추천은 선택 불가로 표시. 이 계약은 서버가 항목마다 `registered`와 `selectable`(등록 ∧ 실행 가능 ∧ ML이면 학습 후보 있음 ∧ action=enable)을 미리 계산해 제공하고, WebUI는 `selectable=true`만 자동 체크·나머지는 표시만 함

## 작업 — `POST /api/jobs kind=recommend_methods` [신규, 관리자]

```json
{
  "kind": "recommend_methods",
  "sourceId": "<source UUID>",
  "snapshotId": "<ready snapshot UUID>",
  "featureSetId": "<feature set UUID|null>",
  "draft": {
    "catalogVersion": "method-catalog-v1",
    "methods": [{"methodId": "hr:process-status", "enabled": true},
                {"methodId": "ml:iforest", "enabled": true}]
  },
  "scope": ["sr", "ml"]
}
```

- 권한·멱등키·재시도·상태(queued/running/succeeded/failed, `attemptCount`·`maxAttempts`·`nextRunAt`) 기존 `/api/jobs` 그대로. 큐 `llm_recommendation`(`queues.md` 7행, `jobRoutes.ts:257, 272`)
- `snapshotId`: 데이터 집계 출처(ready). 생략 시 `from/until`로 스냅샷 생성하는 현행 추천 경로 재사용 ★
- `draft.methods[]`: 현재 화면 선택(식별자·enabled만, 파라미터는 선택). 카탈로그에 없는 식별자는 422 `method_unknown:<id>`([detection-config](detection-config-version.md) 조합 저장 검증과 같은 코드·상태 — 본문 형식은 맞고 카탈로그 검증 실패이므로 현행 `unsupported_model` 422와 같은 분류)
- `scope`: 추천 대상 kind 부분집합(기본 `["hr","rule","sr","ml"]`). **같은 멱등키 + 같은 본문**(draft·scope·스냅샷·카탈로그 버전)이면 `reuse_key`로 기존 진행/완료 작업을 200 반환(`jobRoutes.ts:229-244` 규약); 같은 멱등키·다른 본문은 409
- `llm_spec` ★: `evidence_version: 'config-draft-catalog-v1'`, `input_evidence_hash: sha256(입력 집계 JSON)`, `model_version: '<어댑터 식별자>'`(연구 산출물 결정, 예 `methods_v1`), `prompt_version: 'method-selection-v0.1'`, `output_schema_version: 'method-recommendation-v1'`, `reuse_key: sha256([actor, input, idempotencyKey, prompt_version])`
- `reuse_key`에 `idempotencyKey`가 들어가므로 서버의 재사용은 **같은 멱등키일 때만** 성립. 다른 멱등키로 같은 초안을 다시 보내면 새 작업이 생김 → "실행 중 중복 요청 방지"(WebUI 3절)는 화면이 실행 중 버튼을 비활성 처리하는 것을 전제(WebUI 검토 의견 7)

## 입력 범위 — GPU에 넘기는 것 ★ Q-M=A

- **집계값만**. 원문 BODY·토큰·관측 행 원문·고객 식별자 금지(`pipeline.md` 수집 절 원칙 유지). 요청 크기 상한 256,000 바이트(`generation_client.py:44`) 안에서 구성
- 포함
  - `draft`: 위 그대로
  - `catalog`: scope 안 항목의 `{methodId, kind, category, displayName, status, executable, inputRequirements, parameterSchema, defaults}`(카탈로그 JSON 부분집합, `uiTarget`·`sourceRef` 제외)
  - `registeredRules`: `rule:*` 인스턴스 목록 `{methodId, ruleVersionId, version, displayName, condition 요약, outcome}`(retired 제외; HR 등록 목록 경계는 Detector D9-3 후속)
  - `dataSummary`: 스냅샷 train 분할 집계 — 행 수·상태 분포(S/F/P/N 건수)·`featureSetId`의 변수별 `summary`(count/valid_count/p50/p95/p99 등, 현행 `research_design.rule_request`의 `measurement`·`baseline` 형식 재사용 `research_design.py:156-199`)·어댑터 유형별 건수. 라벨은 anomaly 건수만
  - `constraints`: `{maxMethodsPerKind: 8, executableOnly: false}` ★ — 실행 불가 방법도 추천 가능(화면 표시 대상이므로), 결과에 `executable` 병기
- 제외: 판정 결과·evidence·설명 결과, 다른 source 데이터

## 결과 JSON `method-recommendation-v1`

```json
{
  "recommendationContractVersion": "method-recommendation-v1",
  "catalogVersion": "method-catalog-v1",
  "inputEvidenceHash": "<sha256>",
  "provider": "<어댑터 식별자>",
  "truncated": false,
  "recommendations": [
    {"methodId": "sr:ewma", "kind": "sr", "registered": true,
     "executable": {"process": false, "window": false}, "executableReason": {"process": "input_unit_mismatch"},
     "selectable": false, "selectableReason": "not_executable",
     "action": "enable",
     "parameters": {"window": 120, "aggregation": 1, "baseline": "B1"},
     "parameterStatus": "proposed", "parameterValidation": "valid",
     "rationale": {"factIds": ["m_…", "b_…"], "text": "처리시간 분산이 어댑터별로 커서 지수가중 추세 감시가 적합"},
     "confidence": null},
    {"methodId": "ml:iforest", "kind": "ml", "registered": true, "executable": {"process": true, "window": true},
     "trainedCandidateCount": 3, "selectable": false, "selectableReason": "action_not_enable",
     "action": "keep", "parameters": null, "parameterStatus": "not_proposed", "parameterValidation": "not_provided",
     "rationale": {"factIds": ["m_…"], "text": "…"}, "confidence": null},
    {"methodId": "rule:process_errors", "kind": "rule", "registered": true, "ruleVersionId": "<rule version UUID>",
     "executable": {"process": true, "window": false}, "selectable": true, "selectableReason": null,
     "action": "enable", "parameters": null, "parameterStatus": "not_proposed", "parameterValidation": "not_provided",
     "rationale": {"factIds": [], "text": "…"}, "confidence": null}
  ],
  "unregistered": [
    {"proposedId": "sr:changepoint_bocpd", "kind": "sr", "reason": "not_in_catalog", "rationale": {"text": "…"}}
  ],
  "reviews": [{"stage": "schema", "passed": true}, {"stage": "catalog_match", "passed": true, "unregisteredCount": 1}]
}
```

| 필드 | 규칙 |
|---|---|
| `recommendations[].methodId` | 카탈로그 식별자 체계와 동일(`<kind>:<key>`). 서버가 카탈로그·등록 Rule과 대조해 `registered=true`인 것만 이 배열에 둠 |
| `unregistered[]` | 모델 출력 중 목록에 없는 식별자. 선택 불가 표시용. 자동 등록·Rule 초안 생성 없음 |
| `action` | `enable` / `disable` / `keep`(초안 상태 유지) ★ — draft에 없는 방법에 `disable`은 허용하지 않음. `disable`·`keep`은 **제안 표시만**이며 자동 체크 대상이 아님(WebUI 검토 의견 1) |
| `selectable`·`selectableReason` | 서버가 항목마다 계산(WebUI 검토 의견 1): `registered ∧ executable.process ∧ (kind=ml이면 학습 후보 ≥1, `trainedCandidateCount`) ∧ action=enable` → `true`. 아니면 `false` + 사유 `not_executable` / `no_trained_candidate` / `action_not_enable`. 화면은 `selectable=true`만 자동 체크. `constraints.executableOnly:false`와의 관계: 추천 자체는 실행 불가 방법도 포함(표시·근거 제공)하되 `selectable=false`로 구분 |
| `trainedCandidateCount` | `kind=ml`만. 같은 source의 성공한 train 후보 중 그 `model_key` 행 수(응답 시점 계산) |
| `parameters`·`parameterStatus` | `proposed`(모델 제안) / `not_proposed`. `proposed`여도 **자동 적용 없음** [확정]; 사람이 적용을 누르면 구성 초안의 `parameterSource=llm_proposed`로 들어가고 저장해야 버전이 됨 |
| `parameterValidation` | 서버 대조 결과 `valid`(카탈로그 `parameterSchema` 통과) / `invalid`(위반, 값은 보존하되 WebUI가 적용 불가 표시) / `not_provided`. `ml:*` 파라미터 제안은 재학습이 필요함을 WebUI가 안내(구성 저장 시 `ml_parameters_require_training`) |
| `executable` | 응답 시점 카탈로그 값 병기. 실행 불가 방법 추천은 허용하되 그대로 표시 |
| `rationale.factIds` | 입력 집계의 measurement/baseline ID(`m_…`, `b_…`)만. 입력에 없는 ID는 검증 실패 |
| `confidence` | v1은 항상 null(근거 없는 수치 금지) |
| `reviews[]` | 서버 검증 단계 결과. `schema`(출력 스키마) → `catalog_match`(식별자 대조) → `parameters`(스키마 대조) → `facts`(factIds 존재) |

- 검증 실패 처리: 출력 스키마 위반·`truncated=true`·전부 미등록 → 작업 `failed` + `errorCode`(`invalid_method_recommendation`, `recommendations_empty`). **빈 추천을 성공으로 대체하지 않음** [확정 WebUI 3절]
- 상한: `recommendations` ≤ 32, `unregistered` ≤ 32, `rationale.text` ≤ 500자 ★
- 저장: 성공 결과는 `run_llm_results`(kind·evidence_version·hash·reuse_key·output) + `run_jobs.result_summary`(현행 `_finish` 경로 `trainer/runner.py:619-627`)

## 작업 상태 ↔ WebUI 3절

| WebUI 상태 | API |
|---|---|
| 대기 | 작업 없음 |
| 실행 중 | `status` queued/running(`GET /api/jobs/:id`), 같은 초안 재요청은 `reuse_key`로 기존 작업 반환 |
| 완료 | `succeeded`, `resultSummary` 위 JSON. WebUI는 `selectable=true`만 자동 체크(AND·실행 가능·후보 유무·action을 서버가 이미 계산, WebUI 검토 의견 1); `registered=true`이나 `selectable=false`인 항목은 비활성으로 표시 |
| 적용 선택 | 서버 호출 없음. 적용 = 화면 초안 반영, 저장 시 `createdFrom.recommendationJobId` 기록([detection-config](detection-config-version.md)) |
| 실패 | `failed` + `errorCode`(`generation_service_unavailable`, `invalid_method_recommendation`, `recommendations_empty`). 재시도는 새 멱등키로 새 요청 |
| 시간 초과 | `failed` + `errorCode: generation_timeout` **[확정 — 사용자 10/21, 설계 방향 4.4절: 시간 초과는 실패와 별도 표시]**. 현행 `generation_client.py:54-55`는 `TimeoutError`를 `generation_service_unavailable`로 묶으므로 변경 제안(아래 표). 재시도 규칙은 실패와 같음 |

## 마이그레이션·코드 영향 ★

| 대상 | 변경 |
|---|---|
| `job_kind` enum | `recommend_methods` 추가(`schema.dbml:21-30`) — PostgreSQL 세션 |
| `run_llm_results.kind CHECK` | `('recommend_rule','recommend_feature','explain')` → `recommend_methods` 추가(`004_erd_v03_contracts.sql:112`); `decision_id IS NULL` 조건 분기 추가(`:120-121`) |
| 004 결과 검증 트리거 | "LLM completion requires result" kind 목록에 추가(`004:227`) |
| `jobRoutes.ts` | `KINDS`(`:15`)·허용 키(`:47`)에 `recommend_methods`·`draft`·`scope`, `llmSpec` 분기(`:221-228`), 큐 선택(`:257, 272`) |
| `trainer/runner.py` | `role='llm'` claim은 큐 기준이라 변경 없음(`:682-691`); `_execute`에 `recommend_methods` 분기(입력 집계 → `generation_client.generate("methods", payload)` → 검증·대조); `_finish`의 `run_llm_results` 저장 kind 집합(`:619`)에 추가 |
| `generation_client.py` | 허용 kind `{"feature","rule","explain"}`(`:39`)에 `"methods"` 추가. 원격 서비스·로컬 `DesignModel` 모두 같은 kind |
| `generation_client.py:54-55` | `except (URLError, TimeoutError, OSError)` → `GenerationUnavailable("generation_service_unavailable")` 단일 | `TimeoutError`(및 `socket.timeout`·`URLError.reason`이 timeout인 경우)를 분리해 `GenerationUnavailable("generation_timeout")` — 기존 `recommend_rule`·`explain`에도 같은 코드가 적용되므로 LLM 세션 조율 [확정 근거: 설계 방향 4.4절] |
| `llm_worker.py`·`generation_service.py` | `DesignModel.generate("methods", …)` 어댑터(프롬프트·출력 스키마)는 **연구 산출물 연결 필요**(미존재). 없으면 `GenerationUnavailable` → 작업 `failed`(목업 금지) |
| `queues.md` 7행 | `jobId (recommend_feature/rule)` → `recommend_methods` 추가 |

- GPU·외부 생성 서비스 호출은 사용자 승인 환경에서만. 이 계약은 호출을 요구하지 않음

## 충돌·주의

- 현행 LLM Rule 초안 키 `sr_<hash>`·코드 `SR_DIAGNOSTIC`(`research_design.py:228, 231`)은 `rule:sr_<hash>` 인스턴스이며 `sr:*` 통계 방법이 아님 — Detector D12 예약 접두(`SR_*`) 도입 시 이름 정리 필요(Detector D9-3 후속, LLM·Trainer 세션)
- 등록 Rule에 승인 상태가 없어(`rules.md`) `registeredRules`는 draft 전체가 후보. HR 등록 목록 경계(승인 상태)는 별도 결정
- `recommend_rule`은 폐지 경로(위 관계 절 [확정])이므로 통합 kind 대안 B는 소멸. 기존 결과 읽기 보존만 남음

## 결정 대기

1. Q-M 입력 범위: ★A 집계값만·파라미터 제안 허용
2. Q-N kind 분리: **[확정 10/21]** `recommend_rule` 폐지 경로, `recommend_methods`만 유지(차단 시점은 LLM 세션)
3. Detector Q2 (나) — **[확정 10/21]** LLM Rule 생성 폐지 예정. 남은 것: `recommend_rule` 신규 요청 차단 시점·기존 결과 읽기 보존 방식(LLM 세션)
4. 어댑터·프롬프트 출처(연구 산출물), `model_version` 값
5. `action`·`constraints`·상한 값
6. `generation_timeout` 오류 코드 분리 — **[확정 10/21]** 분리(설계 방향 4.4절). 남은 것: `generation_client.py` 변경 시 기존 kind 영향 범위(LLM 세션)
