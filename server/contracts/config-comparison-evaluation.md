# 구성 비교 평가 계약 v1 (초안, Issue #74)

- 계약 버전: `config-comparison-v1`(비교 작업), `evaluation-set-v1`(평가 세트). 근거 코드 origin/dev `872f540`
- 상위 결정: WebUI 화면 흐름 4절(하나의 불변 평가 세트 위 다중 구성 비교, 현재 운영 버전 항상 포함, 종합 + 모델별·방법별 + 4종 가족·applicability), 플러그형 설계 방향 3절(모델별 개별 결과 + 구성 종합, 하나로 합산 금지), Q-C=A(세트가 지정한 스냅샷 train 분할 기준 생성·고정)
- 표기: **[확정]** / **★** 제안(결정 대기) / **충돌**
- 범위: 평가 세트 엔티티(생성·고정·조회), 비교 작업(요청·검증·상태·결과), 적용 gate와의 관계, 기존 [합성 평가 계약](synthetic-evaluation.md) 변경 제안
- 제외: 종합 판정 조합 규칙(Detector 계약 — 비교 결과의 `composite`는 Detector 순수 함수 호출 결과를 집계만 함), 자동 교체·자동 승인, WebUI 화면, 새 합성 생성기 설계(기존 ESB P02~P04·ADBench 프로필만 재사용)
- 기존 규약 유지: 4종 평가 가족 봉투([JSON Schema](trainer-evaluation-envelope.schema.json))·measurement 규약·"미측정은 null+사유". `synthetic-evaluation.md`의 "합성만으로 운영 적용 검증 대체 금지"는 **[확정 10/21]** "평가 세트 비교 + 사람 수락"으로 대체 — 변경 제안 표 참조

## 평가 세트 `evaluation-set-v1`

- 정의: source 1개·스냅샷 1개·분할(purpose) 1개 위에 **선택적으로** 합성 행을 1회 생성해 **고정**한 불변 평가 모집단. 구성과 무관하게 만들어지며 여러 구성이 같은 세트로 평가됨 [확정 Q-C=A]
- 생성 기준: 합성 생성 통계(ESB P02/P03 어댑터 기준선·ADBench `fitScope: snapshot_train_only_max_1000`)는 **세트가 지정한 스냅샷의 train 분할**에서 계산. 평가 대상 구성의 학습 스냅샷은 참조하지 않음 — **충돌**: 현행은 평가 요청 구성의 학습 스냅샷 train(`synthetic_execution.py:181-182`, `synthetic-evaluation.md` "P02/P03: train 전용 어댑터 기준선", "생성 통계는 train만")
- 합성 없는 세트(원본 분할만)도 허용 ★: `synthetic: null`. 이 세트의 구성별 평가 행은 현행 원본 평가와 같은 의미(gate 증거 가능)
- prepared 스냅샷(`prepared-features-v1`)은 세트로 쓸 수 없음 ★(`prepared_not_comparable`; 운영 적용 차단 계보 유지)
- 상한: 합성 요청 상한은 현행 그대로(seed 0~2147483647, repetitions 1~5, targetRows 1~100, 시나리오 1~4·반복×항목 ≤20, cohort 2000행·학습 참조 10000행·산출물 64 MiB — `synthetic_profiles.py:52-56`, `synthetic-evaluation.md` 평가 요청 절). 세트 개수 상한 없음·삭제 없음 ★ Q-L=A

### 생성 — `POST /api/evaluation-sets` [신규, 관리자, 비동기]

```json
{
  "sourceId": "<source UUID>",
  "snapshotId": "<ready snapshot UUID>",
  "purpose": "validation",
  "displayName": "검증 세트 10/21 — P04 sev1 ×2",
  "synthetic": {
    "profileId": "esb-process-research-v1", "profileVersion": "1",
    "scenarios": [{"scenario": "P04", "severity": 1}],
    "seed": 42, "repetitions": 2, "targetRows": 10
  }
}
```

- 헤더 `Idempotency-Key` 선택(기존 규약). 응답 `201 {id, jobId, status: "queued"}`; 같은 관리자·키·같은 본문 200, 다른 본문 409 `idempotency_payload_conflict`
- 작업: `run_jobs(kind='evaluation_set', queue='training')` ★ 새 `job_kind` 값. Trainer worker가 claim(`trainer/runner.py:678` `job_kinds`에 추가)
- 서버 고정 binding(`syntheticProfileBinding`)은 현행대로 서버가 채움, 클라이언트 주입 거부
- 상태 `queued → ready | failed`. 생성 거부·미지원·전부 미생성은 `failed` + 사유(현행 "전부 미생성은 작업 실패" 유지). 일부 시나리오만 생성된 세트는 `ready`이되 `generation.trialStatuses`에 거부·실패 수 명시
- 산출물: `ARTIFACT_ROOT/evaluation-sets/<setId>/<content SHA>.json` ★(현행 `evaluations/<job UUID>/<sha>.json`과 같은 저장기 `synthetic_storage.store` 재사용, 디렉터리만 추가). 내용: 원본 행 식별자 목록 hash·합성 행(주입 전후 raw 복사본 또는 수치 행)·정답(`label=1` 주입 행)·프로필·seed·세트 스냅샷 train 통계 참조 hash. 임시 파일 격리·완성 후 원자 이동·동일 내용 재시도 hash 재사용(현행 규약)
- 합성 행 정답·원본 라벨 출처: `labelSource` = 원본 `manual_snapshot_evidence` + 합성 `synthetic_injection`. 비주입 원본·S를 정상 정답으로 만들지 않음(현행 유지) → 정상 정답 없는 Precision/FPR은 null+사유

### 조회

- `GET /api/evaluation-sets?sourceId=&limit=&cursor=`: `{items: [{id, sourceId, snapshotId, purpose, displayName, status, synthetic, generation: {rowCount, originalRowCount, syntheticRowCount, truthCount, contentHash, artifactRef}, createdAt, readyAt}], pagination}`
- `GET /api/evaluation-sets/:id`: 위 항목 + `jobId`, `errorCode`, `syntheticProfileBinding`, `generation.trialStatuses`, `generation.unsupportedScenarios`
- 원문 행·파일 경로는 응답하지 않음(현행 prepared 규약과 동일)

### 저장 — 새 테이블 ★ `data_evaluation_sets`

- `id uuid PK, job_id uuid UNIQUE → run_jobs, source_id, snapshot_id → data_snapshots, purpose split_role, display_name text, synthetic_request jsonb|null, synthetic_profile_binding jsonb|null, generation jsonb, content_hash text, artifact_relative_path text|null, status text CHECK(queued|ready|failed), error_code text|null, created_by_actor_id, created_at, ready_at`
- 불변(ready 후 UPDATE 금지 트리거 ★). `content_hash`는 산출물 파일 hash와 같음
- 기존 WebUI DEV fixture 타입 `EvaluationSet{id, revision, immutable, snapshotId, splitManifestId, specId, purpose, scenarios[]}`(`frontend/src/features/detection/data/evaluationTypes.ts`)와 이름은 맞추되 `splitManifestId`·`specId`·`revision`은 서버 대응물 없음(WebUI 세션 통지)

## 비교 작업 `config-comparison-v1`

### 요청 — `POST /api/jobs kind=compare` [신규, 관리자]

```json
{
  "kind": "compare",
  "sourceId": "<source UUID>",
  "evaluationSetId": "<evaluation set UUID>",
  "configVersionIds": ["<config UUID>", "<config UUID>"]
}
```

- 기존 `/api/jobs` 권한·멱등키·재시도(최대 3회)·상태(queued/running/succeeded/failed) 그대로. 큐 `training`, Trainer worker claim ★
- **현재 운영 구성 자동 포함** [확정]: 접수 시 `ops_system_state.active_config_version_id`를 읽어 `role: "active"` 항목으로 추가(요청에 넣지 않음). 활성 구성이 없으면 항목 없이 진행하고 `baseline: null` ★. 활성 구성이 `configVersionIds`에도 있으면 1개로 합침
- 상한 ★ Q-L=A: 활성 포함 총 **≤ 8**, 초과 400 `comparison_too_many_configs`. 빈 목록(활성도 없음) 400 `comparison_configs_required`
- 검증(접수 시): UUID 형식·중복, 세트 `status=ready`·같은 source, 각 구성 같은 source·존재. 실행 가능 여부·겹침은 **항목별로** 기록하고 작업 전체는 접수(아래 항목 상태)
- `input_spec = {kind:'compare', sourceId, evaluationSetId, configVersionIds(활성 포함·정렬), baselineConfigVersionId|null, activeRevision}`; 큐 payload `{schemaVersion: 1, jobId, enqueuedAt}` 그대로

### 실행 — 구성별 평가

- Trainer가 구성마다 현행 `_evaluate`와 같은 경로(`load_context` → 행마다 `compute`, `trainer/runner.py:458-531`)로 평가하되 모집단은 **세트의 고정 행**(원본 분할 행 + 합성 행). 모델·fit 재학습 없음, 운영 구성 변경 없음
- 항목 실패 사유(해당 구성만 `failed`, 나머지 계속) ★ Q-F=A

| 사유 코드 | 조건 |
|---|---|
| `method_not_executable:<methodId>` | 활성 방법에 `executable.process=false`(Detector D13 A) |
| `training_evaluation_overlap` | 그 구성의 활성 ML 후보 학습 train과 세트 행이 observation 또는 execution 단위로 겹침(현행 `synthetic_execution.py:183-193` 검사 재사용) |
| `prepared_not_comparable` | 구성이 prepared 계보(`operationalEligibility.eligible=false`) |
| `config_source_mismatch` / `config_not_found` | 접수 뒤 삭제·불일치 |
| `evaluation_failed:<code>` | 평가 중 예외(현행 `ValueError` 코드 전달) |

- 모든 항목이 `failed`면 작업 `failed`(`comparison_all_items_failed`). 일부 성공이면 작업 `succeeded`이고 항목 상태 병기(빈 성공 대체 없음)
- 결과 공개: 항목별 `run_evaluations` 행 + 비교 묶음 + job 성공·queue ack를 **같은 트랜잭션**으로 확정(현행 규약). lease 상실 시 공개 차단

### 결과 — `GET /api/jobs/:id` `resultSummary`

```json
{
  "serviceContractVersion": "trainer-service-v1",
  "comparisonContractVersion": "config-comparison-v1",
  "comparisonId": "<comparison UUID>",
  "evaluationSet": {"id": "<set UUID>", "contentHash": "…", "purpose": "validation", "rowCount": 1200,
                    "originalRowCount": 1180, "syntheticRowCount": 20, "synthetic": true},
  "baseline": {"configVersionId": "<active config UUID>", "role": "active", "activeRevision": 12},
  "manualApprovalRequired": true,
  "replacementRecommendation": {
    "targetVersionId": "<config B UUID>",
    "basis": [{"metric": "composite.completeOnly.recall", "target": 0.65, "baseline": 0.556, "comparedTo": "<active config UUID>"}],
    "checks": {"executable": true, "methodUnavailableCount": 0, "itemStatus": "succeeded"},
    "reason": "better_than_active_on_all_basis_metrics",
    "noRecommendationReason": null,
    "generatedAt": "<UTC>"
  },
  "items": [
    {"configVersionId": "<active config UUID>", "role": "active", "status": "succeeded", "errorCode": null,
     "evaluationId": "<run_evaluations UUID>", "metricVersion": "comparison-methods-v1",
     "applicability": "unmeasured", "reasonCodes": ["selected_paths_unavailable", "synthetic_set_evaluation"],
     "counts": {"populationCount": 1200, "scoredCount": 1188, "unavailableCount": 12, "unlabeledCount": 1180},
     "composite": {
       "combinationPolicy": {"policyKey": "<Detector 정책 키>"},
       "labelCounts": {"anomalyLabelCount": 20, "normalLabelCount": 0, "unlabeledCount": 1180},
       "completeOnly": {"basis": "complete_only", "confusion": {"tp": 10, "fp": null, "tn": null, "fn": 8},
                        "precision": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "recall": {"value": 0.556, "unit": "ratio", "numerator": 10, "denominator": 18, "coverage": 0.9, "reason": null},
                        "falsePositiveRate": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "coverage": 0.99, "unavailableCount": 12},
       "decidable": {"basis": "decidable", "confusion": {"tp": 11, "fp": null, "tn": null, "fn": 8}, "coverage": 0.995, "unavailableCount": 6,
                     "biasNote": "partial_true_included_none_excluded"}},
     "categoryResults": {
       "hr": {"status": "measured", "methodCount": 2, "confusion": {"tp": 7, "fp": null, "tn": null, "fn": 13}, "unavailableCount": 0},
       "sr": {"status": "skipped", "reason": "no_enabled_methods", "methodCount": 0},
       "ml": {"status": "measured", "methodCount": 1, "confusion": {"tp": 9, "fp": null, "tn": null, "fn": 11}, "unavailableCount": 12}},
     "methodResults": [
       {"methodId": "hr:process-status", "kind": "hr", "methodVersion": "process-status-hr-v1", "status": "measured",
        "confusion": {"tp": 7, "fp": null, "tn": null, "fn": 13}, "hitCount": 7, "unavailableCount": 0, "unavailableReasons": {}},
       {"methodId": "rule:process_errors", "kind": "rule", "status": "measured", "confusion": {"tp": 2, "fp": null, "tn": null, "fn": 18}, "hitCount": 2},
       {"methodId": "ml:iforest", "kind": "ml", "methodVersion": "process-iforest-v1", "status": "measured",
        "confusion": {"tp": 9, "fp": null, "tn": null, "fn": 11}, "scoreMin": 0.31, "scoreMax": 0.92,
        "threshold": {"direction": "higher_is_anomaly", "value": "0.61", "quantile": 0.99},
        "unavailableCount": 12, "unavailableReasons": {"model_features_unavailable": 12}}],
     "evaluationEnvelope": {"rule_ml": {}, "synthetic": {}, "execution_cost": {}, "operational": {}}},
    {"configVersionId": "<config B UUID>", "role": "candidate", "status": "succeeded", "errorCode": null,
     "evaluationId": "<run_evaluations UUID>", "metricVersion": "comparison-methods-v1",
     "applicability": "valid", "reasonCodes": ["synthetic_set_evaluation"],
     "counts": {"populationCount": 1200, "scoredCount": 1200, "unavailableCount": 0, "unlabeledCount": 1180},
     "composite": {
       "combinationPolicy": {"policyKey": "<Detector 정책 키>"},
       "labelCounts": {"anomalyLabelCount": 20, "normalLabelCount": 0, "unlabeledCount": 1180},
       "completeOnly": {"basis": "complete_only", "confusion": {"tp": 13, "fp": null, "tn": null, "fn": 7},
                        "precision": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "recall": {"value": 0.65, "unit": "ratio", "numerator": 13, "denominator": 20, "coverage": 1, "reason": null},
                        "falsePositiveRate": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "coverage": 1, "unavailableCount": 0},
       "decidable": {"basis": "decidable", "confusion": {"tp": 13, "fp": null, "tn": null, "fn": 7}, "coverage": 1, "unavailableCount": 0,
                     "biasNote": "partial_true_included_none_excluded"}},
     "categoryResults": "<active 항목과 같은 형식, 생략>",
     "methodResults": "<active 항목과 같은 형식, 생략>",
     "evaluationEnvelope": {"rule_ml": {}, "synthetic": {}, "execution_cost": {}, "operational": {}}},
    {"configVersionId": "<config C UUID>", "role": "candidate", "status": "failed",
     "errorCode": "method_not_executable:sr:ewma", "evaluationId": null}
  ]
}
```

- 혼동행렬 칸별 null 규칙(WebUI 검토 의견 8): `fp`·`tn`은 `normal` 라벨 행에서만 세므로(`trainer/runner.py:482-485, 522-531`) `labelCounts.normalLabelCount=0`이면 `fp`·`tn`은 0이 아니라 **null**, `precision`·`falsePositiveRate`도 null+`verified_normal_labels_unavailable`. 같은 식으로 `anomalyLabelCount=0`이면 `tp`·`fn`·`recall` null. **충돌**: 현행 `classification_metrics`(`runner.py:487-492`)는 fp=0으로 두고 `precision=tp/tp`를 계산하므로 `comparison-methods-v1`은 이 null 규칙을 새로 적용(기존 `selected-paths-v3` 결과 재해석 없음). `hitCount`는 라벨 무관 hit 수
- `composite`: Detector 순수 함수(종합 판정)의 행별 결과를 집계만 함. `completeOnly`(현행 기준, `trainer/runner.py:530-531`)와 `decidable`(None만 제외) **병기** — 예시 active 항목: 라벨 20행 중 complete 18행(tp 10·fn 8), partial+True 1행(decidable에서 tp 11), partial+None 1행(둘 다 제외) ★ Detector Q7 C 대기 — Detector가 B(현행만)를 고르면 `decidable` 생략. 편향 방향은 `biasNote`로 표기
- `categoryResults{hr,sr,ml}`: 대분류 결과(Detector 1단계 결과) 기준 혼동행렬. 방법 0개는 `skipped`
- `methodResults[]`: 활성 방법마다 1개, [method-result-v1](method-catalog.md) 상태 집계. 실행 불가 방법이 활성인 구성은 항목 자체가 `failed`(실행 절 사유 표)이므로 성공 항목의 `methodResults`에 `method_not_executable` 결과는 생기지 않음(예시 2번째 항목). 라벨 있는 행만 혼동행렬, `unavailableReasons`는 사유별 건수. ML `score` 척도는 방법마다 다르므로 비교·합산하지 않음(표시만)
- `evaluationEnvelope`: 구성별 4가족 봉투 그대로. `synthetic`은 세트 합성 행 기준 측정(`synthetic-process-metrics-v1` 재사용, 구성별 분모 동일), `rule_ml`은 세트 원본 행 교차표(`rule-ml-comparison-v1`), `execution_cost`는 구성별 실측, `operational`은 `unavailable/operational_report_separate_time_scoped`. ESB와 ADBench 분모 합산 금지 유지
- 묶음 수준 지표 요약은 두지 않음(구성별 봉투를 하나로 합치지 않음) [확정 3절]. 순위 표시 없음, `manualApprovalRequired: true`
- `replacementRecommendation` [확정 — 사용자 10/21, 설계 방향 4.4절·WebUI 화면흐름 2.4절]: **교체는 시스템 '추천'만 자동, 운영 변경은 사람 '수락'으로만. 자동 교체 금지**. 비교 작업마다 추천 1건(또는 없음)을 계산해 결과에 포함
  - `targetVersionId` 추천 대상(활성 구성 자체는 추천하지 않음) 또는 null; `basis[]` 근거 지표(규칙 (d)의 `composite.*` 지표만)·대상 값·비교 대상 값·비교 대상 구성; `checks` 규칙 (a)~(c)의 통과 결과(`itemStatus`·`executable`·`methodUnavailableCount`); `reason` 추천 사유 코드; `noRecommendationReason`(`no_candidate_better_than_active` / `active_missing` / `all_candidates_failed` / `basis_metrics_unavailable`); `generatedAt`
  - 추천 규칙 ★(초안, 결정 대기): 후보 항목 중 (a) `status=succeeded` (b) 버전 `executable=true` (c) 모든 `methodResults[].unavailableCount=0` (d) 활성 구성 대비 `composite.completeOnly`의 정의된 지표(recall·precision·falsePositiveRate 중 null 아닌 것) 전부 같거나 우위이고 하나 이상 엄격 우위. 여러 개면 `basis` 지표 우위 폭이 큰 1개. 활성 구성이 없으면 추천 없음(`active_missing`). 점수 척도가 다른 ML 점수는 근거로 쓰지 않음
  - 수락 경로: 사람이 [교체 요청] → `POST /api/configurations/:id/applications`에 `acceptedRecommendation{comparisonJobId, targetVersionId}`([detection-config](detection-config-version.md) 교체 절). 추천을 따르지 않는 교체 요청도 허용(수락 없이 다른 버전 선택)
- 같은 세트 위 구성별 행은 `run_evaluations.evaluation_spec_hash`가 같으므로 기존 비교 인덱스 `(snapshot_id, evaluation_spec_hash, purpose)`(`002_core_erd.sql:183`)로 조회 가능

### 조회 API

- `GET /api/comparisons?sourceId=&evaluationSetId=&limit=&cursor=` ★: `{items: [{comparisonId, jobId, evaluationSetId, baselineConfigVersionId, status, itemCount, succeededCount, failedCount, completedAt}], pagination}`
- `GET /api/comparisons/:id`: 위 `resultSummary` 전체(완료 전에는 `status`·접수 항목만)
- 구성 목록 `latestComparison`([detection-config](detection-config-version.md) 조회 절)은 이 테이블에서 계산

### 저장 — 마이그레이션 ★

| 변경 | 내용 |
|---|---|
| `job_kind` enum | `evaluation_set`, `compare` 추가(`schema.dbml:21-30`) |
| `run_evaluations` | `comparison_id uuid NULL → run_config_comparisons`, `evaluation_set_id uuid NULL → data_evaluation_sets` 열 추가. **충돌**: `job_id UNIQUE`(`002:172`)는 비교 작업 1개가 구성 N개 행을 만들면 위반 → `UNIQUE(job_id)` → `UNIQUE(job_id, config_version_id)`로 완화 ★(대안: 구성마다 자식 `evaluate` 작업 생성 + `run_jobs.parent_job_id` — 작업 수 N+1·부모 상태 집계 필요). PostgreSQL 세션 결정 |
| `run_config_comparisons` | `id PK, job_id UNIQUE, evaluation_set_id, source_id, baseline_config_version_id NULL, active_revision bigint, status, summary jsonb, completed_at` |
| `run_config_comparison_items` | `comparison_id, config_version_id, role CHECK(active|candidate), status CHECK(succeeded|failed), error_code, evaluation_id NULL → run_evaluations; PK (comparison_id, config_version_id)` |
| `data_evaluation_sets` | 위 세트 절 |
| 004 결과 검증 트리거 | `kind='evaluate'`에만 걸린 조건이 있으면 `compare`에 확장(`004_erd_v03_contracts.sql:223-228` 확인 범위에는 evaluate 전용 조건 없음, detect·LLM만) |

- 기존 `evaluate` 작업·`run_evaluations` 행·`metrics` 형식은 변경 없음(새 열은 NULL)

## 적용 gate와의 관계 [확정 — 사용자 10/21, 설계 방향 4.4절]

- 최종 관문은 **사람의 수락**. gate는 **기술 무결성만** 검사: 버전 `executable=true`(실행 불가 방법 없음), 산출물 경로·hash 검증(`applications.prepare_config`), 평가 행의 `scored_count>0 AND unavailable_count=0`(모든 행 complete·방법 결과 unavailable 0), prepared 계보 아님
- "원본 validation 평가 필수"는 **"평가 세트 비교(합성 행 포함 허용) + 사람 수락"으로 대체** [확정]. 적용 요청 `evaluationId`에는 비교 작업이 만든 구성별 `run_evaluations` 행(세트 `purpose=validation`)을 허용하며, 세트에 합성 행이 있어도 기술 무결성을 만족하면 `applicability='valid'`
  - **충돌(구현 Issue)**: 현행은 합성 평가를 `applicability='unmeasured'`·`synthetic_not_operational_validation`으로 강제(`trainer/runner.py:571-574`). `comparison-methods-v1`에서는 합성 행 포함 여부를 `reasonCodes`에 `synthetic_set_evaluation`(정보성)으로만 남기고 applicability는 기술 무결성으로 판단. 기존 `evaluate`(`synthetic-method-v1`) 결과는 재해석하지 않음
  - `synthetic-evaluation.md` "합성 측정 완료만으로 운영 적용의 원본 validation 검증을 대체하지 않음"은 변경 제안 표에 [확정 근거] 표기(아래)
- gate 조건 SQL(`configRoutes.ts:90-99`, `applications.py:102-109`)은 형식상 불변(`purpose='validation' AND applicability='valid' AND scored_count>0 AND unavailable_count=0`); 바뀌는 것은 Trainer가 비교 행의 applicability를 정하는 규칙과 `executable` 검사 추가
- `purpose=test` 세트의 행은 gate 증거로 쓰지 않음(현행 유지). 기존 `evaluate` 원본 validation 행도 계속 유효
- 자동 교체 금지. 추천(`replacementRecommendation`)은 화면 표시·수락 입력일 뿐 적용을 일으키지 않음

## `synthetic-evaluation.md` 변경 제안(수정하지 않음, 문장 목록)

| 위치 | 현재 문장 | 제안 |
|---|---|---|
| 범위·의미 1항 | "기존 `POST /api/jobs`의 `kind=evaluate` 선택 확장" | "`kind=evaluate` 선택 확장 **및** `kind=evaluation_set`(세트 생성)·`kind=compare`(세트 위 구성 비교)의 합성 행 생성 규약" 추가 |
| 범위·의미 2항 | "기존 IF·피처 순서·train 전용 fit·임계값 고정; 합성 평가 중 모델/fit 재학습·운영 구성 변경 없음" | "IF"를 "활성 ML 방법 0..N"으로 일반화, 나머지 유지 |
| 지원 프로필 표 아래 "P02/P03: train 전용 어댑터 기준선…" | 기준선 출처 미명시(현행 구현은 평가 구성의 학습 스냅샷) | "기준선은 **평가 세트가 지정한 스냅샷의 train 분할**에서 계산(세트 생성 시 1회)" 명시 |
| 평가 요청 절 "현재 실행 상한: 평가 cohort 2000행…" | 작업당 상한 | 세트당 상한으로 같은 값 적용, 비교 작업은 세트 행 수를 그대로 사용(재생성 없음) 추가 |
| 평가 요청 절 "snapshot 분할·회원/원본 행·선정 정책 불변; 생성 통계는 train만, 변형은 선택 validation/test 복사본만" | 구성 학습 스냅샷 기준 | "생성 통계는 **세트 스냅샷** train만" 으로 명시 |
| 결과·재현 절 "합성 산출물은 `evaluations/<job UUID>/<content SHA>.json`" | 작업별 저장 | "세트 산출물은 `evaluation-sets/<setId>/<content SHA>.json`, 작업별 평가 산출물은 기존 경로" 추가 |
| 결과·재현 절 "SR 실제 미연결은 null+사유" | SR 자리 고정 | "SR 방법 0개는 skipped, 실행 불가 방법은 `method_not_executable`" 보완 |
| 결과·재현 절 마지막 "합성 측정 완료만으로 운영 적용의 원본 validation 검증을 대체하지 않음" | 합성 평가는 gate 불통 | **[확정 근거: 사용자 10/21, 설계 방향 4.4절]** "평가 세트 비교(합성 행 포함 허용)의 기술 무결성 검사 + 사람 수락이 운영 적용 관문이며, 자동 교체는 금지"로 교체. `runner.py:571-574` 강제 unmeasured는 `evaluate` 경로에만 남김 |
| 제외 절 "최종 시나리오/모델 선정" | 유지 | "비교 결과의 자동 순위·자동 선정" 추가 |

- `trainer-service.md` 영향: 완료 결과 절에 `kind=compare`의 `resultSummary` 형식 링크 추가, `queues.md` Trainer 행에 `evaluation_set`·`compare` 추가

## 결정 대기

1. Q-C 생성 기준 [확정 A]; 합성 없는 세트 허용 ★
2. Q-E gate 증거: **[확정 10/21]** 기술 무결성 gate + 평가 세트 비교(합성 포함 허용) + 사람 수락, 자동 교체 금지(설계 방향 4.4절). 남은 결정: `replacementRecommendation` 추천 규칙 ★(위 결과 절)
3. Q-F 운영 구성 겹침: ★A 항목 failed·나머지 진행
4. Q-L 상한: ★ 구성 ≤8(활성 포함), 세트 상한·삭제 없음
5. `run_evaluations.job_id` UNIQUE 완화 vs 자식 작업 — PostgreSQL 세션
6. Detector Q7(종합 지표 기준 병기 ★C)·Detector 순수 함수 제공 시점(구현 순서 의존)
7. 활성 구성 없을 때 비교 허용(`baseline: null`) ★
8. 작업 kind 이름 `evaluation_set`·`compare` ★, 조회 경로 `/api/comparisons` ★
