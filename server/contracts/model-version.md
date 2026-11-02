Created: 2026-11-02T18:00:00+09:00
Updated: 2026-11-02T18:00:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft

# 모델 버전 계약 v1 (초안, Issue #94)

- 변경 요약(#94): 기존 [detection-config-version.md](detection-config-version.md)(#74 `detection-config-v1`, configuration_spec v3 = HR·rule·SR·ML 묶음)에서 **룰 항목을 제거**하고 `ml:*`만 남긴 모델 버전 계약. 룰은 [rule-set.md](rule-set.md)(버전 없음). 버전·교체·복원·비교 평가는 **모델 버전에만** [확정 11/02 Q1·Q2]
- 계약 버전: `model-version-v1`(#74 `detection-config-v1` 대체). 근거 결정: 사용자 11/02 결정(Q1 운영 = 룰 1벌 + 모델 0..N, Q2 ML 다중 운영 유지)·Trainer·Detector 분담 합의 11/02. 근거 코드 origin/dev `7f24e03`
- 범위: 불변 모델 버전 = 함께 운영할 ML 모델 목록(식별자·enabled·파라미터·산출물 참조) + 조합 정책 참조 + 데이터 계보(스냅샷·학습 선정). 저장마다 새 버전, UPDATE 없음
- 제외: 종합 판정 조합·등급·evidence v4·`detectionPolicy` 필드 세부(detection-decision.md(#92)), 카탈로그 내용([method-catalog.md](method-catalog.md)), 비교 평가([config-comparison-evaluation.md](config-comparison-evaluation.md)), 임계값 추천([llm-recommendation-job.md](llm-recommendation-job.md)), 학습 제외 구간 요청 형식([trainer-service.md](trainer-service.md) 변경 제안 절), WebUI 화면
- 호환 원칙: 기존 키·열·응답 유지, 추가만. schemaVersion 1·2 구성(현행 train 결과)은 변환·재해석 없이 기존 경로로 실행
- 표기: **[확정]** 사용자 확정 또는 코드 사실 / **[유지 #74]** 10/21 확정값을 모델 단위로 그대로 가져옴 / **[제안]** 이 초안이 정한 값

## 단위 정의 [제안 — 결정 질문 Q-M1]

- **모델 버전** = 함께 운영할 ML 모델 1..N개의 불변 묶음(`methods[]` 전부 `ml:*`). **운영 활성 모델 버전은 0 또는 1개**(`ops_system_state.active_config_version_id`, 단일 행) → "활성 ML 모델 0..N 동시 운영"(10/20 결정, 11/02 Q2 유지) = 활성 버전 없음(룰만 운영) 또는 활성 버전 안의 N개
- 교체·복원·비교 단위 = 모델 버전. "모델끼리 비교"(11/02 Q1)는 모델 버전끼리 비교. 모델 1개만 담은 버전이 가장 흔한 형태(학습 1회 = 후보 버전 1개)
- 대안(채택하지 않음): 모델 1개 = 버전 1개 + 활성 집합 N개. `ops_system_state`·`run_applications`·비교 기준선(활성 1개 전제)·추천 규칙을 모두 다중 활성으로 바꿔야 함

## 방법 식별자 [합의 11/02 — 사용자 확인 필요 (a)]

- 형식 `ml:<model_key>`, key `^[a-z][a-z0-9_-]{1,63}$`. `hard:*`·`soft:*`는 룰 세트 계약, 모델 버전 `methods[]`에 오지 않음(422 `method_kind_not_allowed:<id>`)
- 구성 안에서 식별자 유일: 같은 모델은 버전당 1개(`ml_config_models` PK `(config_version_id, model_key)` 그대로). 같은 모델을 파라미터만 바꿔 2개 넣는 것은 불가
- `requestedMode` 파생 [유지 #74, 의미 축소]: 활성 ML 0개 버전은 저장 불가(아래 검증)이므로 v3 버전의 `requestedMode`는 항상 `ml`(v1·v2 투영에서만 `rule_only`/`ml_only`/`parallel`). 룰 전용 운영 = 활성 모델 버전 없음. **evidence의 `requestedMode`와의 관계**: 모델 버전의 값은 요청(저장) 시점 값이고, evidence v4의 `requestedMode`는 판정마다 **실제 켜진 대분류**(활성 룰 세트의 hard/soft 켜짐 + 활성 모델 버전의 ml)에서 파생(detection-decision.md(#92) 7.3절) — 둘이 다를 수 있으며 evidence 값이 판정 사실
- 호환 매핑: 기존 v3 초안(#74)의 `hr:*`·`rule:*`·`sr:*` 항목은 **DB에 저장된 적 없음**(조합 저장 API 미구현) → 이관 대상 없음. v1·v2 행의 `ml_config_rules`·`statusRulePolicy`는 읽기 투영만(조회 절)

## configuration_spec schemaVersion 3 [제안: 번호 3 유지, 내용은 ML 전용으로 재정의]

- 저장 위치: `ml_config_versions.configuration_spec`. 기존 값 1(ML 후보, `trainer/runner.py:402-407`)·2(rule_only 후보, `:349-352`)와 의미가 다르므로 3부터. #74의 v3(룰 포함)는 구현·저장된 적이 없어 번호를 올리지 않음 — Detector "v3 로더" Issue 이름과 일치. Detector가 4를 원하면 번호만 바꿈(Q-M3)
- `ml_config_models` N행(활성 `ml:*` 1개당 1행). **`ml_config_rules` 행 없음**(v3에서는 항상 0행)

```json
{
  "schemaVersion": 3,
  "contractVersion": "model-version-v1",
  "contentHashVersion": 2,
  "sourceId": "<source UUID>",
  "baseVersionId": "<config UUID|null>",
  "displayName": "IF v1 기본값 — 10월 학습(장애 2구간 제외)",
  "catalogVersion": "method-catalog-v2",
  "featureSetId": "<feature set UUID>",
  "trainingSnapshotId": "<snapshot UUID|null>",
  "methods": [
    {"methodId": "ml:iforest", "kind": "ml", "enabled": true, "methodVersion": "process-iforest-v1",
     "featureSetId": "<feature set UUID>",
     "parameters": {"n_estimators": 128, "max_samples": 512, "contamination": "auto",
                    "random_state": 42, "thresholdPolicy": "train-internal-p99-v1"},
     "parameterSource": "catalog_default",
     "artifacts": {"candidateConfigVersionId": "<train 후보 config UUID>", "trainJobId": "<job UUID>",
                   "modelArtifactId": "<artifact UUID>", "fitArtifactId": "<artifact UUID>"}}
  ],
  "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000},
  "trainingSelection": {"policyVersion": "train-success-excluded-v2", "split": "train", "populationCount": 48210,
                        "selectedCount": 47120, "excludedCount": 1090, "excludedExplicitAnomalyCount": 12,
                        "excludedByIntervalCount": 1040, "excludedTimeUnknownCount": 0,
                        "timeBasis": "esb_occurred_at", "excludedIntervalsHash": "a71c…",
                        "statusCounts": {"S": 47158, "F": 31, "P": 4, "N": 3}, "normalGroundTruth": false},
  "createdFrom": {"kind": "train", "trainJobId": "<job UUID>", "recommendationJobId": null}
}
```

| 키 | 의미·규칙 |
|---|---|
| `schemaVersion` | 정수 3 고정 |
| `contractVersion` | `model-version-v1` |
| `contentHashVersion` | 정수 2 [유지 #74]. 목록·`GET /:id` 응답에 노출. v1·v2 행은 `contentHashVersion: 1`로 투영 |
| `sourceId` | 버전이 속한 source; 모든 참조(후보·피처셋·스냅샷)의 source와 일치 |
| `baseVersionId` | 출발점 버전 또는 null; `ml_config_versions.base_version_id`에 같은 값 |
| `displayName` | 1~120자. 내용 hash에서 **제외** [유지 #74] |
| `catalogVersion` | 저장 시점 카탈로그 버전(`method-catalog-v2`). `executable`은 저장하지 않고 응답 시 재계산 |
| `featureSetId` | 활성 ML 모델이 쓰는 피처셋. 모든 `methods[].featureSetId`와 일치(불일치 409 `feature_set_mismatch`). 소프트룰은 피처셋을 쓰지 않음(카탈로그 내장 필드) |
| `trainingSnapshotId` | 활성 ML이 1개면 그 후보의 학습 스냅샷; 여러 개면 null(각 `ml:*`의 후보가 보유) [유지 #74] |
| `methods[]` | 1개 이상, 전부 `kind: "ml"`. 식별자 유일. 순서 = evidence 방법별 결과 순서(Detector) |
| `methods[].enabled` | boolean. `false` 항목은 spec·hash에 보존, 실행·`ml_config_models` 행에서는 제외 [유지 #74]. 활성 ML 0개 버전은 거부 |
| `methods[].parameters` | 카탈로그 `parameterSchema` 검증. 서버가 학습 후보의 실제 값(`training_settings.parameters`)을 채움 — 요청이 다른 값을 주면 422 `ml_parameters_require_training` [유지 #74] |
| `methods[].parameterSource` | `system` / `catalog_default` / `user` / `llm_proposed`(사람이 저장을 눌렀다는 뜻, 자동 적용 아님) [유지 #74] |
| `methods[].artifacts` | 학습 후보(`kind=train` 성공)의 `ml_config_models` 행. 파일 복사 없음, artifact ID 재사용. 후보 없는 모델은 선택 불가 |
| `detectionPolicy` | **Detector 소유**(detection-decision.md(#92)): v2 객체 `{version: 2, policyKey, k: {hard, soft, ml}, deadlineMs}`. 이 문서는 "모델 버전 spec 안에 저장되고 hash에 포함된다"는 위치만 정함(Q-M2). `k.hard`·`k.soft`는 판정 시점 활성 룰 세트에 적용. **활성 모델 버전이 없을 때(룰만 운영) 기본 정책** = `{"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}`(Detector 11/02 회신). **버전 판정 규칙**: `version` 키가 **없을 때만** v1(`{mode, deadlineMs, combination}`), `version: 2`는 v2 객체, **`version: 1`은 400 `invalid_detection_policy`**(v1·v2 키 혼용 400 규칙과 일관) — #92 U12 결정 대기(확정되면 #74 "없거나 1이면 v1" 문구를 폐기) |
| `trainingSelection` | 활성 ML 1개면 그 후보의 값 복사, 여러 개면 null, 0개 불가. **v2 확장 필드**(`excludedByIntervalCount`·`excludedTimeUnknownCount`·`timeBasis`·`excludedIntervalsHash`)는 [trainer-service.md](trainer-service.md) 변경 제안 절. 제외 구간 목록 자체는 spec이 아니라 학습 작업 `input_spec`·`ml_config_models.training_settings`에 저장(spec은 hash만) |
| `createdFrom` | `{kind: "train"|"compose", trainJobId?, recommendationJobId?}` [유지 #74]. `recommendationJobId`는 모델 선택 추천(보류, [llm-recommendation-job.md](llm-recommendation-job.md))이 재개될 때만 값 |
| `operationalEligibility`·`preparedDatasetId`·`preparedInputContract` | 기존 키 유지([prepared-csv-v1](prepared-csv-import.md)). prepared 계보 후보가 하나라도 있으면 버전 전체 `eligible=false` [유지 #74] |
| (없음) `statusRulePolicy`·`ruleVersionIds` | **v3에 없음**(v1·v2 전용 파생 복사본). 판독기는 v3에서 이 키를 요구하지 않음 |

- 내용 hash(`content_hash`, `contentHashVersion: 2`) [유지 #74, 룰 항목만 제거]: `sourceId`, `featureSetId`, `methods[]`의 `methodId`·`enabled`·`parameters`·`artifacts.modelArtifactId/fitArtifactId`·`featureSetId`, `detectionPolicy`(v2 객체)를 `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`의 **UTF-8 바이트**로 SHA-256 hex(소문자 64자, 접두 없음) — 룰 세트 `contentHash`([rule-set.md](rule-set.md))와 **같은 직렬화**. 현행 v1·v2 hash 코드(`runner.py:408`, 기본 separators·`ensure_ascii=True`)와는 다르며 그 코드는 v1·v2 행 전용으로 남김. `displayName`·`baseVersionId`·`createdFrom`·`catalogVersion`·`trainingSnapshotId`·`trainingSelection`은 제외. 같은 `sourceId`·`contentHashVersion=2` 행끼리만 중복 비교
- 같은 구성 재저장 [유지 #74]: 막지 않고 `200 {id: <기존>, duplicateOf: <기존>, …}`. 중복이 아니면 `201`·`duplicateOf: null`. 중복 저장은 자동 비교 평가를 만들지 않음([config-comparison-evaluation.md](config-comparison-evaluation.md) 자동 비교 절)

## 생성 경로

### (A) 학습 — `POST /api/jobs kind=train` [기존 + 확장]

- 현행: IF 후보 1개 + HR + Rule 0..32 → schemaVersion 1·2 구성(`trainer/runner.py:349-352`, `:402-407`). 요청 형식·권한·멱등키 유지, 기존 요청(`modelKey` 생략·`parameters` 없음)은 IF v1 기본값으로 계속 유효 [유지 #74 Q-H]
- 확장(구현 단계): `modelKey` = 카탈로그 `ml:*`의 `<model_key>`(접두 없이), `executable.process=false`면 422 `method_not_executable`; 선택 `parameters`(카탈로그 schema 검증); **`excludedIntervals[]`·`timeBasis`**([trainer-service.md](trainer-service.md) 변경 제안 절); 성공 결과의 구성은 schemaVersion 3(`methods` = ML 1개, `createdFrom.kind="train"`)
- **v3 train 요청에는 `ruleVersionIds`·`detectionPolicy.mode`가 없음**: v1 형식(`{mode, deadlineMs, combination}`)과 v2 객체(`{version: 2, …}`)를 한 요청에 섞으면 400 `invalid_detection_policy` [유지 #74 N16]. v1 형식 요청은 v1·v2 구성 생성 경로로 계속 처리
- 성공 시 자동 비교 평가 접수(10/21 §4.8, [config-comparison-evaluation.md](config-comparison-evaluation.md) 자동 비교 절)

### (B) 조합 저장 — `POST /api/configurations` [신규, 관리자, 동기 — 유지 #74 Q-D]

```json
{
  "sourceId": "<source UUID>",
  "baseVersionId": "<config UUID|null>",
  "displayName": "IF 10월 + KMeans 실험",
  "featureSetId": "<feature set UUID>",
  "methods": [
    {"methodId": "ml:iforest", "enabled": true, "artifacts": {"candidateConfigVersionId": "<train 후보 config UUID>"}},
    {"methodId": "ml:minibatch_kmeans", "enabled": false, "artifacts": {"candidateConfigVersionId": "<train 후보 config UUID>"}}
  ],
  "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000},
  "createdFrom": {"kind": "compose", "recommendationJobId": null}
}
```

- 경로 이름 `/api/configurations`는 기존 교체·복원 코드(`configRoutes.ts`)와 맞추려 유지. 화면 용어는 "모델 버전"
- 헤더 `Idempotency-Key` 선택; 같은 키·같은 본문 200, 다른 본문 409 `idempotency_payload_conflict`
- 응답 `201 {id, sourceId, schemaVersion: 3, contentHash, contentHashVersion: 2, requestedMode: "ml", executable, createdAt, duplicateOf: null}`; 중복이면 `200` + 기존 버전·`duplicateOf`
- 학습 없음. 서버가 `ml:*`의 `parameters`·`featureSetId`·`methodVersion`·artifact ID·`trainingSelection`을 후보의 `ml_config_models` 행에서 채움
- 검증(순서대로, 첫 실패에서 중단)

| 검사 | 오류 |
|---|---|
| 본문 키 집합·타입·UUID 형식, `methods` 1개 이상 | 400 `invalid_configuration_request` |
| `methodId` kind가 `ml`이 아님 | 422 `method_kind_not_allowed:<id>` |
| `methodId` 카탈로그에 없음 | 422 `method_unknown:<id>` |
| 활성 방법에 `executable.process=false` | 422 `method_not_executable:<id>` — 저장 거부 [유지 #74 Q3-1] |
| 같은 `methodId` 2개 | 422 `method_duplicate:<id>` |
| 활성 방법 0개 | 422 `detection_methods_empty` |
| 후보 없음·다른 source·후보에 그 `model_key` 행 없음·artifact NULL | 422 `ml_candidate_invalid:<id>` |
| 후보들의 `featureSetId`가 서로·본문과 다름 | 409 `feature_set_mismatch` |
| `parameters`를 주었고 후보 실제 값과 다름 | 422 `ml_parameters_require_training:<id>` |
| `parameters`가 카탈로그 `parameterSchema` 위반 | 422 `method_parameters_invalid:<id>` |
| `detectionPolicy` 형태 오류(키 집합·타입·v1/v2 혼용·`version: 1`) | 400 `invalid_detection_policy` |
| `detectionPolicy` v2 객체 의미 검증(정책 키 미지·`k` 범위·활성 대분류 등) | 422 detection-decision.md(#92) 7.4가 정한 코드(예 `combination_k_out_of_range`) |

- 파일 hash 재검증은 적용 단계 `applications.prepare_config`가 수행 [유지 #74]

## 조회

- `GET /api/configurations?sourceId=&limit=<1..100>&cursor=&createdFrom=<train|compose>&modelKey=&executable=<true|false>` [유지 #74]: 최신순 커서 페이지. v1·v2 행은 `createdFrom=train`으로 투영

```json
{"items": [{"id": "<config UUID>", "sourceId": "…", "schemaVersion": 3, "displayName": "…", "contentHash": "…", "contentHashVersion": 2,
            "createdAt": "…", "requestedMode": "ml", "active": true,
            "createdFrom": {"kind": "train", "trainJobId": "<job UUID>", "recommendationJobId": null},
            "executable": true,
            "methods": [{"methodId": "ml:iforest", "kind": "ml", "enabled": true, "status": "supported",
                         "executable": {"process": true, "window": true}, "executableReason": null}],
            "trainingSelection": {"policyVersion": "train-success-excluded-v2", "excludedByIntervalCount": 1040},
            "operationalEligibility": {"eligible": true, "reason": null},
            "applicationEligibility": {"eligible": true, "evaluationId": "<run_evaluations UUID>", "reason": null},
            "latestEvaluation": {"evaluationId": "…", "snapshotId": "…", "purpose": "validation", "metricVersion": "…",
                                 "applicability": "valid", "completedAt": "…"},
            "latestComparison": {"comparisonJobId": "…", "evaluationSetId": "…", "status": "succeeded", "completedAt": "…",
                                 "auto": true, "recommendation": {"targetVersionId": "…", "state": "current"}}}],
 "pagination": {"limit": 50, "hasMore": false, "nextCursor": null}}
```

- 버전 단위 `executable`: 활성 방법 전부 `executable.process=true`일 때만 true, 응답 시점 재계산. false는 저장 뒤 어댑터 소실·deprecated에서만 [유지 #74]
- `applicationEligibility` [유지 #74]: 기술 무결성 gate(`purpose=validation ∧ applicability=valid ∧ scored_count>0 ∧ unavailable_count=0 ∧ prepared 아님`)를 만족하는 가장 최근 적격 평가 1건 + `executable=true` → `{eligible: true, evaluationId}`; 아니면 사유(`no_eligible_evaluation` / `method_results_unavailable` / `operational_ineligible` / `not_executable`)
- v1·v2 행의 읽기 투영 [유지 #74, 축소]: `ml_config_models` → `ml:iforest`; `detectionPolicy.mode` → `requestedMode`. `statusRulePolicy`·`ml_config_rules`는 투영 결과에 `legacyRules: {statusRule: true, ruleVersionIds: [...]}`로만 표시(새 체계 `hard:*`·`soft:*`로 번역하지 않음)
- `latestComparison.auto`: 자동 비교 평가가 만든 작업이면 true [제안]
- `GET /api/configurations/:id`: 기존 `{id, sourceId, configurationSpec, contentHash, createdAt}` 유지 + `schemaVersion`, `displayName`, `requestedMode`, `active`, `methods[]`(파라미터·artifacts 포함), `models[]`(`ml_config_models` 행: `modelKey, featureSetId, modelArtifactId, fitArtifactId, adapterSpec, trainingSettings(excludedIntervals 포함), thresholdSpec`). `rules[]`는 v1·v2 행에만(legacy)
- `GET /api/pipeline/reports/candidates` 그대로 둠 [유지 #74]

## 운영 교체·복원 [유지 #74 — 모델 버전 단위]

- `POST /api/configurations/:id/applications` 요청·응답·멱등키 그대로(`configRoutes.ts:58-170`). `operation` apply/restore, `evaluationId`, `expectedRevision`(`ops_system_state.revision`), `expectedActiveVersionId`
- gate = 기술 무결성만: 해당 버전의 `purpose=validation` 평가 `applicability='valid' AND scored_count>0 AND unavailable_count=0` + prepared 아님 + 버전 `executable=true` + 산출물 경로·hash 검증. 최종 관문은 사람 수락. 자동 교체 금지
- `evaluationId`에 비교 평가가 만든 버전별 `run_evaluations` 행 허용. `acceptedRecommendation: {comparisonJobId, targetVersionId}`(mismatch 400 `accepted_recommendation_mismatch`, stale 400 `accepted_recommendation_stale`) [유지 #74 N22·R10]
- v3 준비 검사(Detector 소유, 요구만): 활성 `ml:*`마다 artifact·fit·feature set 일치(현행 `model_key='iforest'` 1행 고정 `configRoutes.ts:117-126`·`applications.py:13-32`를 N행으로). **`ml_config_rules` 0행이 정상**(v1·v2 경로는 그대로)
- `gate_evidence` `gateVersion: 2`에 `comparisonJobId`·`evaluationSetId`·`acceptedRecommendation`·`recommendationRuleVersion` + **적용 시점 룰 세트 `{revision, contentHash}`** 기록 [제안] — 교체 이력에서 "그때 룰이 무엇이었는지" 추적
- 교체·복원은 **룰 세트에 영향 없음**(룰은 별도 리비전). 모델 버전 복원이 룰을 되돌리지 않음
- **일관 읽기 요구사항**(Detector 11/02 회신): 판정 시도 시작 시 (활성 모델 버전, 룰 세트 `revision`·`contentHash`)를 **한 번에 일관되게** 읽을 수 있어야 함 — 운영 상태 조회(`GET /api/configurations/active` 상당·Detector의 `ops_system_state` 읽기)가 둘을 같은 응답/같은 스냅샷에 함께 담음(예: `{configVersionId, revision, ruleSet: {revision, contentHash}}`). 구현 방식(같은 트랜잭션 SELECT·`ops_system_state`에 룰 리비전 열 추가 등)은 DB·Detector

## 불변성·저장소(사실)

- `ml_config_versions`·`ml_config_models`·`ml_config_rules`·`ml_artifacts`·`run_evaluations`는 **이미 UPDATE/DELETE 금지 트리거가 있음**(`004_feature_pipeline.sql:84-96` `pipeline_immutable` → `prevent_pipeline_immutable_mutation()`). #74 "선택: 불변 트리거 추가"는 **불필요**(PostgreSQL 세션 10/21 발견 사항 반영). 새 마이그레이션은 이 트리거를 유지해야 하며 spec 보정은 새 행으로만
- `ml_config_models.training_settings`에 `parameters`·`methodVersion`·`excludedIntervals[]`·`timeBasis` 키 추가(jsonb, 마이그레이션 없음)
- `run_jobs.input_spec`(train)에 `parameters`·`detectionPolicy`(v2)·`excludedIntervals`·`timeBasis` 키 추가(jsonb)

## schemaVersion 3 판독 규칙 — fail-closed [유지 #74 B1]

- `schemaVersion`을 모르는 판독기는 그 구성을 읽지 않고 `unsupported_config_schema`로 실패. v3를 v1 규칙(IF 1개·mode 분기)으로 조용히 해석 금지
- worker `parse_policy`(`policy.py:30-31`, 키 집합 정확 일치)는 v2 객체를 만나면 예외 → 자동 fail-closed. TS `configRoutes.ts:107`(`mode ?? 'parallel'`)·`inputRoutes.ts:22`는 `schemaVersion`·`detectionPolicy.version` 분기 추가 필요
- 소비처 목록(#74 표 그대로): `detector/computation.py:17-60`, `detector/policy.py:26-37`, `detector/applications.py:35-55`, `trainer/runner.py:340-341·459`, `trainer/service_contract.py:21-33`, `configRoutes.ts:107`, `inputRoutes.ts:22`, `reportRoutes.ts:97-123`
- 허용 시점: v3 생성(train·compose)·적용은 Detector v3 로더(#92 후속 구현)가 병합된 뒤. 그 전에는 422 `config_schema_not_supported_yet`

## 기존 계약 문구 — 변경 제안(이 초안에서 수정하지 않음)

| 문서 | 현재 문구 | 제안 |
|---|---|---|
| `pipeline.md:20` | train 요청 `detectionPolicy:{mode,deadlineMs,combination}` 필수 | v1 형식 요청에 한함; v3 요청은 v2 객체(Detector 계약) + `excludedIntervals` — 판정 문단은 Detector(#92)가 수정 |
| `trainer-service.md` | `models[{key,displayName,supported}]`·`iforest`만·`modelManifest` 단일 | [trainer-service.md](trainer-service.md) 변경 제안 절(#94)에 통합 |
| `synthetic-evaluation.md` | "기존 IF" 전제 | "활성 ML 방법 0..N"으로 일반화(#74 제안 유지) |

## 확정 요약·결정 질문

- 유지(#74 10/21 확정, 모델 단위로 축소): 식별자 형식 / 동기 조합 저장 / 기술 무결성 gate + 비교 + 사람 수락 / 실행 불가 방법 저장 거부 / hash 입력 규칙·`enabled:false` 보존 / 파라미터 변경=재학습 / v1·v2 형식 혼용 400 / 중복 재저장 안내 / `acceptedRecommendation`·stale
- 확정(11/02): 룰 항목 제거, 버전·교체·복원·비교는 모델만, ML 다중 운영 유지

| # | 질문 | 제안 |
|---|---|---|
| Q-M1 | 모델 버전 = ML 1..N 묶음·활성 1개(위 단위 정의) vs 모델 1개 = 버전 1개·활성 집합 N개 | 묶음·활성 1개(기존 테이블·gate·추천 규칙 무변경) |
| Q-M2 | `detectionPolicy`(k:{hard,soft,ml}) 저장 위치 | 모델 버전 spec(hash 포함). **단점**(Detector 회신): `k.hard`·`k.soft`가 모델 버전에 묶여 **룰만 운영 중이어도 정책을 바꾸려면 모델 버전을 새로 만들어야 함**(활성 모델 없으면 기본 정책 고정). **대안**: `k.hard`·`k.soft`·`deadlineMs`는 룰 세트 스냅샷(저장 즉시 적용, hash 포함), `k.ml`만 모델 버전 — 정책이 두 곳에 나뉘어 비교 평가가 접수 시 둘을 합쳐 고정해야 함. 그 밖 대안: 운영 설정 별도 저장(버전·비교에서 정책이 빠짐) |
| Q-M3 | schemaVersion 번호 3 유지 vs 4 | 3 유지(저장된 v3 없음). Detector 선호에 따름 |
| Q-M4 | 대분류 키 `hard/soft/ml`·식별자 접두는 Trainer·Detector 세션 합의(11/02)이며 **사용자 확인 전** | 사용자 확인 요청(#92·rule-set.md Q-R7과 같은 질문) |
