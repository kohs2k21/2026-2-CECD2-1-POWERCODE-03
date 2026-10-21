# 판정 구성 버전 계약 v1 (초안, Issue #74)

- 계약 버전: `detection-config-v1`; 구현 전 초안. 근거 코드는 origin/dev `872f540`
- 상위 결정: MyDocs `20261020-판정파이프라인-플러그형-설계방향.md` 4·4.1·4.2절(세부 방법 on/off·SR/ML 0..N·scikit-learn·Git 기준 UI 범위·실행 가능 별도 필드·Q-A/Q-H 답변)
- 상태: 초안 — 사용자 결정 반영 완료(10/21 일괄, 설계 방향 4.7절), 구현은 MyDocs `plans/20261021-Issue74-구현분할안.md` 참조. 표기: **[확정]** 사용자 확정 또는 현재 코드 사실 / **충돌** 기존 계약·코드와 어긋나 구현 Issue에서 바꿀 항목(결정은 끝남)
- 범위: 불변 판정 구성 버전 = HR·SR·ML 세부 방법 목록(식별자·enabled·파라미터·산출물 참조) + 조합 정책 참조 + 데이터 계보. 저장마다 새 버전, UPDATE 없음
- 제외: 종합 판정 조합 규칙·등급·evidence v4(Detector 계약 소유, 이 문서는 참조만), 카탈로그 내용([method-catalog](method-catalog.md)), 비교 평가([config-comparison-evaluation](config-comparison-evaluation.md)), LLM 추천([llm-recommendation-job](llm-recommendation-job.md)), WebUI 화면
- 호환 원칙: 기존 키·열·응답 유지, 추가만([trainer-service-v1](trainer-service.md) "기존 결과 변경 금지"와 동일). schemaVersion 1·2 구성은 변환·재해석 없이 기존 경로로 실행

## 방법 식별자 [확정 Q-A=A]

- 형식 `<kind>:<key>`; kind ∈ `hr`·`rule`·`sr`·`ml`; key [확정 10/21] `^[a-z][a-z0-9_-]{1,63}$`
- `hr:process-status` 상태 HR(시스템 제공, `status_rule.py:2`), `rule:<rule_key>` 등록 Rule DSL(HR 대분류 소속 — **[확정 10/21]** Detector Q2 A, 설계 방향 4.5절), `sr:<method_key>` 통계 SR, `ml:<model_key>` ML 모델
- 대분류 매핑: hr·rule → HR, sr → SR, ml → ML. 대분류별 결과 조합은 Detector 계약
- 구성 안에서 식별자 유일: 같은 방법은 구성당 1개. 같은 모델을 파라미터만 바꿔 2개 넣는 것은 불가(`ml_config_models` PK `(config_version_id, model_key)` 그대로, `schema.dbml:413`)
- `rule:<rule_key>`는 버전 1개만; 같은 `rule_key`의 다른 버전 2개 동시 포함 거부 [확정 10/21](현행 API는 UUID 중복만 검사 `jobRoutes.ts:108`, DB 유일성은 `(rule_key, version)`)
- `requestedMode` 파생(Detector D7 B, Q-A 답변에 포함): 활성 ML 0개 → `rule_only` / 활성 HR·rule·SR 0개 → `ml_only` / 그 외 `parallel`. v3에는 mode 입력 없음

## configuration_spec schemaVersion 3

- 저장 위치: `ml_config_versions.configuration_spec`(`schema.dbml:391-401`). 기존 값 1(ML 후보, `trainer/runner.py:402-407`)·2(rule_only 후보, `:349-352`)와 의미가 다르므로 3부터 사용
- `ml_config_models` N행(활성 `ml:*` 1개당 1행), `ml_config_rules` N행(활성 `rule:*` 1개당 1행, `evaluation_order` = `methods[]` 안 `rule:*` 순서)

```json
{
  "schemaVersion": 3,
  "contractVersion": "detection-config-v1",
  "contentHashVersion": 2,
  "sourceId": "<source UUID>",
  "baseVersionId": "<config UUID|null>",
  "displayName": "실험 7 — HR + IF(v1 기본값) + EWMA",
  "catalogVersion": "method-catalog-v1",
  "featureSetId": "<feature set UUID|null>",
  "trainingSnapshotId": "<snapshot UUID|null>",
  "methods": [
    {"methodId": "hr:process-status", "kind": "hr", "enabled": true,
     "methodVersion": "process-status-hr-v1", "parameters": {}, "parameterSource": "system"},
    {"methodId": "rule:process_errors", "kind": "rule", "enabled": true,
     "ruleVersionId": "<rule version UUID>", "ruleVersion": 3},
    {"methodId": "sr:ewma", "kind": "sr", "enabled": true, "methodVersion": "research-trend-sr-R06",
     "parameters": {"window": 60, "aggregation": 1, "baseline": "B1"}, "parameterSource": "catalog_default"},
    {"methodId": "ml:iforest", "kind": "ml", "enabled": true, "methodVersion": "process-iforest-v1",
     "featureSetId": "<feature set UUID>",
     "parameters": {"n_estimators": 128, "max_samples": 512, "contamination": "auto",
                    "random_state": 42, "thresholdPolicy": "train-internal-p99-v1"},
     "parameterSource": "catalog_default",
     "artifacts": {"candidateConfigVersionId": "<train 후보 config UUID>", "trainJobId": "<job UUID>",
                   "modelArtifactId": "<artifact UUID>", "fitArtifactId": "<artifact UUID>"}}
  ],
  "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hr": 1, "sr": 1, "ml": 1}, "deadlineMs": 3000},
  "statusRulePolicy": {"version": "process-status-hr-v1", "sourceId": "<source UUID>",
                       "mapping": {"S": "no_hit", "F": "critical", "P": "warning", "N": "warning"}},
  "ruleVersionIds": ["<rule version UUID>"],
  "trainingSelection": null,
  "createdFrom": {"kind": "compose", "recommendationJobId": null}
}
```

| 키 | 의미·규칙 |
|---|---|
| `schemaVersion` | 정수 3 고정 |
| `contractVersion` | `detection-config-v1` |
| `contentHashVersion` | 정수 2 [확정 10/21](spec 키로 저장). hash 입력 규칙 버전(아래 hash 절). 목록·`GET /:id` 응답에 그대로 노출. v1·v2 행은 키가 없으므로 응답에서 `contentHashVersion: 1`로 투영 |
| `sourceId` | 구성이 속한 source; 모든 참조(후보·Rule·피처셋)의 source와 일치 |
| `baseVersionId` | 출발점 구성(복제·수정 전 버전) 또는 null; `ml_config_versions.base_version_id`에 같은 값 |
| `displayName` | 1~120자, 표시용. 내용 hash 계산에서 **제외** [확정 10/21](WebUI 검토 의견 5) |
| `catalogVersion` | 저장 시점 카탈로그 버전. 실행 가능 여부(`executable`)는 **저장하지 않고** 목록·실행 시 현재 카탈로그로 다시 계산 |
| `featureSetId` | 등록 Rule `F.*`가 묶이는 피처셋(Rule `input_spec.featureSetId`와 일치, `configRoutes.ts:127-136` 검사 유지). Rule·ML 모두 없으면 null |
| `trainingSnapshotId` | 활성 ML 방법이 1개면 그 후보의 학습 스냅샷; 여러 개면 null, 각 `ml:*`의 후보가 보유 |
| `methods[]` | 1개 이상. 식별자 유일. 순서 = Detector 대표 코드·evidence 순서(D15), `rule:*` 순서 = `evaluation_order`. 단 상태 HR(`hr:process-status`)은 배열 위치와 무관하게 대표 코드 최우선(Detector D12) |
| `methods[].enabled` | boolean. `false` 항목은 **spec·hash에는 보존**, **실행·DB 자식 행(`ml_config_models`·`ml_config_rules`)에서는 제외**, Detector는 skipped [확정 10/21]. 활성 방법 0개 구성은 거부 |
| `methods[].parameters` | 카탈로그 `parameterSchema`로 검증. `ml:*`는 학습된 후보의 실제 값(`training_settings.parameters`)을 서버가 채움 — 요청이 다른 값을 주면 422 `ml_parameters_require_training`(파라미터 변경 = 재학습) |
| `methods[].parameterSource` | `system` / `catalog_default` / `user` / `llm_proposed` [확정 10/21]. `llm_proposed`는 사람이 저장을 눌렀다는 뜻이며 자동 적용 아님 |
| `methods[].artifacts` (`ml:*`) | 학습 후보(`kind=train` 성공 결과)의 `ml_config_models` 행을 가리킴. 파일 복사 없음, artifact ID 재사용. 후보 없는 모델은 선택 불가(WebUI 10/21 선택 제한) |
| `detectionPolicy` | **[확정 10/21, Detector D6·설계 방향 4.6절]** 키 이름은 기존과 같은 `detectionPolicy`이며 v3에서는 **v2 객체** `{version: 2, policyKey, k: {hr, sr, ml}, deadlineMs}`를 담음. 필드 세부·정책 키 목록·검증 규칙은 Detector 계약 Issue가 문서화하고 이 문서는 참조만. `deadlineMs`는 기존 v1 `detectionPolicy.deadlineMs`(100~30000) 승계. **v1/v2 구분 규칙**: `version` 필드가 없거나 1이면 v1(`{mode, deadlineMs, combination}` 문자열 mode 기반), `version: 2`이면 v2 객체. 효과: 현행 worker `parse_policy`(`policy.py:30-31`, 키 집합 정확 일치)는 v2 객체를 받으면 예외로 **자동 fail-closed**(B1 절), TS(`jobRoutes.ts:94-101`·`configRoutes.ts:107`·`inputRoutes.ts:22`)는 `version` 분기 추가 필요 |
| `statusRulePolicy` | `hr:process-status`가 enabled일 때만 존재하는 **파생 복사본**(`detector/computation.py:24-28`, `applications.py:46-48`, `service_contract.py:57` 호환). 값은 `status_rule.default_policy`와 정확히 일치(`status_rule.py:13-16`) |
| `ruleVersionIds` | 활성 `rule:*`의 `ruleVersionId` 순서대로 — 파생 복사본(`service_contract.py:48` 호환) |
| `trainingSelection` | 활성 ML 1개면 그 후보의 값 복사, 여러 개면 null(각 후보가 보유), ML 0개면 null |
| `createdFrom` | `{kind: "train"|"compose", trainJobId?, recommendationJobId?}`; LLM 추천 결과에서 시작한 저장은 `recommendationJobId` 기록 |
| `operationalEligibility`·`preparedDatasetId`·`preparedInputContract` | 기존 키 유지([prepared-csv-v1](prepared-csv-import.md)). 활성 `ml:*` 후보 중 하나라도 prepared 계보면 구성 전체에 `operationalEligibility.eligible=false` 전파 [확정 10/21] |

- 내용 hash(`content_hash`) [확정 10/21](WebUI 검토 의견 5): **실제 구성 요소만** key 정렬 compact JSON으로 SHA-256(`trainer/runner.py:408`와 같은 직렬화) — `sourceId`, `featureSetId`, `methods[]`의 `methodId`·`enabled`·`parameters`·`ruleVersionId`·`artifacts.modelArtifactId/fitArtifactId`·`featureSetId`, `detectionPolicy`(v2 객체). `displayName`·`baseVersionId`·`createdFrom`·`catalogVersion`·`trainingSnapshotId`·파생 복사본(`statusRulePolicy`·`ruleVersionIds`·`trainingSelection`)은 제외. `schemaVersion`·`contractVersion`·`contentHashVersion`도 hash 입력에서 제외(버전 구분은 아래 중복 판정 범위로 처리); `requestedMode`는 파생값이라 spec에 저장하지 않고 응답에서만 계산하므로 hash와 무관. **충돌**: 현행 v1·v2는 spec 전체 hash이므로 v3는 hash 입력 규칙이 다름을 `contentHashVersion: 2` 키로 표기 [확정 10/21]
- 중복 판정 범위: 같은 `sourceId` **이고 `contentHashVersion=2`인 행끼리만** hash 비교. v1·v2 행(전체 hash)과는 비교하지 않음
- 같은 구성 재저장 **[확정 — 사용자 10/21, 설계 방향 4.4절]**: 막지 않고 기존 버전으로 안내. 형식 [확정 10/21]: 새 행을 만들지 않고 `200 {id: "<기존 config UUID>", duplicateOf: "<기존 config UUID>", ...}`로 기존 버전을 반환(`displayName`이 달라도 hash 제외 항목이므로 같은 구성). 중복이 아니면 `201`·`duplicateOf: null`

## 생성 경로

### (A) 학습 — `POST /api/jobs kind=train` [기존]

- 현행: ML 후보 1개(`iforest`) + HR + Rule 0..32 → schemaVersion 1·2 구성 생성(`trainer/runner.py:349-352`, `:402-407`). 요청 형식·권한·멱등키 유지
- [확정 10/21] 확장(구현 단계 2 이후): `modelKey`에 카탈로그 `ml:*` 키(접두 없이 `<model_key>`) 허용, `executable.process=false`인 키는 422 `method_not_executable`; 선택 `parameters`(카탈로그 schema 검증, 생략 = `defaults`); 성공 결과의 구성은 schemaVersion 3(`methods` = HR + rule + ML 1개, `createdFrom.kind="train"`), `training_settings.parameters`에 실제 사용값 기록
- 기존 요청(`modelKey` 생략·`parameters` 없음)은 그대로 유효하며 IF v1 기본값으로 학습 [확정 Q-H=A]
- **충돌**: 현행 검증은 `detectionPolicy`를 v1 형식(`{mode, deadlineMs, combination}` 정확 일치)으로만 받음(`jobRoutes.ts:94-101`, `pipeline.md:20`) → v3 생성 요청은 같은 키에 **v2 객체(`version: 2`)** 를 받고 mode는 파생. v1 형식 요청은 계속 허용. **v1 형식 키(`mode`·`combination`)와 v2 형식 키(`version`·`policyKey`·`k`)를 한 객체에 섞으면 400 `invalid_detection_policy`** [확정 10/21](N16 재서술)

### (B) 조합 저장 — `POST /api/configurations` [신규, 관리자, 동기 [확정 10/21] Q-D=A]

```json
{
  "sourceId": "<source UUID>",
  "baseVersionId": "<config UUID|null>",
  "displayName": "실험 7 — HR + IF(v1 기본값) + EWMA",
  "featureSetId": "<feature set UUID|null>",
  "methods": [
    {"methodId": "hr:process-status", "enabled": true},
    {"methodId": "rule:process_errors", "enabled": true, "ruleVersionId": "<rule version UUID>"},
    {"methodId": "sr:ewma", "enabled": true, "parameters": {"window": 60, "aggregation": 1, "baseline": "B1"}, "parameterSource": "user"},
    {"methodId": "ml:iforest", "enabled": true, "artifacts": {"candidateConfigVersionId": "<train 후보 config UUID>"}}
  ],
  "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hr": 1, "sr": 1, "ml": 1}, "deadlineMs": 3000},
  "createdFrom": {"kind": "compose", "recommendationJobId": null}
}
```

- 헤더 `Idempotency-Key`(1~128자 `[a-zA-Z0-9_.:-]`) 선택; 같은 키·같은 본문 200, 다른 본문 409 `idempotency_payload_conflict`(기존 applications 규약 재사용)
- 응답 `201 {id, sourceId, schemaVersion: 3, contentHash, contentHashVersion: 2, requestedMode, executable, createdAt, duplicateOf: null}`; 같은 구성이 이미 있으면 `200` + 기존 버전·`duplicateOf`(위 hash 절, [확정] 차단 없음)
- 학습 없음. 서버가 `ml:*`의 `parameters`·`featureSetId`·`methodVersion`·artifact ID를 후보의 `ml_config_models` 행에서 채움(`candidateConfigVersionId`만 받음)
- 검증(순서대로, 첫 실패에서 중단)

| 검사 | 오류 코드(400/404/409/422) |
|---|---|
| 본문 키 집합·타입·UUID 형식, `methods` 1개 이상 | 400 `invalid_configuration_request` |
| `methodId` 카탈로그에 없음 | 422 `method_unknown:<id>` |
| 활성 방법에 `executable.process=false`(연구 폴더 항목) **[확정 Q3-1, 10/21]** | 422 `method_not_executable:<id>` — 저장 자체 거부 |
| 같은 `methodId` 2개 | 422 `method_duplicate:<id>` |
| 활성 방법 0개 | 422 `detection_methods_empty` |
| `rule:*`에 `ruleVersionId` 없음·존재하지 않음·retired·`rule_key` 불일치 | 422 `rule_version_invalid:<id>` |
| 같은 `rule_key` 다른 버전 2개 | 422 `rule_key_duplicate:<rule_key>` |
| Rule `input_spec.featureSetId` ≠ `featureSetId` | 409 `rule_feature_set_mismatch` |
| `ml:*` 후보 없음·다른 source·후보에 그 `model_key` 행 없음·artifact NULL | 422 `ml_candidate_invalid:<id>` |
| `ml:*` `parameters`를 주었고 후보 실제 값과 다름 | 422 `ml_parameters_require_training:<id>` |
| `parameters`가 카탈로그 `parameterSchema` 위반 | 422 `method_parameters_invalid:<id>` |
| `detectionPolicy`(v2 객체) 검증(Detector 계약 규칙: 활성 대분류 1개 이상·1 ≤ k ≤ n 등) | 422 Detector 계약이 정한 코드(예 `combination_k_out_of_range`) |
| `hr:process-status` `parameters`가 비어 있지 않음(매핑 고정) | 422 `method_parameters_invalid:hr:process-status` |

- 실행 불가 방법(`executable.process=false`)은 **선택 자체 불가 — 저장 거부** 422 `method_not_executable:<id>` **[확정 — 사용자 10/21, Detector Q3-1·설계 방향 4.5절]**. 이전 초안의 "저장 허용 [확정 10/21]"은 폐기. 연구 폴더 항목은 개발 병행 중 화면에 비활성으로 보일 뿐이며 최종 버전 목록에는 없음. 학습(A)·평가·적용 요청도 같은 코드로 거부. Detector 런타임은 적용 뒤 어댑터가 사라진 예외 상황에서만 방법 결과 `failed`+`method_not_executable`(D13 B 방어 경로)
- 파일 hash 재검증은 하지 않음(적용 단계 `applications.prepare_config`가 수행). 사전 조사 Q-D B(Trainer 작업 `kind=compose`)는 채택하지 않음 [확정 10/21]

## 조회

- `GET /api/configurations?sourceId=<uuid>&limit=<1..100>&cursor=<base64url>&createdFrom=<train|compose>&modelKey=<model_key>&executable=<true|false>` [신규]: 최신순 커서 페이지(`reports/candidates`와 같은 커서 형식 `reportRoutes.ts:115-121`). 필터(WebUI 검토 의견 2·10): `createdFrom` = train 후보 vs compose 조합본, `modelKey` = 활성 `ml:<model_key>` 포함 구성, `executable` = 버전 단위 실행 가능 여부. v1·v2 행은 `createdFrom=train`으로 투영

```json
{"items": [{"id": "<config UUID>", "sourceId": "…", "schemaVersion": 3, "displayName": "…", "contentHash": "…", "contentHashVersion": 2,
            "createdAt": "…", "requestedMode": "parallel", "active": true,
            "createdFrom": {"kind": "compose", "trainJobId": null, "recommendationJobId": null},
            "executable": true,
            "methods": [{"methodId": "ml:iforest", "kind": "ml", "enabled": true, "status": "supported",
                         "executable": {"process": true, "window": true}, "executableReason": null}],
            "operationalEligibility": {"eligible": true, "reason": null},
            "applicationEligibility": {"eligible": true, "evaluationId": "<run_evaluations UUID>", "reason": null},
            "latestEvaluation": {"evaluationId": "…", "snapshotId": "…", "purpose": "validation", "metricVersion": "…",
                                 "applicability": "valid", "completedAt": "…"},
            "latestComparison": {"comparisonJobId": "…", "evaluationSetId": "…", "status": "succeeded", "completedAt": "…",
                                 "recommendation": {"targetVersionId": "…", "state": "current"}}}],
 "pagination": {"limit": 50, "hasMore": false, "nextCursor": null}}
```

- 버전 단위 `executable`(WebUI 검토 의견 10): 활성 방법 전부 `executable.process=true`일 때만 true — 응답 시점 카탈로그로 재계산(저장값 아님). 저장 시 실행 불가 방법이 거부되므로(Q3-1 [확정]) **false는 저장 뒤 카탈로그가 바뀌어 사후 실행 불가가 된 경우(어댑터 소실·deprecated)에만** 생김. 그 버전은 교체 후보에서 제외(WebUI 2.2절)
- `applicationEligibility`(WebUI 검토 의견 3): 서버가 기술 무결성 gate(`purpose=validation ∧ applicability=valid ∧ scored_count>0 ∧ unavailable_count=0 ∧ prepared 아님`, `configRoutes.ts:90-99`; 비교 작업이 만든 행·기존 `evaluate` 행 모두 대상, 합성 세트 비교 행 포함 — [확정 10/21])을 만족하는 **가장 최근 적격 평가** 1건과 버전 `executable=true`를 확인해 `{eligible: true, evaluationId, reason: null}`; 없으면 `{eligible: false, evaluationId: null, reason}`(`no_eligible_evaluation` / `method_results_unavailable` / `operational_ineligible` / `not_executable` — 마지막 것은 사후 어댑터 소실 경우만). `latestEvaluation`(최신 1건, 적격 무관)과 별개. 적격이어도 적용은 사람 요청으로만
- `methods[].status`·`executable`은 응답 시점 카탈로그로 계산. schemaVersion 1·2 행은 **읽기 전용 투영** [확정 10/21]: `statusRulePolicy` 유무 → `hr:process-status`, `ml_config_rules` → `rule:*`, `ml_config_models` → `ml:iforest`, `detectionPolicy.mode` → `requestedMode`. 저장 내용은 바꾸지 않음
- `GET /api/configurations/:id` [기존 확장]: 기존 `{id, sourceId, configurationSpec, contentHash, createdAt}`(`configRoutes.ts:46-56`) 유지 + `schemaVersion`, `displayName`, `requestedMode`, `active`, `methods[]`(위 형식 + `parameters`·`artifacts`), `models[]`(`ml_config_models` 행: `modelKey, featureSetId, modelArtifactId, fitArtifactId, adapterSpec, trainingSettings, thresholdSpec`), `rules[]`(`ml_config_rules` 조인: `ruleVersionId, ruleKey, version, evaluationOrder`)
- `GET /api/pipeline/reports/candidates`는 그대로 둠(학습 후보 이력). 목록 API와 역할이 겹치나 기존 소비처 보존 [확정 10/21]. 관계(WebUI 검토 의견 2): `candidates` = train 후보(구성당 최신 평가 1건) 전용 보고, 새 목록 = train 후보 + compose 조합본 전체이며 `createdFrom=train` 필터가 `candidates`와 같은 집합

## 운영 교체·복원

- `POST /api/configurations/:id/applications` 요청·응답·멱등키 그대로(`configRoutes.ts:58-170`). `operation` apply/restore, `evaluationId`, `expectedRevision`, `expectedActiveVersionId`
- gate = **기술 무결성만** [확정 — 사용자 10/21, 설계 방향 4.4절]: 해당 구성 ID의 `purpose=validation` 평가가 `applicability='valid' AND scored_count>0 AND unavailable_count=0`(모든 행 complete·방법 결과 unavailable 0) + prepared 아님(`configRoutes.ts:90-99`, `applications.py:102-109`, SQL 형식 불변) + 버전 `executable=true` + 산출물 경로·hash 검증(`prepare_config`). 최종 관문은 사람 수락
- `evaluationId`에 **비교 평가가 만든 구성별 `run_evaluations` 행** 허용 — 합성 행 포함 세트의 비교 행도 기술 무결성을 만족하면 적격 [확정 10/21]([comparison](config-comparison-evaluation.md) 적용 gate 절). "원본 validation 평가 필수"는 "평가 세트 비교 + 사람 수락"으로 대체. 기존 `evaluate` 원본 validation 행도 계속 유효
- 요청 본문 선택 필드 `acceptedRecommendation: {comparisonJobId, targetVersionId}` [확정 10/21 N22]: 비교 결과 `replacementRecommendation`을 수락해 요청했음을 기록. 화면은 추천의 `targetEvaluationId`를 `evaluationId`에, `baselineActiveRevision`·`baselineConfigVersionId`를 `expectedRevision`·`expectedActiveVersionId`에 그대로 넣음. 서버 검증: ① `targetVersionId == :id` ② 그 비교 작업의 저장된 추천이 그 대상을 가리킴 ③ `evaluationId == targetEvaluationId`(불일치 400 `accepted_recommendation_mismatch`) ④ 추천이 stale(`baselineActiveRevision ≠ ops_system_state.revision`)이면 400 `accepted_recommendation_stale` [확정 R10] — `expectedRevision` 409보다 먼저 검사해 사용자에게 "추천이 낡음"을 구분 표시. 추천 없이 사람이 직접 고른 교체 요청은 이 필드 없이 그대로 허용. 기존 키 집합 검사(`configRoutes.ts:62`)에 키 추가
- v3 구성의 준비 검사(Detector 소유, 여기서는 요구만): mode 분기 대신 `methods[]` 기준 — 활성 방법 0개 거부, 활성 `ml:*`마다 artifact·fit·feature set 일치(현행 `model_key='iforest'` 정확히 1행 `configRoutes.ts:117-126`·`applications.py:13-32`을 N행으로), 활성 방법 중 사후 어댑터 소실로 `executable.process=false`가 된 것이 있으면 `method_not_executable:<id>`(D13 B 방어 경로; 저장 시 거부되므로 드묾), `rule_feature_set_mismatch` 유지
- `gate_evidence`에 `comparisonJobId`·`evaluationSetId`·`acceptedRecommendation`·`recommendationRuleVersion`을 추가 기록 [확정 10/21](`gateVersion: 2`); 기존 `{evaluationId, gateVersion: 1}` 형식 유지. 교체 이력에서 "추천 수락 여부"를 구분 표시 가능
- **자동 교체 금지** [확정]. 시스템은 추천(`replacementRecommendation`)만 하고 운영 변경은 관리자의 명시 요청(수락)으로만

## 기존 저장소·계약과의 호환 — 마이그레이션 필요 여부

| 대상 | v3 사용 방식 | 변경 필요 |
|---|---|---|
| `ml_config_versions` | `configuration_spec` v3, `base_version_id`, `content_hash`, `created_by_actor_id`=요청 관리자 | **없음**. 선택 [확정 10/21]: UPDATE/DELETE 금지 트리거(003이 `ml_rule_versions`에만 적용한 방식) |
| `ml_config_models` | 활성 `ml:*` 1개당 1행, PK `(config_version_id, model_key)` 그대로. `adapter_spec.name` = 카탈로그 어댑터 이름(현행 `sklearn-iforest`), `training_settings`에 `parameters`·`methodVersion` 키 추가 [확정 10/21], artifact ID는 후보 행 재사용 | **없음**(키 추가는 jsonb) |
| `ml_config_rules` | 활성 `rule:*` 1개당 1행, `evaluation_order` = `methods[]` 안 순서 | **없음** |
| `ml_artifacts` | 재사용(복사 없음). `format_version`은 카탈로그 `methodVersion`과 일치 | **없음** |
| `run_jobs` | 조합 저장은 동기라 작업 없음. train `input_spec`에 `parameters`·`detectionPolicy`(v2 객체) 키 추가 [확정 10/21] | **없음** |
| `ops_system_state`·`run_applications` | 그대로. `gate_evidence` jsonb 키 추가 | **없음** |
| schemaVersion 1·2 구성 | 변환하지 않음. Detector·Trainer·gate는 기존 경로로 실행, 목록 API는 투영만 | 코드: Detector v3 분기 신설(`policy.parse_policy`는 키 집합 정확 일치 검사 `policy.py:30-31`라 v3는 별도 파서) |

- 결론: 이 계약만으로는 **DB 마이그레이션 불필요**. 비교 평가·LLM 추천 계약이 요구하는 마이그레이션은 각 문서 참조

## schemaVersion 3 판독 규칙 — fail-closed (Detector 차단 요청 B1)

- 규칙: `configuration_spec.schemaVersion`을 모르는 판독기(3을 지원하지 않는 Detector·Trainer·API 코드)는 그 구성을 **읽지 않고 실패**해야 함 — 오류 코드 `unsupported_config_schema`. v3 구성을 v1 규칙(IF 1개·mode 분기)으로 조용히 해석하는 것을 금지
- worker 측 이점 [확정 4.6절]: v3 spec은 `detectionPolicy`에 v2 객체를 담으므로 현행 `parse_policy`(`policy.py:30-31`)가 키 집합 불일치(`detection_policy_required`)로 예외 → 옛 Detector·Trainer 판독기는 **자동 fail-closed**(legacy `parallel/3000/any_anomaly` 조용한 읽기 `policy.py:26-29`는 키가 **없을 때만** 발동하므로 v3에는 해당 없음). 단 오류 코드가 `unsupported_config_schema`가 아니므로 `schemaVersion` 검사를 앞에 두어 코드를 정확히 함
- TS 측 충돌(현행): `configRoutes.ts:107`은 `detectionPolicy.mode ?? 'parallel'`로 기본값 처리, `inputRoutes.ts:22`는 `->>'mode'`를 그대로 표시 → v2 객체에는 `mode`가 없어 **조용히 `parallel`/null로 오해석**. 따라서 TS 판독기는 `schemaVersion`·`detectionPolicy.version` 분기를 별도로 추가해야 함
- 소비처 목록(워크트리 872f540 기준, 모두 `schemaVersion` 검사 추가 대상)

| 소비처 | 현행 | 필요 변경 |
|---|---|---|
| `server/worker/detector/computation.py:17-60` (`load_context`) | `statusRulePolicy`·`parse_policy`·`ml_config_models` 읽기, schemaVersion 미검사 | v3 분기 전까지 `schemaVersion ∉ {None,1,2}` → `unsupported_config_schema` |
| `server/worker/detector/policy.py:26-37` (`parse_policy`) | 키 없음 → legacy; 키 집합 불일치 → 예외(v2 객체는 여기서 자동 fail-closed) | v3는 `version: 2` 파서 분기, legacy 분기는 schemaVersion 1·2(또는 없음)에만 |
| `server/worker/detector/applications.py:35-55` (`prepare_config`) | mode 분기 | 적용 준비 전 schemaVersion 검사 |
| `server/worker/trainer/runner.py:340-341` (train: `parse_policy(spec, required=…)`) | 요청 spec의 `detectionPolicy` 기준 | v3 요청(`detectionPolicy`(v2 객체))은 별도 검증 |
| `server/worker/trainer/runner.py:459` (`_evaluate` → `load_context`) | 위와 동일 | 위와 동일 |
| `server/worker/trainer/service_contract.py:21-33` | `ml_config_models` >1 예외 | schemaVersion 검사 + N행 |
| `server/backend/src/routes/configRoutes.ts:107` | `detectionPolicy.mode ?? 'parallel'` 기본값 — v2 객체면 **조용히 parallel로 오해석**(자동 fail-closed 아님) | `detectionPolicy.version === 2`·`schemaVersion` 분기 추가: v3면 mode 파생, 미지 schemaVersion은 409 `unsupported_config_schema` |
| `server/backend/src/routes/inputRoutes.ts:22` | `configuration_spec->'detectionPolicy'->>'mode'`를 목록에 표시 — v2 객체면 null | v3는 `requestedMode` 파생값 표시(null 금지), 별도 분기 |
| `server/backend/src/routes/reportRoutes.ts:97-123` (`candidates`) | spec 그대로 반환 | 투영 규칙(조회 절) 적용 |

- 허용 시점: **v3 구성의 생성(train·compose)과 적용은 Detector v3 로더 Issue가 병합된 뒤에만 허용** — 그 전에는 API가 v3 생성 요청을 422 `config_schema_not_supported_yet`로 거부(구현분할안 Issue 3↔6 의존)

## 기존 계약 문구 — 변경 제안(이 초안에서 수정하지 않음)

| 문서 | 현재 문구 | 제안 |
|---|---|---|
| `pipeline.md:20` | "새 train 요청은 `detectionPolicy:{mode,deadlineMs,combination}` 필수. mode는 rule_only/ml_only/parallel …" | v1 형식 요청에 한함을 명시하고, v3 생성 요청은 `methods[]`+`detectionPolicy`(v2 객체)(Detector 계약) 기준·mode 파생을 추가 |
| `trainer-service.md:13` | capabilities `models[{key,displayName,supported}]` | `methodCatalog` 추가, `supported := executable.process`([catalog](method-catalog.md)) |
| `trainer-service.md:14` | "모델: `iforest`만 지원; … 다른 값 422 `unsupported_model`" | 카탈로그 `ml:*` 중 `executable.process=true`만 허용, 그 외 422 `method_not_executable`; `unsupported_model`은 카탈로그에 없는 키로 의미 축소 |
| `trainer-service.md:15` | "`rule_only`: modelKey 생략 필수" | v3는 mode 파생이므로 v1 요청에 한함 |
| `trainer-service.md:27` | `modelManifest` 단일 | `modelManifests[]`(활성 ML마다 1개) 추가, 기존 `modelManifest`는 활성 ML이 정확히 1개일 때 그 값·아니면 null [확정 10/21] |
| `trainer-service.md:71` | "병렬/rule_only/ml_only 선택 모드·기한·수동 승인 보존" | "v1 구성에 한함; v3는 방법 목록·조합 정책 보존"으로 보완. SR 별칭 금지 조항은 유지 |
| `queues.md:6` | Trainer jobId (train/evaluate/import_csv) | 비교 평가 계약의 새 kind 추가 시 함께 갱신 |
| `GET /api/jobs`(`jobRoutes.ts:342-353`, 최근 50건 고정) | 필터 없음 | `kind` 필터·`since`·커서 페이지 추가 제안(WebUI 검토 의견 9, 기존 응답 키 유지) |
| `GET /api/configurations/applications`(`configRoutes.ts:35-44`, `status='succeeded'`만 50건) | 성공 이력만 | 실패·진행 중(`queued/applying/failed`) 포함 + `createdAt`·`errorCode` 추가 제안(WebUI 검토 의견 9) |
| 작업 상태 표시 | queued/running/succeeded/failed만 | WebUI가 쓰려는 `blocked`(선행 조건 미충족: 실행 불가 방법·적격 평가 없음·활성 구성 변경)는 **DB 상태값이 아니라** 접수 시 4xx 오류 코드 또는 목록의 `applicationEligibility`/`executable`로 표현 — 상태값 추가 없음 [확정 10/21](WebUI 검토 의견 9) |

## 코드 충돌 목록(구현 Issue 대상, 사실)

- 구성당 IF 1개 고정: `detector/computation.py:35-36`(`unsupported_model`), `:65-66`·`:114-115`(모델 1개), `applications.py:13-32`, `configRoutes.ts:117-126`, `service_contract.py:32-33`(`unsupported_model_combination`), `jobRoutes.ts:103-104`, `trainer/runner.py:246-249`
- SR 자리 고정 `not_ready/sr_experiment_pending`: `computation.py:147`, `trainer/metrics.py:33-38`, `synthetic_metrics.py` — v3에서는 SR 방법 0개 → skipped(Detector D13 표), 방법 있음 → 어댑터 실행 또는 `method_not_executable`
- evidence `models[{modelKey:'iforest', anomalous}]`(`computation.py:141`)·`decision_code`·`anomaly_score` 단일 열(`detector/runner.py:102, 124`): 다중 ML 처리는 Detector D12
- 함수명 `*_iforest` 고정(`model_adapters.py:89, 190, 217`): 카탈로그 어댑터 디스패치 필요(Trainer)

## 확정 요약·남은 의존

- 확정(10/21, 설계 방향 4.4~4.7절): Q-A 식별자·mode 파생 / N1 `detectionPolicy` v2 키 / Q-D 동기 조합 저장 API / Q-E 기술 무결성 gate + 세트 비교 + 사람 수락, 자동 교체 금지 / Q3-1 실행 불가 방법 저장 거부 / Q2 등록 Rule=HR·LLM Rule 생성 폐지 / N2 train의 v3 생성은 구현 2단계부터 / N3 hash 입력 규칙(`contentHashVersion: 2`)·`enabled:false` 보존 / N4 ML 파라미터 변경=재학습 / N16 v1·v2 형식 혼용 400 / N19·N23 중복 재저장 안내(200+기존 버전+`duplicateOf`) / N22 `acceptedRecommendation`·stale 400 / 변경 제안 표의 `modelManifests[]`·`blocked` 비상태값 처리
- 남은 의존(결정 아님): v2 `detectionPolicy` 객체 필드 세부·evidence v4(Detector 계약 Issue), 불변 트리거 추가 여부(PostgreSQL 세션 구현 판단, 계약 영향 없음), WebUI 용어 "탐지 버전"(N10, 와이어프레임에서 확정 — 계약 본문 용어는 유지)
