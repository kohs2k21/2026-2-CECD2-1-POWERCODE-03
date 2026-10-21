# 구성 비교 평가 계약 v1 (초안, Issue #74)

- 계약 버전: `config-comparison-v1`(비교 작업), `evaluation-set-v1`(평가 세트). 근거 코드 origin/dev `872f540`
- 상위 결정: WebUI 화면 흐름 4절(하나의 불변 평가 세트 위 다중 구성 비교, 현재 운영 버전 항상 포함, 종합 + 모델별·방법별 + 4종 가족·applicability), 플러그형 설계 방향 3절(모델별 개별 결과 + 구성 종합, 하나로 합산 금지), Q-C=A(세트가 지정한 스냅샷 train 분할 기준 생성·고정)
- 상태: 초안 — 사용자 결정 반영 완료(10/21 일괄, 설계 방향 4.7절), 구현은 MyDocs `plans/20261021-Issue74-구현분할안.md` 참조. 표기: **[확정]** 사용자 확정·코드 사실 / **충돌** 현행 코드·계약과 어긋나 구현 Issue에서 바꿀 항목(결정은 끝남)
- 범위: 평가 세트 엔티티(생성·고정·조회), 비교 작업(요청·검증·상태·결과), 적용 gate와의 관계, 기존 [합성 평가 계약](synthetic-evaluation.md) 변경 제안
- 제외: 종합 판정 조합 규칙(Detector 계약 — 비교 결과의 `composite`는 Detector 순수 함수 호출 결과를 집계만 함), 자동 교체·자동 승인, WebUI 화면, 새 합성 생성기 설계(기존 ESB P02~P04·ADBench 프로필만 재사용)
- 기존 규약 유지: 4종 평가 가족 봉투([JSON Schema](trainer-evaluation-envelope.schema.json))·measurement 규약·"미측정은 null+사유". `synthetic-evaluation.md`의 "합성만으로 운영 적용 검증 대체 금지"는 **[확정 10/21]** "평가 세트 비교 + 사람 수락"으로 대체 — 변경 제안 표 참조

## 평가 세트 `evaluation-set-v1`

- 정의: source 1개·스냅샷 1개·분할(purpose) 1개 위에 **선택적으로** 합성 행을 1회 생성해 **고정**한 불변 평가 모집단. 구성과 무관하게 만들어지며 여러 구성이 같은 세트로 평가됨 [확정 Q-C=A]
- 생성 기준: 합성 생성 통계(ESB P02/P03 어댑터 기준선·ADBench `fitScope: snapshot_train_only_max_1000`)는 **세트가 지정한 스냅샷의 train 분할**에서 계산. 평가 대상 구성의 학습 스냅샷은 참조하지 않음 — **충돌**: 현행은 평가 요청 구성의 학습 스냅샷 train(`synthetic_execution.py:181-182`, `synthetic-evaluation.md` "P02/P03: train 전용 어댑터 기준선", "생성 통계는 train만")
- 합성 없는 세트(원본 분할만)도 허용 [확정 10/21]: `synthetic: null`. 이 세트의 구성별 평가 행은 현행 원본 평가와 같은 의미(gate 증거 가능)
- prepared 스냅샷(`prepared-features-v1`)은 세트로 쓸 수 없음 [확정 10/21](`prepared_not_comparable`; 운영 적용 차단 계보 유지)
- 상한: 합성 요청 상한은 현행 그대로(seed 0~2147483647, repetitions 1~5, targetRows 1~100, 시나리오 1~4·반복×항목 ≤20, cohort 2000행·학습 참조 10000행·산출물 64 MiB — `synthetic_profiles.py:52-56`, `synthetic-evaluation.md` 평가 요청 절). 세트 개수 상한 없음·삭제 없음 [확정 10/21] Q-L=A

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
- 작업: `run_jobs(kind='evaluation_set', queue='training')` [확정 10/21] 새 `job_kind` 값. Trainer worker가 claim(`trainer/runner.py:678` `job_kinds`에 추가)
- 서버 고정 binding(`syntheticProfileBinding`)은 현행대로 서버가 채움, 클라이언트 주입 거부
- 상태 `queued → ready | failed`. 생성 거부·미지원·전부 미생성은 `failed` + 사유(현행 "전부 미생성은 작업 실패" 유지). 일부 시나리오만 생성된 세트는 `ready`이되 `generation.trialStatuses`에 거부·실패 수 명시
- 산출물: `ARTIFACT_ROOT/evaluation-sets/<setId>/<content SHA>.json` [확정 10/21](현행 `evaluations/<job UUID>/<sha>.json`과 같은 저장기 `synthetic_storage.store` 재사용, 디렉터리만 추가). 내용: 원본 행 식별자 목록 hash·합성 행(주입 전후 raw 복사본 또는 수치 행)·정답(`label=1` 주입 행)·프로필·seed·세트 스냅샷 train 통계 참조 hash. 임시 파일 격리·완성 후 원자 이동·동일 내용 재시도 hash 재사용(현행 규약)
- 합성 행 정답·원본 라벨 출처: `labelSource` = 원본 `manual_snapshot_evidence` + 합성 `synthetic_injection`. 비주입 원본·S를 정상 정답으로 만들지 않음(현행 유지) → 정상 정답 없는 Precision/FPR은 null+사유

### 조회

- `GET /api/evaluation-sets?sourceId=&limit=&cursor=`: `{items: [{id, sourceId, snapshotId, purpose, displayName, status, synthetic, generation: {rowCount, originalRowCount, syntheticRowCount, truthCount, contentHash, artifactRef}, createdAt, readyAt}], pagination}`
- `GET /api/evaluation-sets/:id`: 위 항목 + `jobId`, `errorCode`, `syntheticProfileBinding`, `generation.trialStatuses`, `generation.unsupportedScenarios`
- 원문 행·파일 경로는 응답하지 않음(현행 prepared 규약과 동일)

### 저장 — 새 테이블 [확정 10/21] `data_evaluation_sets`

- `id uuid PK, job_id uuid UNIQUE → run_jobs, source_id, snapshot_id → data_snapshots, purpose split_role, display_name text, synthetic_request jsonb|null, synthetic_profile_binding jsonb|null, generation jsonb, content_hash text, artifact_relative_path text|null, status text CHECK(queued|ready|failed), error_code text|null, created_by_actor_id, created_at, ready_at`
- 불변(ready 후 UPDATE 금지 트리거 [확정 10/21]). `content_hash`는 산출물 파일 hash와 같음
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

- 기존 `/api/jobs` 권한·멱등키·재시도(최대 3회)·상태(queued/running/succeeded/failed) 그대로. 큐 `training`, Trainer worker claim [확정 10/21]
- **현재 운영 구성 자동 포함** [확정]: 접수 시 `ops_system_state.active_config_version_id`를 읽어 `role: "active"` 항목으로 추가(요청에 넣지 않음). 활성 구성이 없으면 항목 없이 진행하고 `baseline: null` [확정 10/21]. 활성 구성이 `configVersionIds`에도 있으면 1개로 합침
- 상한 [확정 10/21] Q-L=A: 활성 포함 총 **≤ 8**, 초과 400 `comparison_too_many_configs`. 빈 목록(활성도 없음) 400 `comparison_configs_required`
- 검증(접수 시): UUID 형식·중복, 세트 `status=ready`·같은 source, 각 구성 같은 source·존재. 실행 가능 여부·겹침은 **항목별로** 기록하고 작업 전체는 접수(아래 항목 상태)
- `input_spec = {kind:'compare', sourceId, evaluationSetId, configVersionIds(활성 포함·정렬), baselineConfigVersionId|null, activeRevision}`; 큐 payload `{schemaVersion: 1, jobId, enqueuedAt}` 그대로

### 실행 — 구성별 평가

- Trainer가 구성마다 현행 `_evaluate`와 같은 경로(`load_context` → 행마다 `compute`, `trainer/runner.py:458-531`)로 평가하되 모집단은 **세트의 고정 행**(원본 분할 행 + 합성 행). 모델·fit 재학습 없음, 운영 구성 변경 없음. **활성 항목을 항상 첫 번째로 평가**(콜드 스타트 비용을 활성이 부담, `warmCold=mixed_unclassified` 보정) [확정 10/21 R12]
- 항목 실패 사유(해당 구성만 `failed`, 나머지 계속) [확정 10/21] Q-F=A

| 사유 코드 | 조건 |
|---|---|
| `method_not_executable:<methodId>` | 활성 방법이 저장 뒤 카탈로그 변경으로 사후 실행 불가가 됨 — 어댑터 소실(Detector D13 B 방어 경로). 저장 시에는 이미 거부되므로 드묾 |
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
    "ruleVersion": "replacement-recommendation-v1",
    "comparisonId": "<comparison UUID>", "comparisonJobId": "<job UUID>", "evaluationSetId": "<set UUID>",
    "baselineConfigVersionId": "<active config UUID>", "baselineEvaluationId": "<run_evaluations UUID>", "baselineActiveRevision": 12,
    "targetVersionId": "<config B UUID>", "targetEvaluationId": "<run_evaluations UUID>",
    "summary": "실험 7이 현재 운영 V4보다 재현율 12/18(0.667) > 10/18(0.556), P04 sev1 탐지율 12/18 > 10/18, 지연 P95 41ms(한도 내)",
    "reason": "better_than_active_on_all_basis_metrics",
    "noRecommendationReason": null,
    "comparedRowCount": 1188,
    "comparedLabelCounts": {"anomalyLabelCount": 18, "normalLabelCount": 0},
    "basis": [
      {"metric": "composite.completeOnly.recall", "family": "composite", "direction": "higher_is_better",
       "target": {"value": 0.667, "numerator": 12, "denominator": 18},
       "baseline": {"value": 0.556, "numerator": 10, "denominator": 18},
       "outcome": "better", "comparedTo": "<active config UUID>"},
      {"metric": "synthetic.scenario.P04.sev1.combinedDetectionRate", "family": "synthetic", "direction": "higher_is_better",
       "target": {"value": 0.667, "numerator": 12, "denominator": 18},
       "baseline": {"value": 0.556, "numerator": 10, "denominator": 18},
       "outcome": "better", "comparedTo": "<active config UUID>"}
    ],
    "excludedMetrics": [
      {"metric": "composite.completeOnly.precision", "reason": "verified_normal_labels_unavailable"},
      {"metric": "composite.completeOnly.falsePositiveRate", "reason": "verified_normal_labels_unavailable"}
    ],
    "checks": {"itemStatus": "succeeded", "executable": true, "methodUnavailableCount": 0, "applicability": "valid", "costLimit": "passed"},
    "costs": [
      {"metric": "execution_cost.itemP95Ms", "target": 41.2, "baseline": 37.9, "limit": 75.8,
       "limitRule": "max(latencyFloorMs, baseline*latencyP95RelativeLimit) and deadlineMs*latencyDeadlineRatio", "outcome": "within_limit"}
    ],
    "candidates": [
      {"configVersionId": "<config B UUID>", "outcome": "selected", "excludedReason": null, "lostAt": null},
      {"configVersionId": "<config C UUID>", "outcome": "excluded", "excludedReason": "item_failed:method_not_executable:sr:ewma", "lostAt": null}
    ],
    "references": [
      {"metric": "rule_ml.hrBoth", "target": 0.12, "baseline": 0.10, "note": "checker_agreement_not_accuracy"},
      {"metric": "execution_cost.processRssBytes", "target": 412000000, "baseline": 409000000, "note": "shared_worker_process_sample"}
    ],
    "thresholds": {"minLabeledAnomalyCount": 10, "minScenarioDenominator": 10, "ratioEpsilon": 0.0,
                   "latencyP95RelativeLimit": 2.0, "latencyFloorMs": 50, "latencyDeadlineRatio": 0.5,
                   "costUnmeasuredPolicy": "allow", "staleAcceptPolicy": "reject",
                   "tieBreak": ["recall_tp", "fpr_fp", "precision", "scenario_min", "itemP95Ms", "created_at_asc"]},
    "generatedAt": "<UTC>"
  },
  "items": [
    {"configVersionId": "<active config UUID>", "role": "active", "status": "succeeded", "errorCode": null,
     "evaluationId": "<run_evaluations UUID>", "metricVersion": "comparison-methods-v1",
     "applicability": "unmeasured", "reasonCodes": ["selected_paths_unavailable", "synthetic_set_evaluation"],
     "counts": {"populationCount": 1200, "scoredCount": 1188, "unavailableCount": 12, "unlabeledCount": 1180},
     "composite": {
       "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hr": 1, "sr": 1, "ml": 1}},
       "labelCounts": {"anomalyLabelCount": 20, "normalLabelCount": 0, "unlabeledCount": 1180},
       "completeOnly": {"basis": "complete_only", "confusion": {"tp": 10, "fp": null, "tn": null, "fn": 8},
                        "precision": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "recall": {"value": 0.556, "unit": "ratio", "numerator": 10, "denominator": 18, "coverage": 0.9, "reason": null},
                        "falsePositiveRate": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "coverage": 0.99, "unavailableCount": 12, "hitCount": 31},
       "decidable": {"basis": "decidable", "confusion": {"tp": 11, "fp": null, "tn": null, "fn": 8}, "coverage": 0.995, "unavailableCount": 6,
                     "biasNote": "partial_true_included_none_excluded"}},
     "categoryResults": {
       "hr": {"measurementStatus": "measured", "methodCount": 2, "confusion": {"tp": 7, "fp": null, "tn": null, "fn": 13}, "unavailableCount": 0},
       "sr": {"measurementStatus": "skipped", "reason": "no_enabled_methods", "methodCount": 0},
       "ml": {"measurementStatus": "measured", "methodCount": 1, "confusion": {"tp": 9, "fp": null, "tn": null, "fn": 11}, "unavailableCount": 12}},
     "methodResults": [
       {"methodId": "hr:process-status", "kind": "hr", "methodVersion": "process-status-hr-v1", "measurementStatus": "measured",
        "confusion": {"tp": 7, "fp": null, "tn": null, "fn": 13}, "hitCount": 7, "unavailableCount": 0, "unavailableReasons": {}},
       {"methodId": "rule:process_errors", "kind": "rule", "measurementStatus": "measured", "confusion": {"tp": 2, "fp": null, "tn": null, "fn": 18}, "hitCount": 2},
       {"methodId": "ml:iforest", "kind": "ml", "methodVersion": "process-iforest-v1", "measurementStatus": "measured",
        "confusion": {"tp": 9, "fp": null, "tn": null, "fn": 11}, "scoreMin": 0.31, "scoreMax": 0.92,
        "threshold": {"direction": "higher_is_anomaly", "value": "0.61", "quantile": 0.99},
        "unavailableCount": 12, "unavailableReasons": {"model_features_unavailable": 12}}],
     "evaluationEnvelope": {"rule_ml": {}, "synthetic": {}, "execution_cost": {}, "operational": {}}},
    {"configVersionId": "<config B UUID>", "role": "candidate", "status": "succeeded", "errorCode": null,
     "evaluationId": "<run_evaluations UUID>", "metricVersion": "comparison-methods-v1",
     "applicability": "valid", "reasonCodes": ["synthetic_set_evaluation"],
     "counts": {"populationCount": 1200, "scoredCount": 1200, "unavailableCount": 0, "unlabeledCount": 1180},
     "composite": {
       "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hr": 1, "sr": 1, "ml": 1}},
       "labelCounts": {"anomalyLabelCount": 20, "normalLabelCount": 0, "unlabeledCount": 1180},
       "completeOnly": {"basis": "complete_only", "confusion": {"tp": 13, "fp": null, "tn": null, "fn": 7},
                        "precision": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "recall": {"value": 0.65, "unit": "ratio", "numerator": 13, "denominator": 20, "coverage": 1, "reason": null},
                        "falsePositiveRate": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "coverage": 1, "unavailableCount": 0, "hitCount": 29},
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
- `composite`: Detector 순수 함수(종합 판정)의 행별 결과를 집계만 함. `completeOnly`(현행 기준, `trainer/runner.py:530-531`)와 `decidable`(None만 제외) **병기** — 예시 active 항목: 라벨 20행 중 complete 18행(tp 10·fn 8), partial+True 1행(decidable에서 tp 11), partial+None 1행(둘 다 제외). **[확정 Q7 C, 10/21]** `completeOnly`·`decidable` 두 기준을 각각 `coverage`(분모/전체)·`unavailableCount`와 함께 **항상 병기**(생략 분기 없음). 편향 방향은 `biasNote`로 표기. `completeOnly.hitCount` = 라벨 무관 종합 판정 true 행 수(세트 경보율·경보/일 추정 참고용) [확정 10/21 R11]
- `categoryResults{hr,sr,ml}`: 대분류 결과(Detector 1단계 결과) 기준 혼동행렬. 방법 0개는 `measurementStatus: skipped`. U(정상 여부 미확인, 대분류 결과 None) 행의 집계 기준은 composite와 같은 Q7 C: `completeOnly`(대분류 방법 전부 succeeded인 행만)·`decidable`(대분류 결과 None만 제외, T는 partial이어도 포함) 두 기준을 병기하고 각각 `coverage`·`unavailableCount`를 붙임
- v1 운영 구성과 v3 후보를 같은 세트로 비교할 때 Detector 행동 변경 2(방법 단위 실행 후 등록 Rule 예외가 상태 HR 이상을 지우지 않음, Detector 문서 D4-1)로 드물게 v1·v3의 `composite`가 같은 입력에서 달라질 수 있음 — 차이는 `items[].metricVersion`·`detectionPolicy`(v2)로 구분하고 결과를 재해석하지 않음
- `methodResults[]`: 활성 방법마다 1개, [method-result-v1](method-catalog.md) 상태 집계. `measurementStatus`(`measured` / `unavailable` / `skipped`)는 **집계 측정 상태**이며 방법 결과 상태 enum(`succeeded/not_ready/failed/skipped`)과 다른 필드(이름 충돌 방지, Detector 의견 9). 실행 불가 방법이 활성인 구성은 항목 자체가 `failed`(실행 절 사유 표)이므로 성공 항목의 `methodResults`에 `method_not_executable` 결과는 생기지 않음(예시 2번째 항목). 라벨 있는 행만 혼동행렬, `unavailableReasons`는 사유별 건수. ML `score` 척도는 방법마다 다르므로 비교·합산하지 않음(표시만)
- `evaluationEnvelope`: 구성별 4가족 봉투 그대로. `synthetic`은 세트 합성 행 기준 측정(`synthetic-process-metrics-v1` 재사용, 구성별 분모 동일) — 봉투 내부는 `evaluate`와 같은 `details.trials[]{scenario, severity, repetition, status, metrics{hr,sr,ml,combined{injectedCount, detectedCount, unjudgedCount, detectionRate}}}` 형식 유지(`synthetic_execution.py:111-112`; 추천 규칙 (b)의 시나리오별 탐지율 입력 전제), `rule_ml`은 세트 원본 행 교차표(`rule-ml-comparison-v1`), `execution_cost`는 구성별 실측, `operational`은 `unavailable/operational_report_separate_time_scoped`. ESB와 ADBench 분모 합산 금지 유지
- 묶음 수준 지표 요약은 두지 않음(구성별 봉투를 하나로 합치지 않음) [확정 3절]. 순위 표시 없음, `manualApprovalRequired: true`
- `replacementRecommendation` [확정 — 사용자 10/21, 설계 방향 4.4절·WebUI 화면흐름 2.4절]: **교체는 시스템 '추천'만 자동, 운영 변경은 사람 '수락'으로만. 자동 교체 금지**. 비교 작업마다 추천 1건(또는 없음)을 계산해 결과에 포함
  - 규칙 버전 `replacement-recommendation-v1` **[확정 10/21 — 사용자 "A~C 추천안대로", 설계 방향 4.7절; 기획 MyDocs `prd/20261021-운영버전-교체추천-규칙.md` R1~R17 전부 추천값 채택]**. 입력은 비교 작업 1건의 `resultSummary`만(다른 작업·운영 DB 조회 없음). 비교 전에 **활성·후보 양쪽 모두 complete인 행의 교집합**으로 라벨·주입 분모를 다시 세고(R16) `comparedRowCount`·`comparedLabelCounts`에 기록 — 활성 구성이 세트 일부 행에서 unavailable이어도 추천 가능
  - (a) 후보 자격(순서대로 첫 실패 사유를 `candidates[].excludedReason`에 기록): ① 항목 `status=succeeded`(`item_failed:<errorCode>`) ② 버전 `executable=true`(`not_executable`, 사후 어댑터 소실 방어) ③ 교집합 행 밖에서도 모든 `methodResults[].unavailableCount=0`은 요구하지 않음 — 교집합 행에서만 비교(R16) ④ `applicability=valid`(`applicability_<value>`) ⑤ `operationalEligibility.eligible=true`(`operational_ineligible`). 활성 항목은 `status=succeeded`여야 함(`active_item_failed`)
  - (b) 1차 지표 — 비교 집합 `P` = 활성·후보 모두 `value≠null`인 것: `composite.completeOnly.recall`(분모 동일 → 분자 `tp` 정수 비교), `composite.completeOnly.precision`(비율 비교, `ratioEpsilon` 안이면 동등), `composite.completeOnly.falsePositiveRate`(분자 `fp` 정수 비교), `synthetic.scenario.<scenario>.sev<severity>.combinedDetectionRate`(같은 (scenario, severity)의 모든 repetition trial 합산 Σdetected/Σinjected, 분자 정수 비교, 합산 분모 < `minScenarioDenominator`면 제외 `insufficient_denominator`, `status≠generated` trial은 양쪽 제외) [확정 R1·R2]. anomaly 라벨 < `minLabeledAnomalyCount`면 recall 제외(R3). 한쪽이라도 null인 지표는 `excludedMetrics[]`에 `{metric, reason}`로 기록. **지배 규칙**: `P` 전부 같거나 우위 + 하나 이상 엄격 우위 → "활성보다 나음"; 하나라도 열위 `worse_on:<metric>`, 전부 동등 `equal_to_active`. 합산 점수·가중 평균은 만들지 않음(설계 방향 3절)
  - (c) 2차 — 실행 비용 한도(우위 요구 아님, 초과만 금지): 후보 `execution_cost.itemP95Ms` ≤ max(`latencyFloorMs`, 활성 × `latencyP95RelativeLimit`) **이고** ≤ 대상 구성 `detectionPolicy.deadlineMs` × `latencyDeadlineRatio`(R5). 활성 값 null이면 절대 한도만; 후보 값 null이면 `checks.costLimit="unmeasured"`로 기록하고 추천 허용(R6, `costUnmeasuredPolicy=allow`). `processRssBytes`·학습 시간은 한도 없이 참고(`references[]`, R7 — 비교 작업은 한 워커가 순차 평가하므로 RSS가 구성 귀속 값이 아님). 초과 후보 `cost_limit_exceeded:itemP95Ms`
  - (d) 참고 지표는 `references[]`에만: `rule_ml` 4셀(`checker_agreement_not_accuracy`), `methodResults[].hitCount`, `categoryResults` 혼동행렬, `composite.decidable`, `execution_cost.{totalMs, throughput, processRssBytes}`. 운영 보고(`/api/pipeline/reports/operational`)는 결과에 복사하지 않고 화면이 별도 호출
  - (e) 다수 후보 선택 — 사전식 6단계(R8, `thresholds.tieBreak`): 1 recall 분자 `tp` 큰 쪽 → 2 `fp` 작은 쪽(정의 시) → 3 precision 큰 쪽(정의 시, `ratioEpsilon` 밖) → 4 시나리오 탐지율 **최솟값** 큰 쪽 → 5 `itemP95Ms` 작은 쪽(둘 다 측정 시) → 6 `createdAt` **빠른** 쪽. 후보끼리 지배 관계는 요구하지 않음. 선택되지 않은 적격 후보는 `candidates[].outcome=eligible_not_selected`·`lostAt`(1..6)
  - 추천 없음(`targetVersionId=null`) 사유 9종, 먼저 걸리는 순서(R15 화면 문구는 기획 문서 3절): `active_missing`(baseline null) → `active_item_failed` → `active_results_unavailable`(교집합 행 0) → `no_candidates` → `all_candidates_failed` → `all_candidates_ineligible` → `basis_metrics_unavailable`(`P` 공집합) → `no_candidate_better_than_active` → `cost_limit_exceeded`. 추천 없음이어도 `candidates[]`는 항상 채움
  - 필드: 기존 `targetVersionId, basis[], checks, reason, noRecommendationReason, generatedAt` 유지 + `ruleVersion, comparisonId, comparisonJobId, evaluationSetId, baselineConfigVersionId, baselineEvaluationId, baselineActiveRevision, targetEvaluationId, summary, comparedRowCount, comparedLabelCounts, excludedMetrics[], costs[], candidates[], references[], thresholds` 추가. **기존 필드 형태 변경**(R17 [확정]): `basis[].target`·`baseline`은 스칼라 → `{value, numerator, denominator}` 객체, 행에 `family`·`direction`·`outcome` 추가(호환 원칙의 예외로 사용자 확정; 이 초안 안에서만 바뀐 것이며 운영 결과는 아직 없음). `basis[].metric` 키 형식 `composite.completeOnly.<name>` / `synthetic.scenario.<s>.sev<n>.combinedDetectionRate` / `execution_cost.<name>` / `rule_ml.<name>`
  - `summary`는 **서버(Trainer)가 한글 1줄 생성**(R13): "{대상 displayName}이 현재 운영 {활성 displayName}보다 " + basis 행마다 "{지표 한글명} {target} > {baseline}" + ", 지연 P95 {ms}ms(한도 내)"; 추천 없음이면 사유 문구. 임계값(`thresholds`)은 **Trainer 설정**(환경변수 `TRAINER_RECOMMENDATION_*` 또는 설정 모듈 상수, R14)이며 사용값을 결과에 기록. 기본값: `minLabeledAnomalyCount 10`, `minScenarioDenominator 10`, `ratioEpsilon 0.0`, `latencyP95RelativeLimit 2.0`, `latencyFloorMs 50`, `latencyDeadlineRatio 0.5`, `costUnmeasuredPolicy allow`, `staleAcceptPolicy reject`(R3~R6 [확정])
  - 계산 시점·불변: Trainer가 모든 항목 평가 직후, 항목 `run_evaluations` 행·비교 묶음·job 성공·queue ack와 **같은 트랜잭션**에서 1회 계산해 `run_config_comparisons.summary`에 저장. API는 저장값 반환(재계산 없음). 카탈로그·임계가 뒤에 바뀌어도 과거 추천은 다시 쓰지 않음(`thresholds`·`ruleVersion`을 결과에 박는 이유); `executable`만 수락 시점에 gate가 다시 검사
  - stale(R9 [확정]): 저장값은 그대로 두고 조회 시 파생 필드 `recommendationState` — `current`(`ops_system_state.revision == baselineActiveRevision`) / `applied`(활성 구성 == `targetVersionId`) / `stale`(그 외, 같은 버전 복원이라도 revision이 다르면 stale) / `none`(`targetVersionId` null). stale 상태의 수락 요청은 400 `accepted_recommendation_stale`(R10 [확정])
  - 추천 ⊂ gate: 자격 조건은 gate 중 결과만으로 확인 가능한 부분. 추천이 있어도 gate가 거부할 수 있고(산출물 hash 불일치·수락 시점 실행 불가), gate를 통과하는 버전이 추천되지 않을 수도 있음(열위·비용 초과) — 둘 다 정상
  - 수락 경로: 사람이 [수락하고 교체 요청] → `POST /api/configurations/{targetVersionId}/applications` `{operation:"apply", evaluationId: targetEvaluationId, expectedRevision: baselineActiveRevision, expectedActiveVersionId: baselineConfigVersionId, acceptedRecommendation:{comparisonJobId, targetVersionId}}`([detection-config](detection-config-version.md) 교체 절). 추천을 따르지 않는 교체·추천 없음 상태의 교체는 `acceptedRecommendation` 없이 기존 요청 그대로 허용. 실패 시 현행대로 기존 운영 버전 유지
- 같은 세트 위 구성별 행은 `run_evaluations.evaluation_spec_hash`가 같으므로 기존 비교 인덱스 `(snapshot_id, evaluation_spec_hash, purpose)`(`002_core_erd.sql:183`)로 조회 가능

### 조회 API

- `GET /api/comparisons?sourceId=&evaluationSetId=&limit=&cursor=` [확정 10/21 N7]: `{items: [{comparisonId, jobId, evaluationSetId, baselineConfigVersionId, status, itemCount, succeededCount, failedCount, completedAt, recommendation: {targetVersionId, state}}], pagination}`
- `GET /api/comparisons/:id`: 위 `resultSummary` 전체(완료 전에는 `status`·접수 항목만) + 파생 필드 `replacementRecommendation.recommendationState`(`current|applied|stale|none`, 조회 시 `ops_system_state`와 비교해 계산, 저장하지 않음)
- 구성 목록 `latestComparison`([detection-config](detection-config-version.md) 조회 절)은 이 테이블에서 계산하며 `recommendation: {targetVersionId, state}` 요약 포함

### 저장 — 마이그레이션 [확정 10/21]

| 변경 | 내용 |
|---|---|
| `job_kind` enum | `evaluation_set`, `compare` 추가(`schema.dbml:21-30`) |
| `run_evaluations` | `comparison_id uuid NULL → run_config_comparisons`, `evaluation_set_id uuid NULL → data_evaluation_sets` 열 추가. **충돌**: `job_id UNIQUE`(`002:172`)는 비교 작업 1개가 구성 N개 행을 만들면 위반 → `UNIQUE(job_id)` → `UNIQUE(job_id, config_version_id)`로 완화 **[확정 10/21 N6, 설계 방향 4.7절]**(대안이던 자식 `evaluate` 작업 + `run_jobs.parent_job_id`는 채택하지 않음). 마이그레이션 DDL은 PostgreSQL 세션 |
| `run_config_comparisons` | `id PK, job_id UNIQUE, evaluation_set_id, source_id, baseline_config_version_id NULL, active_revision bigint, status, summary jsonb, completed_at`. `summary`에 `replacementRecommendation`(`replacement-recommendation-v1`) 포함 — 항목 평가 행과 같은 트랜잭션에서 1회 저장·불변, stale은 저장하지 않고 조회 시 파생 |
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
| 제외 절 "최종 시나리오/모델 선정" | 유지 | "비교 결과의 자동 순위·자동 선정" 추가; "교체 추천(`replacementRecommendation`)은 선정이 아니라 수락 대기 제안" 보완 |

- `trainer-service.md` 영향: 완료 결과 절에 `kind=compare`의 `resultSummary` 형식 링크 추가("`replacementRecommendation`은 `replacement-recommendation-v1` 규칙으로 완료 시 1회 계산·불변" 포함), `queues.md` Trainer 행에 `evaluation_set`·`compare` 추가

## 확정 요약·남은 의존

- 확정(10/21, 설계 방향 4.4~4.7절): Q-C 세트 생성 기준 A·합성 없는 세트 허용(N5) / Q-E 기술 무결성 gate + 세트 비교 + 사람 수락, 자동 교체 금지 / Q-F 겹침 항목 failed / Q-L 구성 ≤8·세트 불변 / N5 활성 없음 비교 허용·prepared 금지 / N6 `run_evaluations` UNIQUE 완화 / N7 kind·경로 이름 / N15 `comparison-methods-v1` / Detector Q7 C 병기 / 교체 추천 규칙 R1~R17(`replacement-recommendation-v1`)
- 남은 의존(결정 아님): Detector 조합 순수 함수·evidence v4 계약(`composite`·`categoryResults`·`methodResults` 최종 필드), v2 `detectionPolicy` 필드 세부(Detector 계약 Issue). 마이그레이션 DDL 문구는 PostgreSQL 세션
