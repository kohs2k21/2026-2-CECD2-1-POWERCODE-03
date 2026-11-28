Created: 2026-11-02T18:00:00+09:00
Updated: 2026-11-28T18:25:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft

# 모델 버전 계약 v1 (초안, Issue #94)

- 변경 요약(#94): 기존 [detection-config-version.md](detection-config-version.md)(#74 `detection-config-v1`, configuration_spec v3 = HR·rule·SR·ML 묶음)에서 **룰 항목을 제거**하고 `ml:*`만 남긴 모델 버전 계약. 룰은 [rule-set.md](rule-set.md)(버전 없음). 버전·교체·복원·비교 평가는 **모델 버전에만** [확정 11/02 Q1·Q2]
- 2차 개정(사용자 11/02 9.4 결정 반영): "운영 모델 **운영에서 빼기**"·"교체 추천은 **같은 종류(modelKey) 모델끼리**" 두 결정의 귀결로 단위 정의를 **모델 1개 = 버전 계열(modelKey별 lineage), 활성 집합 0..N, 같은 modelKey 1개**로 전환하고, `detectionPolicy`를 모델 버전 spec에서 빼 **운영 설정 전역 값**으로 옮김(Detector 11/02 동의). 단위 전환은 **사용자 11/02 확정**(WebUI 경유, 결정 질문 Q-M1)
- 3차 정정(Issue #106, 11/03): detection-decision.md(#92) 16절 정정 요청·후속 권고 반영 — 운영 설정 절 `k`·n 정합을 #92 7.4 검증 순서(U4 "모두 1" 선행)로 정정, 활성 집합 절 공통 검사 (2)를 같은 결론으로 맞춤, 리비전 고정 단위(시도, U5), 11/03 사용자 확정(U1·U12)으로 상태 표기 갱신. 의미 변경은 U4 범위 안
- 4차 정정(Issue #116, 11/04 — #111 병합 반영): 학습 요청 `modelKey`·`parameters` 검증이 **워커**에서 카탈로그 기준으로 이뤄짐(오류 코드 `unsupported_model` / `method_not_executable:<methodId>` / `method_parameters_invalid:<methodId>`, `rule_only`도 같은 검증), `ml_config_models.training_settings`에 `methodId`·`methodVersion`·`parameters` 추가됨(schemaVersion 1·2 틀 유지). prepared 경로는 미반영
- 5차 정정(Issue #132, 11/05 — 워커 v3 산출 구현 반영): 내용 hash 정규 객체·시험 벡터, 학습 요청에 없는 `displayName` 생성 규칙, `training_settings` 키, Trainer 판독·v3 평가·리비전 기록을 사실대로 적음. `configuration_spec` 키와 hash 입력 집합은 그대로이고, `displayName` 생성 규칙과 v3 `kind=evaluate`의 단일 모델 평가는 #132에서 새로 정한 규칙이다
- 6차 정정(11/15, Detector): schemaVersion 3 판독 규칙 절의 허용 시점을 v04 활성 집합 로더 기준으로 고치고 Detector 소비처 현황(v04 1·3만 판독, v03 경로는 schemaVersion 미확인)을 적음. API 422 해제는 WebUI 몫
- 7차 개정(사용자 11/28 결정 — 이상 ML 두 틀): 활성 집합 유일성을 `modelKey`에서 **틀(`frame`, `row`|`trend`)**로 — 틀당 최대 1(합계 최대 2), `replace` = 같은 틀 안 교체, 버전 행·활성 스냅샷에 `frame` 복사(없으면 `row`), 복원이 틀당 1을 어기면 거절, `frame_mismatch`, "탐지 방법 0개" = 행 판정 방법 0개, `n_ml` = 행 틀 모델 수. 아래 "두 틀 — 활성 집합 규칙" 절이 정본이며 **[확정 11/28 — 구현 전]**(코드는 아직 `modelKey` 규칙). 버전 계열(lineage)·`configuration_spec`·내용 hash는 바뀌지 않음
- 계약 버전: `model-version-v1`(#74 `detection-config-v1` 대체). 근거 결정: 사용자 11/02 결정(Q1 운영 = 룰 1벌 + 모델 0..N, Q2 ML 다중 운영 유지, 9.4 운영에서 빼기·같은 종류끼리 추천)·Trainer·Detector 분담 합의 11/02. 근거 코드 origin/dev `7f24e03`
- 범위: 불변 모델 버전 = ML 모델 1개(`modelKey`)의 학습 결과(식별자·파라미터·산출물 참조) + 데이터 계보(스냅샷·학습 선정·학습 기간·제외 구간). 저장마다 새 버전, UPDATE 없음. **활성 집합**(함께 운영하는 모델 버전 0..N)의 상태·조작·이력. **운영 설정**(`detectionPolicy` 전역 값)의 저장·리비전
- 제외: 종합 판정 조합·등급·evidence v4·`detectionPolicy` 필드 의미(detection-decision.md(#92)), 카탈로그 내용([method-catalog.md](method-catalog.md)), 비교 평가([config-comparison-evaluation.md](config-comparison-evaluation.md)), 임계값 추천([llm-recommendation-job.md](llm-recommendation-job.md)), 학습 제외 구간 요청 형식([trainer-service.md](trainer-service.md) 변경 제안 절), WebUI 화면
- 호환 원칙: 기존 키·열·응답 유지, 추가만. schemaVersion 1·2 구성(현행 train 결과)은 변환·재해석 없이 기존 경로로 실행
- 표기: **[확정]** 사용자 확정 또는 코드 사실 / **[유지 #74]** 10/21 확정값을 모델 단위로 그대로 가져옴 / **[제안]** 이 초안이 정한 값 / **[확정 11/02 Q-M1]** 9.4 결정의 귀결로 이 개정이 바꾼 값(사용자 11/02 확정) / **[확정 11/28 — 구현 전]** 사용자 11/28 결정(이상 ML 두 틀)으로 바뀐 규칙. 계약은 확정이고 API·Detector·Trainer 코드는 아직 이전 규칙이며 후속 구현 변경에서 적용

## 단위 정의 [확정 — 사용자 11/02 Q-M1]

- **모델 버전** = ML 모델 **1개**(`modelKey`, 예 `iforest`·`minibatch_kmeans`)의 불변 학습 결과. 같은 `modelKey`의 버전들이 **버전 계열(lineage)**을 이룸 — "IF 10월 → IF 11월"처럼 같은 종류 안에서 교체·복원·비교가 일어남(9.4 "교체 추천은 같은 종류끼리")
- **활성 집합** = 지금 운영에 쓰이는 모델 버전 0..N개(시스템 전역 1개, 룰 세트와 같은 단위). **같은 `modelKey`의 버전은 동시에 최대 1개만 활성**(Detector 11/02 합의) → 활성 집합 안에서 `methodId: ml:<modelKey>`가 유일. 판정의 ML 방법 수 n = 활성 집합 모델 수, 방법 순서 = 활성 집합 순서(활성화 시각 순). → **[확정 11/28 — 구현 전]** 유일성은 **틀당 최대 1(합계 최대 2)**, `n_ml` = **행 틀** 활성 모델 수(아래 "두 틀 — 활성 집합 규칙" 절)
- 운영 조작(아래 활성 집합 절): **활성화**(집합에 추가) / **운영에서 빼기**(집합에서 제거, 9.4) / **교체**(같은 `modelKey`의 다른 버전으로 교환; [확정 11/28 — 구현 전] 같은 **틀**의 다른 버전으로 교환, 알고리즘이 달라도 됨) / **복원**(과거 활성 집합 리비전 상태를 새 리비전으로 재적용). 활성 집합 변경마다 정수 리비전 증가 + 스냅샷 이력(룰 세트와 같은 패턴, 버전 없음)
- "활성 ML 모델 0..N 동시 운영"(10/20 결정, 11/02 Q2 유지) = 활성 집합 크기 0(룰만 운영)..N → [확정 11/28 — 구현 전] 0..2(행 단위 0..1 + 시계열 0..1). 같은 틀 안 다중 운영은 열지 않음
- **채택되지 않은 대안(기록용)**: 모델 버전 = ML 1..N개 묶음, 활성 버전 0 또는 1개(`ops_system_state.active_config_version_id`). 채택하지 않은 이유(9.4 결정과의 충돌): "운영에서 빼기"가 묶음 안 한 모델을 빼려면 새 묶음 버전을 만들어 교체해야 하고(빼기 조작이 교체와 구별되지 않음), "같은 종류끼리 추천"은 묶음 버전끼리의 추천 규칙과 맞지 않음(묶음 B가 묶음 A의 IF만 바꾼 것인지 식별 불가)
- **구현 기준**: 위 확정 단위로 Detector·DB·API·WebUI 구현. 공통 전제: 모델 버전 불변·`ml:*`만·룰 세트와 분리·`detectionPolicy` 전역·일관 읽기 3요소

## 방법 식별자 [확정 11/03 — #92 U1, 11/02 세션 합의 (a) 승인]

- 형식 `ml:<model_key>`, key `^[a-z][a-z0-9_-]{1,63}$`. `hard:*`·`soft:*`는 룰 세트 계약, 모델 버전 `methods[]`에 오지 않음(422 `method_kind_not_allowed:<id>`)
- 모델 버전 1개 = `methods[]` **정확히 1개**(`modelKey` = 그 항목의 `<model_key>`, `ml_config_models` 1행, PK `(config_version_id, model_key)` 그대로) [확정 11/02 Q-M1]. 활성 집합 안에서는 `modelKey` 유일(활성 집합 절)
- `requestedMode` 파생 [유지 #74, 의미 축소]: 활성 ML 0개 버전은 저장 불가(아래 검증)이므로 v3 버전의 `requestedMode`는 항상 `ml`(v1·v2 투영에서만 `rule_only`/`ml_only`/`parallel`). 룰 전용 운영 = 활성 집합 비어 있음. **evidence의 `requestedMode`와의 관계**: 모델 버전의 값은 요청(저장) 시점 값이고, evidence v4의 `requestedMode`는 판정마다 **실제 켜진 대분류**(활성 룰 세트의 hard/soft 켜짐 + 활성 집합의 ml)에서 파생(detection-decision.md(#92) 7.3절) — 둘이 다를 수 있으며 evidence 값이 판정 사실
- 호환 매핑: 기존 v3 초안(#74)의 `hr:*`·`rule:*`·`sr:*` 항목은 **DB에 저장된 적 없음**(조합 저장 API 미구현) → 이관 대상 없음. v1·v2 행의 `ml_config_rules`·`statusRulePolicy`는 읽기 투영만(조회 절)

## configuration_spec schemaVersion 3 [제안: 번호 3 유지, 내용은 ML 전용으로 재정의]

- 저장 위치: `ml_config_versions.configuration_spec`. 기존 값 1(ML 후보, `trainer/runner.py:402-407`)·2(rule_only 후보, `:349-352`)와 의미가 다르므로 3부터. #74의 v3(룰 포함)는 구현·저장된 적이 없어 번호를 올리지 않음 — Detector "v3 로더" Issue 이름과 일치. Detector가 4를 원하면 번호만 바꿈(Q-M3)
- `ml_config_models` **1행**(버전의 `ml:*` 1개). **`ml_config_rules` 행 없음**(v3에서는 항상 0행)
- **형식 선택 [확정 11/02 Q-M1]**: 모델 1개 = 버전 1개가 되어도 `methods[]`(길이 1)를 **유지**하고 필드를 평탄화하지 않음 — 근거: (1) 이미 합의된 v3 저장 형식·판독기 목록(fail-closed 절)·`ml_config_models` 행 대응이 그대로 성립 (2) 내용 hash 입력 규칙(`methods[]`의 `methodId`·`enabled`·`parameters`·artifact ID·`featureSetId`)·`duplicateOf` 비교·불변 트리거가 변경 없이 유지 (3) 묶음이 다시 필요해져도 길이 제한만 풀면 됨. 대신 `methods` 길이 ≠ 1은 422 `method_count_invalid`, 응답에는 파생 `modelKey`(= `methods[0].methodId`의 `<model_key>`)를 노출. 바뀐 것은 **`detectionPolicy` 제거**(운영 설정 절) 하나 — `contentHashVersion: 2`는 저장된 행이 없으므로 번호를 올리지 않고 입력 집합에서 `detectionPolicy`만 뺌

```json
{
  "schemaVersion": 3,
  "contractVersion": "model-version-v1",
  "contentHashVersion": 2,
  "sourceId": "<source UUID>",
  "baseVersionId": "<config UUID|null>",
  "displayName": "Isolation Forest 2026-10-01~2026-11-01",
  "catalogVersion": "method-catalog-v3",
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
  "trainingSelection": {"policyVersion": "train-success-excluded-v2", "split": "train", "populationCount": 48210,
                        "selectedCount": 47120, "excludedCount": 1090, "excludedExplicitAnomalyCount": 12,
                        "excludedIntervalCount": 2, "excludedByIntervalCount": 1040, "excludedTimeUnknownCount": 0,
                        "timeBasis": "esb_occurred_at", "intervalTimeBasis": "esb_occurred_at", "excludedIntervalsHash": "a71c…",
                        "trainingPeriod": {"from": "2026-10-01T00:00:00+09:00", "until": "2026-11-01T00:00:00+09:00", "timeBasis": "esb_occurred_at"},
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
| `displayName` | 1~120자. 내용 hash에서 **제외** [유지 #74]. 학습 요청에 이름 키가 없어 Trainer가 `<카탈로그 displayName> <from 날짜>~<until 날짜>`(한국 시각 날짜, 예 `Isolation Forest 2026-10-01~2026-11-01`)로 만든다 [#132] |
| `catalogVersion` | 저장 시점 카탈로그 버전(11/28부터 `method-catalog-v3`, 그 전 행은 `method-catalog-v2`). `executable`은 저장하지 않고 응답 시 재계산 |
| `featureSetId` | 활성 ML 모델이 쓰는 피처셋. 모든 `methods[].featureSetId`와 일치(불일치 409 `feature_set_mismatch`). 소프트룰은 피처셋을 쓰지 않음(카탈로그 내장 필드) |
| `trainingSnapshotId` | 그 버전(모델 1개)의 학습 스냅샷. 항상 값 [확정 11/02 Q-M1] |
| `methods[]` | **정확히 1개**, `kind: "ml"` [확정 11/02 Q-M1]. evidence 방법별 결과 순서는 활성 집합 순서(Detector) |
| `methods[].enabled` | boolean, v3에서는 **항상 `true`**(키는 형식 호환으로 유지, `false`면 422 `version_methods_empty` — 버전 수준 오류이며 운영 상태 오류 `detection_methods_empty`와 별개). #74의 "`false` 보존" 의미는 묶음 안에서만 성립해 v3에 없음 |
| `methods[].parameters` | 카탈로그 `parameterSchema` 검증. 서버가 학습 후보의 실제 값(`training_settings.parameters`)을 채움 — 요청이 다른 값을 주면 422 `ml_parameters_require_training` [유지 #74] |
| `methods[].parameterSource` | `system` / `catalog_default` / `user` / `llm_proposed`(사람이 저장을 눌렀다는 뜻, 자동 적용 아님) [유지 #74] |
| `methods[].artifacts` | 학습 후보(`kind=train` 성공)의 `ml_config_models` 행. 파일 복사 없음, artifact ID 재사용. 후보 없는 모델은 선택 불가 |
| (없음) `detectionPolicy` | **v3 spec에 없음** [확정 11/02 Q-M1, Q-M2 귀결]: 모델 1개 = 버전 1개가 되면 `k:{hard,soft,ml}`를 어느 버전에도 둘 수 없으므로 **운영 설정 전역 값**으로 이동(아래 운영 설정 절). spec·hash 입력에 `detectionPolicy` 키가 있으면 400 `invalid_configuration_request`. **버전 판정 규칙**(운영 설정·v1 train 요청 공통): `version` 키가 **없을 때만** v1(`{mode, deadlineMs, combination}`), `version: 2`는 v2 객체, **`version: 1`은 400 `invalid_detection_policy`**(v1·v2 키 혼용 400 규칙과 일관) — #92 U12 [확정 11/03] |
| `trainingSelection` | 그 버전의 학습 작업 값 복사(항상 값). **v2 확장 필드**(`excludedIntervalCount`·`excludedByIntervalCount`·`excludedTimeUnknownCount`·`timeBasis`·`intervalTimeBasis`·`excludedIntervalsHash`)와 **`trainingPeriod {from, until, timeBasis}`**(train 요청의 기간 — 수식 추천 기간 선택 "운영 모델 학습 시작 ~ 현재"의 입력, [llm-recommendation-job.md](llm-recommendation-job.md))는 [trainer-service.md](trainer-service.md) 변경 제안 절. 제외 구간 목록 자체는 spec이 아니라 학습 작업 `input_spec`·`ml_config_models.training_settings`에 저장(spec은 hash만, 응답 `models[].trainingSettings.excludedIntervals`로 노출). #132 구현 범위는 `train-success-only-v1` 값 + `trainingPeriod`. 요청에 `excludedIntervals` 키가 있으면 선정 정책 v2가 v2 확장 필드를 채움(키가 없으면 v1 값 그대로) [구현, trainer-service.md 선정 정책 v2 절] |
| `createdFrom` | `{kind: "train"|"compose", trainJobId?, recommendationJobId?}` [유지 #74]. v3에서는 항상 `train`(`compose`는 조합 저장 경로가 없으므로 생성되지 않음 — Q-M5 확정; enum 값은 v1·v2 호환으로 유지). `recommendationJobId`는 모델 선택 추천(보류, [llm-recommendation-job.md](llm-recommendation-job.md))이 재개될 때만 값 |
| `operationalEligibility`·`preparedDatasetId`·`preparedInputContract` | 기존 키 유지([prepared-csv-v1](prepared-csv-import.md)). prepared 계보 후보가 하나라도 있으면 버전 전체 `eligible=false` [유지 #74] |
| (없음) `statusRulePolicy`·`ruleVersionIds` | **v3에 없음**(v1·v2 전용 파생 복사본). 판독기는 v3에서 이 키를 요구하지 않음 |

- 내용 hash(`content_hash`, `contentHashVersion: 2`) [유지 #74, 룰 항목 제거 + 이번 개정에서 `detectionPolicy` 제거]: `sourceId`, `featureSetId`, `methods[]`의 `methodId`·`enabled`·`parameters`·`artifacts.modelArtifactId/fitArtifactId`·`featureSetId`를 `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`의 **UTF-8 바이트**로 SHA-256 hex(소문자 64자, 접두 없음) — 룰 세트 `contentHash`([rule-set.md](rule-set.md), 룰 세트 `contentHashVersion` 1·2 모두 — 룰 세트 v2는 정규 객체에 하드룰 `severity`를 넣는 차이뿐, #116)와 **같은 직렬화**. 현행 v1·v2 hash 코드(`runner.py:408`, 기본 separators·`ensure_ascii=True`)와는 다르며 그 코드는 v1·v2 행 전용으로 남김. `displayName`·`baseVersionId`·`createdFrom`·`catalogVersion`·`trainingSnapshotId`·`trainingSelection`은 제외. 같은 `sourceId`·`contentHashVersion=2` 행끼리만 중복 비교. 번호 2를 유지하는 이유: `contentHashVersion: 2`로 저장된 행이 아직 없음(#74 v3 미구현)
- 내용 hash 정규 객체 [#132 구현, `trainer/model_version.py`]: `{"sourceId", "featureSetId", "methods": [{"methodId", "enabled", "parameters", "featureSetId", "artifacts": {"modelArtifactId", "fitArtifactId"}}]}`. 시험 벡터: `sourceId` `11111111-1111-4111-8111-111111111111`, `featureSetId` `22222222-2222-4222-8222-222222222222`, `modelArtifactId` `33333333-3333-4333-8333-333333333333`, `fitArtifactId` `44444444-4444-4444-8444-444444444444`, 파라미터 = 위 예시의 IF 기본값 → `e1cd04c824db448281f4aead9f2f451444d9d61b15780b25d57cc0848b041310`(`tests/test_model_version.py`에 입력 바이트 전문)
- 같은 구성 재저장 [유지 #74]: 막지 않고 `200 {id: <기존>, duplicateOf: <기존>, …}`. 중복이 아니면 `201`·`duplicateOf: null`. 중복 저장은 자동 비교 평가를 만들지 않음([config-comparison-evaluation.md](config-comparison-evaluation.md) 자동 비교 절)

## 생성 경로

### (A) 학습 — `POST /api/jobs kind=train` [기존 + 확장]

- 현행: IF 후보 1개 + HR + Rule 0..32 → schemaVersion 1·2 구성(`trainer/runner.py:349-352`, `:402-407`). 요청 형식·권한·멱등키 유지, 기존 요청(`modelKey` 생략·`parameters` 없음)은 IF v1 기본값으로 계속 유효 [유지 #74 Q-H] — #111부터 워커가 카탈로그 기본값으로 확정해 `training_settings.parameters`·`methodId`·`methodVersion`에 기록
- 확장(구현 단계): `modelKey` = 카탈로그 `ml:*`의 `<model_key>`(접두 없이), `executable.process=false`면 422 `method_not_executable`; 선택 `parameters`(카탈로그 schema 검증); **`excludedIntervals[]`·`timeBasis`**([trainer-service.md](trainer-service.md) 변경 제안 절); 성공 결과의 구성은 schemaVersion 3(`methods` = ML 1개, `createdFrom.kind="train"`) = **그 자체가 모델 버전**(학습 1회 = 버전 1개, 별도 조합 저장 없음 — 확정 11/02 Q-M1)
- 검증 위치 [#111 구현, #116 반영]: `modelKey`·`parameters` 검증은 **워커**(`trainer/runner.py` → `methods.registry.resolve_training_method`)가 큐에 담긴 `input_spec`을 카탈로그 기준으로 수행 — 카탈로그에 없는 `modelKey` → `unsupported_model`, 등록됐지만 `executable.process=false` → `method_not_executable:<methodId>`, `parameters`가 `parameterSchema` 위반 → `method_parameters_invalid:<methodId>`(작업 `failed`의 `errorCode`; 형식은 `코드` 또는 `코드:식별자`). `modelKey`·`parameters` 키 부재(v1·v2 요청)는 IF 기본값. **`rule_only` 요청도 같은 검증을 먼저 거치므로 잘못된 `parameters`가 있으면 `method_parameters_invalid:<methodId>`로 거절**(`model_not_selected` 검사보다 앞). API의 접수 단계 검증(카탈로그 기준 422·`parameters` 전달)은 #128 구현 — [trainer-service.md](trainer-service.md) 요청·발견 API 절
- **임시 예외(#128) 종료 [구현, 학습 제외 구간 개방]**: v3 생성 전에는 `modelKey`·`parameters`와 v1 `detectionPolicy`를 함께 보내는 요청을 접수해 schemaVersion 1 구성을 만들었다. 이제 API(`jobRoutes.ts`)가 아래 규칙을 그대로 적용하고 화면은 v3 요청만 보낸다([models-ui.md](models-ui.md) 요청 본문). API의 v3 키는 `modelKey`·`parameters`·`excludedIntervals`·`timeBasis`이고(`evaluationSetId`는 아직 접수하지 않아 400 `invalid_job_request`), v3 요청의 `ruleVersionIds` 키는 400 `invalid_job_request`. v3 요청의 `input_spec`에는 `modelKey`를 항상 싣는다(생략이면 `iforest` — `timeBasis=persisted_at`·`parameters: null`은 남기지 않으므로 워커 판별 키를 남김)
- **v3 train 요청에는 `ruleVersionIds`·`detectionPolicy`가 없음** [확정 11/02 Q-M1]: 정책은 운영 설정 전역 값이므로 v3 키(`modelKey`·`parameters`·`excludedIntervals`·`timeBasis`·`evaluationSetId` 중 하나라도)가 있는 요청에 `detectionPolicy` 키가 있으면 400 `invalid_detection_policy`. 단 현행 API는 `evaluationSetId`를 아직 받지 않아, 이 키가 있는 train 요청은 허용 키 검사에 먼저 걸려 `detectionPolicy` 유무와 관계없이 400 `invalid_job_request`다(`jobRoutes.ts`). v1 형식 요청(`{mode, deadlineMs, combination}` 필수, v3 키 없음)은 v1·v2 구성 생성 경로로 계속 처리; v1 형식과 v2 객체 혼용도 400 [유지 #74 N16]
- 성공 시 자동 비교 평가 접수(10/21 §4.8, [config-comparison-evaluation.md](config-comparison-evaluation.md) 자동 비교 절) — 비교 상대는 **같은 `modelKey`의 활성 버전** → [확정 11/28 — 구현 전] **같은 틀의 활성 버전**
- 틀 [확정 11/28 — 구현 전]: 학습 요청 본문에 틀 키를 두지 않는다. 틀은 `modelKey` → `ml:<modelKey>` → 카탈로그 `frame`으로 정해지고, 학습 결과 `ml_config_models.training_settings.frame`에 기록한다(아래 "두 틀 — 활성 집합 규칙" 절). 허용 키 검사는 그대로라 틀 키를 보내면 400 `invalid_job_request`
- 워커 판별 [#132 구현]: `detectionPolicy` 키가 없고 v3 키가 하나라도 있으면 schemaVersion 3을 만든다. 둘 다 있거나 v3 키가 없으면 schemaVersion 1·2 경로이고 리비전을 읽지 않는다. v3 요청의 `ruleVersionIds` 값은 `invalid_job_input`. 아직 적용하지 못하는 `timeBasis=esb_occurred_at`(모든 경로)과 schemaVersion 1·2 경로의 `excludedIntervals`는 워커도 `config_schema_not_supported_yet`으로 끝낸다(무시해서 성공시키지 않음). schemaVersion 3 경로의 `excludedIntervals`는 선정 정책 v2로 적용한다([trainer-service.md](trainer-service.md) 선정 정책 v2 절). `parameterSource`는 요청 `parameters`가 비어 있지 않으면 `user`, 그 밖(키 없음·`null`·빈 객체)은 `catalog_default`. prepared 학습은 schemaVersion 1 그대로

### (B) 조합 저장 — `POST /api/configurations` [**폐기** — 확정 11/02 Q-M1·Q-M5: 조합 저장 경로 없음]

- 모델 1개 = 버전 1개에서는 학습 결과가 곧 버전이므로 "후보를 골라 묶는" 저장이 없음(같은 후보로 다시 저장하면 내용 hash가 같아 항상 `duplicateOf`). **구현하지 않으며** 아래 요청·검증 표는 채택되지 않은 대안(묶음)의 기록용으로만 남김(`detectionPolicy` 제거 반영본). 기존 교체·복원 경로 이름 `/api/configurations/:id/applications`는 활성 집합 절에서 계속 사용

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
| 본문 키 집합·타입·UUID 형식, `methods` 1개 이상, `detectionPolicy` 키 존재 | 400 `invalid_configuration_request` |
| `methodId` kind가 `ml`이 아님 | 422 `method_kind_not_allowed:<id>` |
| `methodId` 카탈로그에 없음 | 422 `method_unknown:<id>` |
| 활성 방법에 `executable.process=false` | 422 `method_not_executable:<id>` — 저장 거부 [유지 #74 Q3-1] |
| 같은 `methodId` 2개 | 422 `method_duplicate:<id>` |
| 활성 방법 0개 | 422 `version_methods_empty`(버전 수준; 운영 상태의 `detection_methods_empty`와 구분) |
| 후보 없음·다른 source·후보에 그 `model_key` 행 없음·artifact NULL | 422 `ml_candidate_invalid:<id>` |
| 후보들의 `featureSetId`가 서로·본문과 다름 | 409 `feature_set_mismatch` |
| `parameters`를 주었고 후보 실제 값과 다름 | 422 `ml_parameters_require_training:<id>` |
| `parameters`가 카탈로그 `parameterSchema` 위반 | 422 `method_parameters_invalid:<id>` |

- 파일 hash 재검증은 적용 단계 `applications.prepare_config`가 수행 [유지 #74]

## 조회

- `GET /api/configurations?sourceId=&limit=<1..100>&cursor=&createdFrom=<train|compose>&modelKey=&executable=<true|false>&active=<true|false>` [유지 #74, `active`는 #128 추가]: 최신순 커서 페이지. v1·v2 행은 `createdFrom=train`으로 투영

```json
{"items": [{"id": "<config UUID>", "sourceId": "…", "schemaVersion": 3, "displayName": "…", "contentHash": "…", "contentHashVersion": 2,
            "createdAt": "…", "requestedMode": "ml", "modelKey": "iforest",
            "active": true, "activeSince": {"revision": 7, "at": "2026-11-01T02:00:00Z"},
            "createdFrom": {"kind": "train", "trainJobId": "<job UUID>", "recommendationJobId": null},
            "executable": true,
            "methods": [{"methodId": "ml:iforest", "kind": "ml", "enabled": true, "status": "supported",
                         "executable": {"process": true, "window": true}, "executableReason": null}],
            "trainingSelection": {"policyVersion": "train-success-excluded-v2", "excludedByIntervalCount": 1040,
                                  "trainingPeriod": {"from": "2026-10-01T00:00:00+09:00", "until": "2026-11-01T00:00:00+09:00", "timeBasis": "esb_occurred_at"},
                                  "excludedIntervalCount": 2},
            "modelSummary": {"featureCount": 10, "parameters": {"n_estimators": 128, "max_samples": 512, "random_state": 42}, "thresholdValue": "0.58"},
            "operationalEligibility": {"eligible": true, "reason": null},
            "applicationEligibility": {"eligible": true, "evaluationId": "<run_evaluations UUID>", "reason": null},
            "latestEvaluation": {"evaluationId": "…", "snapshotId": "…", "purpose": "validation", "metricVersion": "…",
                                 "applicability": "valid", "completedAt": "…"},
            "latestComparison": {"comparisonJobId": "…", "evaluationSetId": "…", "status": "succeeded", "completedAt": "…",
                                 "auto": true, "recommendation": {"modelKey": "iforest", "targetVersionId": "…", "state": "current"}}}],
 "pagination": {"limit": 50, "hasMore": false, "nextCursor": null}}
```

- `modelKey`(파생, `methods[0].methodId`의 `<model_key>`)·`active`(현재 활성 집합에 포함 여부)·`activeSince`(포함됐을 때 활성 집합 리비전·시각, 아니면 null) [확정 11/02 Q-M1]. `?modelKey=` 필터로 버전 계열(lineage)만 조회 — 화면 "IF 계열 버전 목록"
- `active` 필터 [#128]: `true`면 `active: true`인 버전만, `false`면 그 밖의 버전만. 화면 탭 '현재 운영 버전'이 쓴다. 오류 400 `invalid_active`
- `modelSummary` [#128, 추가 필드]: 목록 열 입력 변수·학습 파라미터·판정선 요약. `featureCount`(`training_settings.featureNames` 수), `parameters`(v3는 `methods[0].parameters`, v1·v2는 `training_settings.parameters`), `thresholdValue`(`threshold_spec.value`). 저장 기록이 없는 값은 null(#111 이전 v1·v2 행의 `parameters` 등), 모델 없는 구성은 `modelSummary` 자체가 null. 값을 계산·추정하지 않음
- `trainingSelection.trainingPeriod`·`excludedIntervalCount`를 목록에도 노출(수식 추천 기간 선택·학습 흐름 화면용, WebUI 11/02 대조 결과). 제외 구간 목록 자체는 `GET /:id`의 `models[].trainingSettings.excludedIntervals`
- `latestComparison.recommendation`은 **이 버전의 `modelKey`에 대한** 추천([확정 11/28 — 구현 전] 이 버전의 **틀**에 대한 추천)([config-comparison-evaluation.md](config-comparison-evaluation.md) `replacementRecommendations[]` 중 같은 `modelKey` 항목)
- 버전 단위 `executable`: 활성 방법 전부 `executable.process=true`일 때만 true, 응답 시점 재계산. false는 저장 뒤 어댑터 소실·deprecated에서만 [유지 #74]
- `applicationEligibility` [유지 #74]: 기술 무결성 gate(`purpose=validation ∧ applicability=valid ∧ scored_count>0 ∧ unavailable_count=0 ∧ prepared 아님`)를 만족하는 가장 최근 적격 평가 1건 + `executable=true` → `{eligible: true, evaluationId}`; 아니면 사유(`config_schema_not_supported_yet` / `no_eligible_evaluation` / `method_results_unavailable` / `operational_ineligible` / `not_executable`). `config_schema_not_supported_yet` = 구성 schemaVersion이 1·2·3 밖(키 없음·`null` 포함, 활성 집합 gate와 같은 범위)이며 가장 먼저 검사한다. 그다음 `operational_ineligible` → `not_executable` → 평가 사유 순
- v1·v2 행의 읽기 투영 [유지 #74, 축소]: `ml_config_models` → `ml:iforest`; `detectionPolicy.mode` → `requestedMode`. `statusRulePolicy`·`ml_config_rules`는 투영 결과에 `legacyRules: {statusRule: true, ruleVersionIds: [...]}`로만 표시(새 체계 `hard:*`·`soft:*`로 번역하지 않음). **`trainingSelection.trainingPeriod` 투영**: 그 행을 만든 train 작업 `run_jobs.input_spec`의 `from`·`until`이 있으면 `{from, until, timeBasis: "persisted_at"}`(v1·v2는 `persisted_at` 기준), 없으면(snapshot 직접 지정 등) **null** — 수식 추천 (a) 모드가 v1·v2 활성 행에서 이 값을 읽음([llm-recommendation-job.md](llm-recommendation-job.md)); `excludedIntervalCount`는 항상 0
- `latestComparison.auto`: 자동 비교 평가가 만든 작업이면 true [제안]
- `GET /api/configurations/:id`: 기존 `{id, sourceId, configurationSpec, contentHash, createdAt}` 유지 + `schemaVersion`, `displayName`, `requestedMode`, `active`, `methods[]`(파라미터·artifacts 포함), `models[]`(`ml_config_models` 행: `modelKey, featureSetId, modelArtifactId, fitArtifactId, adapterSpec, trainingSettings(excludedIntervals 포함), thresholdSpec`). `rules[]`는 v1·v2 행에만(legacy)
- `GET /api/pipeline/reports/candidates` 그대로 둠 [유지 #74]
- 구현(#128): 목록·상세는 `backend/src/services/configurationReadModel.ts`. 구현에서 정한 값(오류 코드, 활성 판단의 v1·v2 호환, `latestComparison` null)은 [models-ui.md](models-ui.md)

## 활성 집합 — 활성화·운영에서 빼기·교체·복원 [확정 11/02 Q-M1, 9.4 결정 귀결]

- **활성 집합 상태**(시스템 전역 1개): `{revision, models: [{configVersionId, modelKey, displayName, activatedAtRevision}], changedAt, changedBy}`([확정 11/28 — 구현 전] 항목에 `frame` 추가 — "두 틀 — 활성 집합 규칙" 절). `revision`은 정수 단조 증가(r0 = 비어 있음, 마이그레이션 시드 불필요), 변경마다 **집합 전체 스냅샷**을 이력에 저장(룰 세트 [rule-set.md](rule-set.md)와 같은 패턴 — 버전 없음, append-only). 현행 `ops_system_state.revision`·`active_config_version_id`(단일)는 v1·v2 운영용으로 남기고 활성 집합은 새 저장소(아래 저장소 절, 이름은 DB 세션)
- **제약**: 같은 `modelKey`의 버전은 동시에 최대 1개(Detector 11/02 합의). 위반 저장은 422 `active_model_key_duplicate:<modelKey>`. → [확정 11/28 — 구현 전] **틀당 최대 1개**, 위반 422 `active_frame_occupied:<frame>`("두 틀 — 활성 집합 규칙" 절). 집합 순서 = 활성화 순서(`activatedAtRevision` 오름차순), evidence `methodResults[]`의 ML 순서와 같음
- **조작** — `POST /api/configurations/:id/applications`(경로·멱등키·응답 형식은 `configRoutes.ts`의 `POST /:id/applications` 처리 유지, `operation` 값 확장)

| `operation` | 의미 | 본문 | 검증·gate |
|---|---|---|---|
| `apply` | **활성화**: `:id`를 집합에 추가([확정 11/28 — 구현 전] 빈 틀 채우기만) | `{evaluationId, expectedRevision, acceptedRecommendation?}` | 기술 무결성 gate(아래) + 같은 `modelKey` 활성 버전이 **없어야** 함(있으면 422 `active_model_key_duplicate` — 교체는 `replace`) + 정책 정합(1차 구현은 별도 k 검사 없음 — 방법 수를 늘리는 조작이라 `detection_methods_empty`도 걸리지 않음, 공통 검사 (2)) |
| `replace` | **교체**: 같은 `modelKey`의 현재 활성 버전을 `:id`로 교환(같은 순서 자리)([확정 11/28 — 구현 전] 같은 **틀**의 현재 활성 버전과 교환, 알고리즘이 달라도 됨) | `{evaluationId, expectedRevision, expectedActiveVersionId, acceptedRecommendation?}` | gate + `expectedActiveVersionId`가 같은 `modelKey`의 현재 활성 버전이어야 함(아니면 409 `active_version_mismatch`; 그 `modelKey`가 활성에 없으면 422 `active_model_key_missing`) |
| `retire` | **운영에서 빼기**: `:id`를 집합에서 제거(9.4) | `{expectedRevision, confirmRetire: true, note?}` | gate 없음(평가 불필요). `confirmRetire≠true` → 422 `retire_confirmation_required`. `:id`가 활성이 아니면 422 `not_active`. 탐지 방법 0개·정책 정합(1차는 `detection_methods_empty`만, 별도 k 검사 없음, 공통 검사 (2))은 아래 공통 검사 |
| `restore` | **복원**: 과거 활성 집합 리비전 `targetRevision`의 집합을 **새 리비전으로 재적용** | `{expectedRevision, targetRevision, confirmRetire?}` (`:id`는 무시, 경로는 `POST /api/active-models/restore`도 허용) | 스냅샷의 모든 버전에 gate 재검사(`executable`·산출물 hash — 평가 행은 그 버전의 최근 적격 평가 재사용) + 현재 집합에서 빠지는 버전이 있으면 `confirmRetire: true` 필수. 룰 세트 되돌리기와 같이 모든 이력 항목에 [이 상태로 복원] 항상 표시 |

- 공통 검사(모든 `operation`, 결과 상태 기준) [확정 — Detector 11/02 회신 Q-M7·Q-M8]: (1) **`detection_methods_empty`** — 변경 **결과** 상태가 켜진 하드룰 0 + 켜진 소프트룰 0 + 활성 모델 0([확정 11/28 — 구현 전] **행 틀** 활성 모델 0 — 시계열 모델만 있고 룰 0이어도 거절)이면 422(룰 세트 저장([rule-set.md](rule-set.md))·`retire`·`replace`·`restore` 등 **그 상태를 만드는 모든 변경**에 같은 정의·같은 코드; 코드 이름은 #92 U10) (2) **정책 정합** [확정 11/03 #92 U4 — 7.4 "검증 위치"]: 정책 저장이 `k.hard`·`k.soft`·`k.ml`을 n과 무관하게 **모두 1**로 강제하므로(운영 설정 절) 활성 집합 변경으로는 `k.ml > n_ml`이 생길 수 없음(`n_ml ≥ 1`이면 `k.ml = 1 ≤ n_ml`, `n_ml = 0`이면 ml 대분류 비활성). 따라서 1차 구현에서 `apply`·`replace`·`retire`·`restore`(룰 세트 저장도 같음)는 (1) `detection_methods_empty`**만** 검사하고 별도 k 검사를 두지 않음. 나중에 U4를 완화해 k ≥ 2를 허용할 때만(사용자 재결정) 변경 결과 `n_ml`이 `k.ml` 미만이면 거절(**`n_ml = 0`은 예외** — ml 대분류 비활성으로 취급해 허용)하는 대칭 검사를 추가하며 오류 코드는 detection-decision.md(#92) 7.4의 "k > n" 코드. 실행 시(판정 시도 시점) 위반이 남아 있으면(동시 변경 경합) Detector가 **fail-closed**(판정 행 없이 시도 실패)
- 공통: `expectedRevision` ≠ 현재 활성 집합 리비전 → 409 `active_set_revision_conflict` + `{currentRevision, changedAt, changedBy}`. 성공 응답 `{revision, previousRevision, models[], changes: [{type: activated|replaced|retired|restored, configVersionId, modelKey, previousConfigVersionId?}], appliesFrom: "next_decision"}`. 실패 시 집합 불변(트랜잭션 1개)
- gate = 기술 무결성만 [유지 #74]: 해당 버전의 `purpose=validation` 평가 `applicability='valid' AND scored_count>0 AND unavailable_count=0` + prepared 아님 + 버전 `executable=true` + 산출물 경로·hash 검증. 최종 관문은 사람 수락. 자동 교체·자동 활성화·자동 빼기 금지
- `evaluationId`에 비교 평가가 만든 버전별 `run_evaluations` 행 허용. `acceptedRecommendation: {comparisonJobId, modelKey, targetVersionId}`(mismatch 400 `accepted_recommendation_mismatch`, stale 400 `accepted_recommendation_stale` — **stale 기준은 같은 `modelKey`의 활성 변경**: 그 `modelKey`의 현재 활성 버전 ≠ 추천의 `baselineConfigVersionId`(빼기로 활성 없음 포함) 또는 평가 세트가 `ready`가 아님; 다른 `modelKey`의 활성화·빼기만으로는 stale이 아님([확정 11/28 — 구현 전] `modelKey` 대신 **틀** — 같은 틀의 활성 변경만 stale) — Detector 11/02 Q-E4 확정, [config-comparison-evaluation.md](config-comparison-evaluation.md) stale 절) [유지 #74 N22·R10, 기준만 전환]
- v3 준비 검사(Detector 소유, 요구만): 활성 집합의 `ml:*`마다 artifact·fit·feature set 일치(현행 `model_key='iforest'` 1행 고정 `configRoutes.ts` 단일 구성 적용의 모델 산출물 검사·`detector/applications.py`의 단일 구성 적용 검사를 집합 N행으로). **`ml_config_rules` 0행이 정상**(v1·v2 경로는 그대로)
- `gate_evidence` `gateVersion: 2`에 `comparisonJobId`·`evaluationSetId`·`acceptedRecommendation`·`recommendationRuleVersion` + **조작 시점 룰 세트 `{revision, contentHash, contentHashVersion}`(행 값 그대로)·정책 `detectionPolicyRevision`·활성 집합 `{before: revision, after: revision}`** 기록 [제안] — 이력에서 "그때 룰·정책·집합이 무엇이었는지" 추적
- 활성 집합 변경은 **룰 세트·정책에 영향 없음**(각각 별도 리비전). 복원이 룰·정책을 되돌리지 않음
- v1·v2 단일 구성 경로(본문이 `apply`·`restore` + `expectedActiveVersionId` 키, `ops_system_state`·`run_applications`) [구현]: schemaVersion 1·2만 받고 그 밖(v3·키 없음 포함)은 422 `config_schema_not_supported_yet`으로 받지 않는다. 입력 구성 고정(`POST /api/inputs/:sourceId/mode`의 `configVersionId` → `ops_input_controls.config_version_id`)도 같은 규칙이다(`null` = 운영 구성 선택은 그대로, 공용 검사 `backend/src/services/legacyConfig.ts`). v3가 `ops_system_state.active_config_version_id`에 들어가면 v03 판독(`load_context`·`parse_policy`)이 정책·룰 없는 구성을 legacy 기본값(parallel, 룰 없음)으로 조용히 읽기 때문이다. 고정된 입력 작업은 v04 모드에서도 v03 경로로 가므로 같은 문제가 생긴다. 따라서 API 경로(legacy `POST /api/configurations/:id/applications`, `POST /api/inputs/:sourceId/mode`)로는 v3를 단일 구성 적용·복원이나 입력 구성 고정에 쓸 수 없고, v3는 활성 집합 조작으로 운영한다. v1·v2 적용·복원·고정은 그대로. 기존 고정을 유지한 `mode: paused` 요청은 검사하지 않는다(이미 v3가 고정된 원천도 일시정지 가능, 새로 지정하는 구성과 재개는 검사). `GET /api/inputs`의 `configurations` 후보 목록은 schemaVersion 1·2만 싣는다(화면 고정 목록에 v3가 나오지 않음). 운영자 CLI `input select --config`(`collector/input_store.py` `switch`)도 같은 검사를 한다(1·2만, 기존 고정을 유지한 paused·`active` 제외)
- 이력 — `GET /api/active-models/history?limit=&cursor=`(로그인 사용자): `{items: [{revision, changedAt, changedBy, note, restoredFromRevision, changes[], summary: {activated, replaced, retired}}], pagination}`; `GET /api/active-models/history/:revision`은 + `snapshot.models[]`. append-only
- **일관 읽기 요구사항**(Detector 11/02 회신·합의): 판정 시도 시작 시 **(룰 세트 `revision`·`contentHash`·`contentHashVersion`, 활성 집합 `revision`(+ 모델 목록), 정책 `revision`(+ 정책 값)) 세 가지**를 **한 번에 일관되게** 읽을 수 있어야 함 — 운영 상태 조회 **응답 한 건**이 셋을 함께 담음. **고정 단위는 시도(attempt)** [확정 11/03 #92 U5]: 세 리비전은 시도마다 그 시도 시작 시점에 한 번에 새로 읽음(재시도는 이전 시도의 리비전을 이어 쓰지 않음; 작업 생성·큐 payload에 고정하는 작업 단위 안은 채택되지 않음 — #92 10절)

```json
{
  "ruleSet": {"revision": 13, "contentHash": "9c40…", "contentHashVersion": 2},
  "activeModelSet": {"revision": 7, "models": [{"configVersionId": "<config UUID>", "modelKey": "iforest", "activatedAtRevision": 5}]},
  "detectionPolicy": {"revision": 2, "policy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}},
  "readAt": "2026-11-02T10:00:00Z"
}
```

- 경로 `GET /api/operational-state`(가칭, 로그인 사용자; Detector는 같은 내용을 DB에서 같은 스냅샷으로 읽음). 구현 방식(같은 트랜잭션 SELECT·`ops_system_state`에 세 리비전 열 추가 등)은 DB·Detector. Detector는 evidence v4에 `ruleSetRevision`·`ruleSetHash`(행의 `contentHash` 그대로 복사 — hash 버전은 `ruleSetRevision`으로 식별, [rule-set.md](rule-set.md) 리비전 형식 절 #116)·**`activeModelSetRevision`(정수)·`detectionPolicyRevision`(정수)**를 기록하고 정책 값은 evidence `detectionPolicy`에 그대로 복사(`detectionPolicySource` 같은 출처 필드는 **없음** — Detector 11/02 합의)

### 두 틀 — 활성 집합 규칙 [확정 11/28 — 구현 전]

- 근거: 사용자 11/28 결정(이상 ML 두 틀 — 틀 고정, 틀 안에서 알고리즘만 교체). 틀 정의와 방법별 `frame`은 [method-catalog.md](method-catalog.md) "틀·결과 코드·틀별 추천" 절. 이 절은 위 활성 집합 절의 `modelKey` 기준 유일성·`apply`/`replace` 의미·`detection_methods_empty`·stale 기준을 **대체**한다. 구현 전까지 API·Detector·Trainer 코드는 위 `modelKey` 규칙대로 동작한다(지금 활성화할 수 있는 모델은 `ml:iforest`(`row`)뿐이라 운영 결과는 같다)
- 틀 값의 자리
  - 정본: 카탈로그 방법 항목 `frame`(`methodId`마다 불변)
  - 버전 행: 학습(Trainer)이 `ml_config_models.training_settings.frame`에 그 `methodId`의 카탈로그 `frame`을 기록한다. 불변 표라 학습 때만 쓴다(jsonb, migration 없음)
  - 활성 스냅샷: 활성화(`apply`·`replace`·`restore`)가 항목에 `frame`을 복사한다 — `models: [{configVersionId, modelKey, frame, displayName, activatedAtRevision}]`(jsonb, migration 없음). 운영 상태 조회 `activeModelSet.models[]`도 같은 값을 싣는다
  - `frame`이 없는 과거 값(스냅샷 항목·`training_settings` — v1·v2 행과 11/28 전 v3 행)은 **`row`로 읽는다**(그때 활성화될 수 있던 실행 가능 모델은 `process_row`인 IF뿐)
- 유일성: 활성 집합은 **틀당 최대 1개(합계 최대 2개)**. 버전 계열(lineage)은 그대로 `modelKey`별. 같은 틀 안 다중 운영(틀당 2개 이상)은 열지 않는다(열려면 ML 슬롯 수 변경이 먼저이고 사용자 결정 필요)
- 조작
  - `apply` = **빈 틀 채우기만**. 그 틀에 활성 버전이 있으면 422 `active_frame_occupied:<frame>`(교체는 `replace`). 같은 `modelKey`는 같은 틀이므로 `active_model_key_duplicate`의 경우도 이 코드로 낸다
  - `replace` = **같은 틀 안 교체**(알고리즘이 달라도 됨, 예 IF 버전 → LOF 버전, 같은 순서 자리). `expectedActiveVersionId`는 `:id`와 같은 틀의 현재 활성 버전이어야 한다(아니면 409 `active_version_mismatch`). 그 틀이 비어 있으면 422 `active_frame_empty:<frame>`(`active_model_key_missing` 자리)
  - `retire`: 그대로(틀과 무관)
  - `restore`: 결과 상태가 틀당 1개를 어기면(예: `frame` 없는 과거 스냅샷에 IF와 K-Means가 함께 있어 둘 다 `row`) **422 `active_frame_occupied:<frame>`로 거절하고 자동으로 줄이지 않는다**(저장 직전 재검사와 같은 자리)
- 저장 시 `frame` 일치 검사(`apply`·`replace`·`restore`, 항목마다): 카탈로그 `frame` ≠ 버전 행 `training_settings.frame`(없으면 `row`)이거나, `restore`의 스냅샷 항목 `frame`(없으면 `row`) ≠ 카탈로그 `frame`이면 422 `frame_mismatch:<methodId>`. 순서는 기술 무결성 gate → `frame_mismatch` → 틀 점유(`active_frame_occupied`·`active_frame_empty`) → 공통 검사. 다른 틀로 옮기는 자동 보정은 없다. 카탈로그에 없는 `methodId`는 지금 규칙 그대로
- 고정 시 불일치(Detector 운영 상태 고정 — 판정 시도와 비교 평가 종합이 같은 함수): 항목마다 스냅샷 `frame`(없으면 `row`)과 Detector 이미지의 카탈로그 `frame`을 비교해 **불일치 항목이 속한 틀만 fail-closed**, 다른 틀은 계속. "속한 틀"은 **스냅샷 `frame`(없으면 `row`) 기준**이다(카탈로그 값이 아님 — 운영자가 저장한 자리를 멈춘다)
  - `row` 쪽 불일치: 판정 행 없이 시도 실패(원인 `frame_mismatch`) — **룰 판정도 멈춘다**. 불일치 항목을 빼고 돌리면 그 시도의 행 방법 집합·`n_ml`이 저장된 활성 집합과 달라져 "고정한 리비전 그대로 판정"이 깨지기 때문이다. 카탈로그에 없는 방법은 다르다 — 그 방법만 `not_executable` 미판정이고 집합은 그대로라 시도를 계속한다
  - `trend` 쪽 불일치: 시계열 틀만 멈추고 행 판정은 계속한다. 기록 형식은 시계열 판정 계약(후속)
- 정의 변경(detection-decision.md 6절·7.4와 같은 정의)
  - **"탐지 방법 0개"(`detection_methods_empty`) = 행 판정 방법 0개**: 켜진 하드룰 + 켜진 소프트룰 + **행 틀 활성 모델** = 0. 시계열 모델만 있고 켜진 룰이 0개인 결과 상태도 422 `detection_methods_empty`(룰 세트 저장·`retire`·`replace`·`restore` — 공통 검사 (1)과 같은 자리)
  - **`n_ml` = 행 틀 활성 모델 수**(0 또는 1). PROCESS 판정의 ML 방법은 행 틀 모델뿐이고 시계열 틀 모델은 PROCESS 판정 방법이 아니다. 공통 검사 (2) 정책 정합의 `n_ml`도 이 값
- 비교 평가·교체 추천은 틀 단위([config-comparison-evaluation.md](config-comparison-evaluation.md) "두 틀 단위 비교" 절): `acceptedRecommendation` stale 기준 = **같은 틀의 활성 변경**(그 틀의 현재 활성 버전 ≠ 추천 기준선, 빼기로 비게 된 경우 포함), 다른 틀의 활성화·빼기만으로는 stale 아님. 자동 비교 상대 = 같은 틀의 활성 버전
- 시계열 버전: 지금 시계열 틀에는 실행 가능한 방법이 없어 시계열 버전은 기존 gate의 `not_executable`로 활성화되지 않는다. 시계열 버전의 적용 관문·실행 가능 표시(`executable.trend`)는 후속 계약

## 운영 설정 — `detectionPolicy` 전역 값 [확정 11/02 Q-M1, Q-M2 귀결 — Detector 11/02 동의]

- 위치: 모델 버전 spec이 아니라 **운영 설정**(시스템 전역 1개). 값 형식은 Detector 소유 v2 객체 `{version: 2, policyKey, k: {hard, soft, ml}, deadlineMs}`(detection-decision.md(#92)); 이 문서는 저장·리비전·조회만 정함
- **리비전 0 = 내장 기본 정책** `{"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}`(Detector 11/02 회신값) — 저장된 행이 없는 상태가 r0이며 시드 마이그레이션 불필요. **첫 저장이 리비전 1**. 버전 없음·저장 즉시 적용("저장 이후 새 판정부터")·되돌리기 = 새 저장(룰 세트 패턴)
- **화면 미노출 — 확정(사용자 11/02, WebUI 경유, Q-M10)**: 판정 정책은 WebUI에 편집·읽기 전용 표시 모두 두지 않음. `GET/PUT /api/detection-policy`는 **운영 담당용 API**(API·운영 담당이 관리)
- `GET /api/detection-policy`(로그인 사용자) → `{revision, policy, savedAt, savedBy, restoredFromRevision, isDefault}`. `PUT /api/detection-policy`(관리자) 본문 `{expectedRevision, policy, restoreFromRevision?, note?}`: 409 `detection_policy_revision_conflict`, 400 `invalid_detection_policy`(형태·v1/v2 혼용·`version: 1`), 422는 #92 7.4가 정한 의미 검증 코드(예 `combination_k_out_of_range:<category>`), 409 `detection_policy_unchanged`. 응답 `{revision, previousRevision, appliesFrom: "next_decision"}`. 이력 `GET /api/detection-policy/history`(+`/:revision` 스냅샷), append-only, 모든 항목에 [되돌리기]
- **`k`와 활성 방법 수의 정합 — 정책 저장 검증 순서** [1차 구현 확정 11/03 #92 U4·7.4 검증 표; min(k, n) 보정 **아님**(Detector 11/02 회신 Q-M8)]: (가) **먼저** `k.hard`·`k.soft`·`k.ml`이 **모두 1**이어야 함 — 각 대분류의 현재 n과 **무관**(n = 0인 대분류 포함, 세 값 전부 검사). 하나라도 1이 아니면 422 `combination_k_out_of_range:<category>`. (나) 그다음 k > n 검사: 활성 대분류(`n_c ≥ 1`)에서 `k_c < 1` 또는 `k_c > n_c`면 같은 코드로 거절(#92 7.4 검증 표 마지막 행 — U4 완화 시 필요). "`n_ml = 0`이면 ml 대분류 비활성이라 `k.ml` 값과 무관하게 허용"은 **이 (나) k > n 검사에 한정된 문장**이며, U4 적용 상태에서는 (가)가 선행하므로 `n_ml = 0`이어도 `k.ml = 2`는 **저장될 수 없음**(#92 16절 시나리오 S4 — 그 값이 저장되면 첫 모델 활성화(`apply`)가 정책 정합에 막혀 정책을 먼저 고치기 전까지 어떤 모델도 운영에 넣을 수 없게 되는 문제를 (가)가 막음). (가) 덕분에 룰 세트 저장·`retire`·활성 집합 변경으로는 k > n이 생길 수 없어 그 경로들은 `detection_methods_empty`만 검사(활성 집합 절 공통 검사 (2)). 두 저장 모두 통과했는데도 실행 시 위반이면(동시 변경 경합) Detector가 fail-closed(판정 행 없이 시도 실패). 이 항목과 #92 7.4가 어긋나면 #92 7.4가 우선(위 `PUT /api/detection-policy` 항목)
- 비교 평가는 접수 시점 정책 리비전·값을 고정해 모든 항목에 적용([config-comparison-evaluation.md](config-comparison-evaluation.md))

## 불변성·저장소(사실 + 확정 단위 추가분)

- `ml_config_versions`·`ml_config_models`·`ml_config_rules`·`ml_artifacts`·`run_evaluations`는 **이미 UPDATE/DELETE 금지 트리거가 있음**(`004_feature_pipeline.sql:84-96` `pipeline_immutable` → `prevent_pipeline_immutable_mutation()`). #74 "선택: 불변 트리거 추가"는 **불필요**(PostgreSQL 세션 10/21 발견 사항 반영). 새 마이그레이션은 이 트리거를 유지해야 하며 spec 보정은 새 행으로만
- `ml_config_models.training_settings`: **`methodId`·`methodVersion`·`parameters`(확정 파라미터 = 카탈로그 기본값 + 요청 값)는 #111에서 추가됨** — 기존 키(`snapshotId`·`split`·`featureNames`·`definitionsHash`·`splitVersion`·`thresholdPolicy`·`trainingSelection`) 유지, configuration_spec schemaVersion 1·2 틀 그대로, jsonb라 마이그레이션 없음. prepared 경로(`trainer/prepared_training.py`, `model_key` `iforest` 고정·`training_settings`에 세 키 없음)는 **아직 미반영**이며 후속에서 맞춤. v3 행은 `timeBasis`·`trainingPeriod`도 저장 [#132]. `frame`(그 `methodId`의 카탈로그 틀)을 저장 [확정 11/28 — 구현 전, 없으면 `row`로 읽음]. 요청에 `excludedIntervals` 키가 있던 v3 행은 `excludedIntervals[{from, until, reasonCode, note}]`(UTC 밀리초·from 순)도 저장 [선정 정책 v2 구현]
- `run_jobs.input_spec`(train)에 `parameters`·`excludedIntervals`·`timeBasis` 키 추가(jsonb). `detectionPolicy`는 v3 train `input_spec`에 **없음**
- [확정 11/02 Q-M1, 이름은 가칭 — DB 세션 결정] `ops_active_model_set_revisions`(`revision bigint PK, snapshot jsonb(models[]), changes jsonb, note, restored_from_revision, changed_by_actor_id, changed_at`, append-only + 불변 트리거, r0 행은 빈 집합) / `ops_detection_policy_revisions`(`revision bigint PK ≥ 1, policy jsonb, note, restored_from_revision, saved_by_actor_id, saved_at`, append-only; 행 없음 = r0 기본 정책) / 현재 값은 최대 `revision` 행. 세 리비전(룰·활성 집합·정책)의 일관 읽기용 열 또는 뷰는 DB·Detector. `ops_system_state.active_config_version_id`는 v1·v2 운영 호환으로만 남김

## schemaVersion 3 판독 규칙 — fail-closed [유지 #74 B1]

- `schemaVersion`을 모르는 판독기는 그 구성을 읽지 않고 `unsupported_config_schema`로 실패. v3를 v1 규칙(IF 1개·mode 분기)으로 조용히 해석 금지
- worker `parse_policy`(`policy.py:30-31`, 키 집합 정확 일치)는 v2 객체를 만나면 예외 → 자동 fail-closed. 단 v3 spec처럼 `detectionPolicy` 키가 없으면 예외가 아니라 legacy 호환 정책을 돌려준다(아래 Detector 소비처 v03 항목). TS `configRoutes.ts` 단일 구성 적용의 정책 모드 읽기(`mode ?? 'parallel'`)·`inputRoutes.ts` 입력 구성 목록 조회는 v3를 받지 않도록 schemaVersion 1·2로 한정했다(아래 v03 항목)
- 소비처 목록(#74 표 그대로): `detector/computation.py:17-60`, `detector/policy.py:26-37`, `detector/applications.py:35-55`, `trainer/runner.py:340-341·459`, `trainer/service_contract.py:21-33`, `configRoutes.ts:107`, `inputRoutes.ts:22`, `reportRoutes.ts:97-123`(#74 당시 줄 번호). Detector 현황은 다음 항목
- Detector 소비처 [11/15 코드 기준]:
  - v04 활성 집합 로더 `computation.load_model_context`(`attempt_v04.load_models` — v04 시도와 비교 평가 종합(`composite`)이 같은 로더를 쓴다): `schemaVersion` 정수 1(ML 후보, train·prepared)·3(모델 버전)만 읽는다. 그 밖(2 = `rule_only`라 모델 행 없음, 0·4 이상, 키 없음, `true`·`"3"`·`3.0`)은 모델 행을 읽기 전에 `unsupported_config_schema`. 이 오류로 시도가 실패하지 않고 **그 모델만** 미판정(`not_ready`·`not_configured`, evidence `reason: unsupported_config_schema`)이며 다른 방법 결과는 남는다. v3에서 쓰는 값은 `featureSetId`와 그 `modelKey`의 `ml_config_models` 행 1개뿐이고 정책·룰은 운영 상태에서 읽는다. v3 형식(`contractVersion`·`methods`) 검사는 하지 않는다(Trainer 판독 몫)
  - v03 작업 고정 경로 `computation.load_context`·`policy.parse_policy`(`runner.py`, 적용 준비 `applications.prepare_config`): `schemaVersion`을 확인하지 않는다. v3 spec은 `detectionPolicy`가 없어 legacy 호환 정책(`parallel`·`any_anomaly`, 구성 룰 없음)으로 읽히므로 fail-closed가 **아니다**
    - v03 고정 구성의 출처는 셋: (1) 입력 고정 `ops_input_controls.config_version_id`(`jobs.py` `_controlled_config`·`detect_contract` — v04 모드여도 고정 구성이 활성 구성과 다르면 v03), (2) 데모 메시지의 `configVersionId`, (3) `ops_system_state.active_config_version_id`(v1·v2 호환, legacy 적용 요청)
    - Detector `applications.prepare_config`는 `schemaVersion`을 확인하지 않는다. 그래서 v3 거절은 이 구성을 정하는 API 경로가 맡는다(다음 항목)
    - v3 구성(schemaVersion) 거절 422 `config_schema_not_supported_yet` [구현]: 활성 집합 경로(`activeModelSet.ts`)는 schemaVersion 1·2·3 밖을 거절한다. v03 고정 구성을 정하는 API 경로 둘 — legacy 적용·복원(`configRoutes.ts`, `expectedActiveVersionId` 요청)과 입력 고정(`inputRoutes.ts` `POST /api/inputs/:sourceId/mode`) — 은 공용 검사(`services/legacyConfig.ts`)로 schemaVersion 1·2만 받고 v3를 거절한다(아래 활성 집합 절의 단일 구성 경로 항목). 운영자 CLI `input select --config`(`collector/input_store.py` `switch`)도 같은 규칙으로 거절한다. 데모 메시지의 `configVersionId`도 이 검사 밖이다. 같은 오류 코드를 쓰는 학습 범위 거절(`jobRoutes.ts` `timeBasis`·제외 구간)은 이 항목과 별개다
    - `load_context`에서 거절하지 않는 이유: Trainer v3 평가·비교(`trainer/runner.py` `_version_context`)가 `load_context`를 쓴다. `prepare_config`의 보조 방어는 기동 시 `prepare_active`에 영향이 있어 후속 결정
- 허용 [구현]: v3 생성(train)은 워커(#132)가 만들고 API `POST /api/jobs`가 v3 train 요청을 접수한다(생성 경로 (A)). v3 활성화는 v04 활성 집합 로더에서 허용한다 — Detector는 v3를 `computation.load_model_context`(위 Detector 소비처)로 읽으며, #92 후속으로 기다리던 "Detector v3 로더"가 이 로더다. API 활성화 gate(`activeModelSet.ts`)는 schemaVersion 1·2·3만 받고 그 밖의 값(키 없음·`null` 포함)은 422 `config_schema_not_supported_yet`(fail-closed). v03 작업 고정 경로는 v3를 fail-closed로 읽지 않으므로 이 허용 범위에 들지 않는다
- Trainer 판독 [#132 구현]: 평가·결과 조립·prepared 평가가 `trainer/model_version.py`로 `schemaVersion` 1·2·3만 읽고 그 밖(키 없음 포함)은 `unsupported_config_schema`. v3는 형식(`contractVersion`·`contentHashVersion`·`methods` 1개·`enabled`·`detectionPolicy` 없음)까지 맞아야 읽음
- v3 `kind=evaluate` [#132 구현]: 구성에 룰·정책이 없으므로 **그 모델 1개만** 평가한다(`ml_only`·`selected_path`, 행 기한은 운영 설정 `deadlineMs`). 측정 전에 산출물을 행 기한 밖에서 한 번 검증·적재하고(`ValueError`는 그 코드로 작업 실패, 그 밖의 예외는 재시도 뒤 `job_processing_failed`) 첫 행을 결과 없이 한 번 돌린다. 측정 행은 운영과 같은 기한 값·판단 코드를 쓰며, 시계 기준점은 다르다(Detector는 처리 시작 시각, Trainer는 `compute` 호출). 예열 시간은 `totalMs`에 들어가고 `evaluationMs`·행 분위수에는 들어가지 않는다. 활성화 gate의 기술 무결성 근거로 쓰는 범위이고, 룰 세트·정책 조합 `composite` 평가는 비교 평가([config-comparison-evaluation.md](config-comparison-evaluation.md)) 몫
- 리비전 기록 [#132 구현]: v3 train·evaluate는 시도 시작에 뷰 `ops_operational_state` 한 문장으로 활성 집합·정책을 읽어 `resultSummary.activeModelSetRevision`·`resultSummary.detectionPolicy {revision, policy, fixedAt: "attempt_start"}`에 싣고, 평가는 `run_evaluations.evaluation_spec`·`metrics`·`ruleMlComparison.details`에 `activeModelSetRevision`·`detectionPolicyRevision`·`detectionPolicy`(정책 값 복사)를 남긴다. 읽을 수 없으면 값을 지어내지 않고 작업 실패(`operational_state_unavailable` 등). 활성 집합 변경 규칙(`detection_methods_empty`, 같은 `modelKey` 1개 — 11/28부터 틀당 1개)은 API 몫이라 검사하지 않음. 운영 역할 `powercode_trainer`는 이 뷰와 세 표를 SELECT만 한다(`scripts/database-roles.sql`, 뷰가 `security_invoker`라 세 표 모두 필요)

## 기존 계약 문구 — 변경 제안(이 초안에서 수정하지 않음)

| 문서 | 현재 문구 | 제안 |
|---|---|---|
| `pipeline.md:20` | train 요청 `detectionPolicy:{mode,deadlineMs,combination}` 필수 | v1 형식 요청에 한함; v3 요청에는 `detectionPolicy` 없음(운영 설정 전역 값) + `excludedIntervals` — 판정 문단은 Detector(#92)가 수정 |
| `trainer-service.md` | `models[{key,displayName,supported}]`·`iforest`만·`modelManifest` 단일 | [trainer-service.md](trainer-service.md) 변경 제안 절(#94)에 통합 |
| `synthetic-evaluation.md` | "기존 IF" 전제 | "활성 ML 방법 0..N"으로 일반화(#74 제안 유지) |

## 확정 요약·결정 질문

- 유지(#74 10/21 확정, 모델 단위로 축소): 식별자 형식 / 기술 무결성 gate + 비교 + 사람 수락 / 실행 불가 방법 저장 거부 / hash 입력 규칙 / 파라미터 변경=재학습 / v1·v2 형식 혼용 400 / 중복 재저장 안내 / `acceptedRecommendation`·stale
- 확정(11/02): 룰 항목 제거, 버전·교체·복원·비교는 모델만, ML 다중 운영 유지, 9.4 운영에서 빼기·같은 종류끼리 교체 추천
- 확정(사용자 11/02 Q-M1, WebUI 경유): 모델 1개 = 버전 계열·활성 집합 0..N·같은 modelKey 1개 / `methods[]` 길이 1 유지(평탄화 안 함) / 조합 저장 경로 없음 / `detectionPolicy` 운영 설정 전역(Detector 동의) / 활성 집합·정책 리비전 이력 / 일관 읽기 3요소
- 확정(사용자 11/28, 이상 ML 두 틀 — 구현 전): 활성 집합 틀당 최대 1(합계 2)·`replace` = 같은 틀 안 교체·`frame` 복사(없으면 `row`)·복원 위반 거절·`frame_mismatch`·고정 시 그 틀만 fail-closed(`row`면 룰 판정도 멈춤)·방법 0개 = 행 판정 방법 0개·`n_ml` = 행 틀 모델 수·학습 요청에 틀 키 없음·stale = 같은 틀의 활성 변경
- 확정(사용자 11/03, #92 U1~U12 전부 "추천대로" 확정): 식별자·대분류 키(U1, Q-M4) / 정책 저장 시 k 모두 1(U4, Q-M8 범위 정리) / 리비전 고정은 시도 단위(U5) / v1 판별은 `version` 키 없음만(U12)

| # | 질문 | 제안 |
|---|---|---|
| Q-M1 | 단위: (A) 모델 1개 = 버전 계열(modelKey별 lineage)·활성 집합 0..N·같은 modelKey 1개 vs (B) 모델 버전 = ML 1..N 묶음·활성 버전 0 또는 1개 | **확정(사용자 11/02, WebUI 경유): (A) 채택**. 근거(9.4 두 결정의 귀결): "운영에서 빼기"는 (B)에서 빼기 조작이 교체(새 묶음 버전)와 구별되지 않고, "같은 modelKey끼리 교체 추천"은 묶음 버전 간 추천 규칙과 맞지 않음. 구현 영향: 활성 집합 저장소·리비전·`retire`·`replace`·`active_model_key_duplicate`·modelKey별 추천 배열·비교 기준선 복수화, 조합 저장 경로 없음 |
| Q-M2 | `detectionPolicy`(k:{hard,soft,ml}) 저장 위치 | **운영 설정 전역 값으로 전환**(위 운영 설정 절, Detector 11/02 동의): 버전 없음·리비전 이력·r0 = 내장 기본·첫 저장 r1. Q-M1이 (A)·(B) 어느 쪽이어도 유지(모델 버전 spec에 두면 룰만 운영 중일 때 정책을 바꿀 수 없다는 Detector 지적이 (B)에서도 성립). 폐기된 안: 모델 버전 spec 포함(hash 포함) / 룰 세트 스냅샷에 `k.hard`·`k.soft`만 |
| Q-M3 | schemaVersion 번호 3 유지 vs 4 | 3 유지(저장된 v3 없음). Detector 선호에 따름 |
| Q-M4 | 대분류 키 `hard/soft/ml`·식별자 접두 — Trainer·Detector 세션 합의(11/02) | **확정(사용자 11/03, #92 U1)**: 합의안 그대로 승인(rule-set.md Q-R7과 같은 질문). 이 문서·rule-set.md·method-catalog.md의 식별자 절 표기를 확정으로 갱신(#106) |
| Q-M5 | 조합 저장 `POST /api/configurations`(B 경로) | **확정(Q-M1 귀결): 조합 저장 경로 없음** — 학습 = 버전, 재저장은 항상 `duplicateOf`. (B) 절은 기록용 폐기 표기 |
| Q-M6 | 활성 집합 저장: 새 append-only 테이블 + 리비전(제안) vs `ops_system_state`에 `active_model_version_ids uuid[]` 열 추가 | 새 테이블(스냅샷 이력·복원·일관 읽기가 룰 세트와 같은 패턴). 이름·일관 읽기 뷰는 DB 세션 |
| Q-M7 | 탐지 방법 0개가 되는 변경 허용 여부 | **확정(Detector 11/02)**: 결과 상태가 켜진 하드룰 0 + 켜진 소프트룰 0 + 활성 모델 0이 되는 **모든 변경**(룰 세트 저장·`retire`·활성 집합 교체/제거·복원)을 422 `detection_methods_empty`로 거절 — rule-set.md·이 문서 양쪽 같은 정의 |
| Q-M8 | 정책 `k.ml` > 활성 모델 수 | **확정(Detector 11/02, 범위는 11/03 사용자 확정 #92 U4로 정리)**: min(k, n) 보정 아님. 1차 구현은 정책 저장 시 `k.hard`·`k.soft`·`k.ml`이 n과 무관하게 **모두 1**이어야 하며(아니면 422 `combination_k_out_of_range:<category>`) 이 검사가 **먼저** 걸림. "`k.ml > n_ml`(`n_ml ≥ 1`) 거절·`n_ml = 0`은 무관"은 그 뒤 k > n 검사에 한정된 문장이라 U4 적용 상태에서는 `k.ml = 2`가 저장되지 않음. 활성 집합 변경은 `detection_methods_empty`만 검사(k > n이 생길 수 없음). k ≥ 2 허용(사용자 재결정) 시에만 활성 집합 변경으로 `n_ml`이 `k.ml` 미만이 되면(0 제외) 거절하는 대칭 검사 추가. 코드는 #92 7.4의 k > n 코드, 실행 시 위반은 Detector fail-closed |
| Q-M9 | `restore`를 `POST /api/configurations/:id/applications`에 두기(`:id` 무시) vs `POST /api/active-models/restore` 전용 | 전용 경로 권장(`:id`가 의미 없음). 둘 다 허용으로 적음 |
| Q-M10 | 운영 설정 `detectionPolicy`의 화면 노출 범위 — 편집 가능 vs 읽기 전용 표시 vs 미노출 | **확정(사용자 11/02, WebUI 경유): 화면 미노출**(편집·읽기 전용 모두 없음). `GET/PUT /api/detection-policy`는 운영 담당용 API로만 유지(관리자 권한) |
