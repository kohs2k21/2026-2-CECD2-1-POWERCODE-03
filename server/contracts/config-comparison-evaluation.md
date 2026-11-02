Created: 2026-10-21T00:00:00+09:00
Updated: 2026-11-02T20:00:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft

# 모델 버전 비교 평가 계약 v1 (초안, Issue #94 — #74 개정)

- 변경 요약(#94): 비교 평가·교체 추천을 **모델 버전 단위로 축소**(룰 세트는 비교 대상 아님 — 버전이 없음, 11/02 Q1). 비교 작업은 접수 시점 **룰 세트 리비전을 고정**해 모든 항목에 같은 룰을 적용(룰 차이가 결과에 섞이지 않음). `categoryResults`·`detectionPolicy`는 `hard/soft/ml`로, `detection-config-version.md` 참조는 [model-version.md](model-version.md)로. 10/21 **자동 비교 평가(§4.8)** 절 신설. PRD R1~R17은 모델 단위에 그대로 적용(아래 표). #74 본문의 "구성"·"구성 버전"은 모두 "모델 버전"으로 읽음(이 개정에서 치환 완료, 코드 현행 설명의 "구성"만 남김)
- 2차 개정(사용자 11/02 9.4 "교체 추천은 같은 종류 모델끼리"): 비교 자체(≤8)는 모델 종류 무관하게 허용하되 **교체 추천은 `modelKey`별로 산출**(`replacementRecommendations[]`), 기준선 = 같은 `modelKey`의 활성 버전(활성 집합 [model-version.md](model-version.md) 추천 전환 — 모델 1개 = 버전 계열·활성 집합 0..N, 사용자 최종 확인 대상). stale 기준은 활성 집합 리비전. `detectionPolicy`는 운영 설정 전역 값이 되어 비교 작업이 접수 시점 정책 리비전을 룰 세트와 함께 고정
- 계약 버전: `config-comparison-v1`(비교 작업), `evaluation-set-v1`(평가 세트). 근거 코드 origin/dev `7f24e03`(#74 인용 행 번호는 `872f540` 기준, 코드 변경 없음)
- 상위 결정: WebUI 화면 흐름 4절(하나의 불변 평가 세트 위 다중 모델 버전 비교, 현재 운영 버전 항상 포함, 종합 + 모델별·방법별 + 4종 가족·applicability), 플러그형 설계 방향 3절(모델별 개별 결과 + 구성 종합, 하나로 합산 금지), Q-C=A(세트가 지정한 스냅샷 train 분할 기준 생성·고정)
- 상태: 초안 — 사용자 결정 반영 완료(10/21 일괄, 설계 방향 4.7절), 구현은 #74 후속 구현 Issue 분할안(총괄 기록) 참조. 표기: **[확정]** 사용자 확정·코드 사실 / **충돌** 현행 코드·계약과 어긋나 구현 Issue에서 바꿀 항목(결정은 끝남)
- 범위: 평가 세트 엔티티(생성·고정·조회), 비교 작업(요청·검증·상태·결과), 적용 gate와의 관계, 기존 [합성 평가 계약](synthetic-evaluation.md) 변경 제안
- 제외: 종합 판정 조합 규칙(Detector 계약 — 비교 결과의 `composite`는 Detector 순수 함수 호출 결과를 집계만 함), 자동 교체·자동 승인, WebUI 화면, 새 합성 생성기 설계(기존 ESB P02~P04·ADBench 프로필만 재사용)
- 기존 규약 유지: 4종 평가 가족 봉투([JSON Schema](trainer-evaluation-envelope.schema.json))·measurement 규약·"미측정은 null+사유". `synthetic-evaluation.md`의 "합성만으로 운영 적용 검증 대체 금지"는 **[확정 10/21]** "평가 세트 비교 + 사람 수락"으로 대체 — 변경 제안 표 참조

## 평가 세트 `evaluation-set-v1`

- 정의: source 1개·스냅샷 1개·분할(purpose) 1개 위에 **선택적으로** 합성 행을 1회 생성해 **고정**한 불변 평가 모집단. 모델 버전과 무관하게 만들어지며 여러 모델 버전이 같은 세트로 평가됨 [확정 Q-C=A]
- 생성 기준: 합성 생성 통계(ESB P02/P03 어댑터 기준선·ADBench `fitScope: snapshot_train_only_max_1000`)는 **세트가 지정한 스냅샷의 train 분할**에서 계산. 평가 대상 모델 버전의 학습 스냅샷은 참조하지 않음 — **충돌**: 현행은 평가 요청 모델 버전의 학습 스냅샷 train(`synthetic_execution.py:181-182`, `synthetic-evaluation.md` "P02/P03: train 전용 어댑터 기준선", "생성 통계는 train만")
- 합성 없는 세트(원본 분할만)도 허용 [확정 10/21]: `synthetic: null`. 이 세트의 모델 버전별 평가 행은 현행 원본 평가와 같은 의미(gate 증거 가능)
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
- **현재 운영 모델 버전 자동 포함** [확정, 9.4로 기준 전환]: 접수 시 활성 집합을 읽어 **요청 후보들의 `modelKey`마다 같은 `modelKey`의 활성 버전**을 `role: "active"` 항목으로 추가(요청에 넣지 않음). 그 `modelKey`에 활성 버전이 없으면 그 `modelKey`의 기준선 없음(`baselines[]`에 항목 없음); 활성 집합이 비어 있으면 `baselines: []` [확정 10/21 활성 없음 허용]. 활성 버전이 `configVersionIds`에도 있으면 1개로 합침. 모델 종류가 다른 후보를 한 작업에 섞어도 됨(결과 표는 함께 보되 추천은 종류별)
- 상한 [확정 10/21] Q-L=A: 활성 포함 총 **≤ 8**, 초과 400 `comparison_too_many_configs`. 빈 목록(활성도 없음) 400 `comparison_configs_required`
- 검증(접수 시): UUID 형식·중복, 세트 `status=ready`·같은 source, 각 모델 버전 같은 source·존재·schemaVersion 3. 실행 가능 여부·겹침은 **항목별로** 기록하고 작업 전체는 접수(아래 항목 상태)
- `input_spec = {kind:'compare', sourceId, evaluationSetId, configVersionIds(활성 포함·정렬), baselines: [{modelKey, configVersionId}], activeModelSetRevision, ruleSetRevision, ruleSetContentHash, detectionPolicyRevision, detectionPolicy}`; 큐 payload `{schemaVersion: 1, jobId, enqueuedAt}` 그대로

### 실행 — 모델 버전별 평가

- Trainer가 모델 버전마다 현행 `_evaluate`와 같은 경로(`load_context` → 행마다 `compute`, `trainer/runner.py:458-531`)로 평가하되 모집단은 **세트의 고정 행**(원본 분할 행 + 합성 행). 모델·fit 재학습 없음, 운영 모델 버전 변경 없음. **활성 항목(들)을 항상 먼저 평가**(콜드 스타트 비용을 활성이 부담, `warmCold=mixed_unclassified` 보정; 활성이 여러 `modelKey`면 활성 집합 순서) [확정 10/21 R12]
- 항목 실패 사유(해당 모델 버전만 `failed`, 나머지 계속) [확정 10/21] Q-F=A

| 사유 코드 | 조건 |
|---|---|
| `method_not_executable:<methodId>` | 활성 방법이 저장 뒤 카탈로그 변경으로 사후 실행 불가가 됨 — 어댑터 소실(Detector D13 B 방어 경로). 저장 시에는 이미 거부되므로 드묾 |
| `training_evaluation_overlap` | 그 모델 버전의 활성 ML 후보 학습 train과 세트 행이 observation 또는 execution 단위로 겹침(현행 `synthetic_execution.py:183-193` 검사 재사용) |
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
  "baselines": [{"modelKey": "iforest", "configVersionId": "<active config UUID>", "role": "active"}],
  "activeModelSetRevision": 12,
  "ruleSet": {"revision": 13, "contentHash": "9c40…", "fixedAt": "accepted"},
  "detectionPolicy": {"revision": 2, "policy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}, "fixedAt": "accepted"},
  "manualApprovalRequired": true,
  "replacementRecommendations": [{
    "modelKey": "iforest",
    "ruleVersion": "replacement-recommendation-v1",
    "comparisonId": "<comparison UUID>", "comparisonJobId": "<job UUID>", "evaluationSetId": "<set UUID>",
    "baselineConfigVersionId": "<active config UUID>", "baselineEvaluationId": "<run_evaluations UUID>", "baselineActiveRevision": 12,
    "targetVersionId": "<config B UUID>", "targetEvaluationId": "<run_evaluations UUID>",
    "summary": "실험 7이 현재 운영 V4보다 모델 재현율 12/18(0.667) > 9/18(0.500), P04 sev1 모델 탐지율 12/18 > 10/18, 지연 P95 41ms(한도 내)",
    "reason": "better_than_active_on_all_basis_metrics",
    "noRecommendationReason": null,
    "comparedRowCount": 1188,
    "comparedLabelCounts": {"anomalyLabelCount": 18, "normalLabelCount": 0},
    "basis": [
      {"metric": "ml.completeOnly.recall", "family": "ml", "direction": "higher_is_better",
       "target": {"value": 0.667, "numerator": 12, "denominator": 18},
       "baseline": {"value": 0.5, "numerator": 9, "denominator": 18},
       "outcome": "better", "comparedTo": "<active config UUID>"},
      {"metric": "synthetic.scenario.P04.sev1.mlDetectionRate", "family": "synthetic", "direction": "higher_is_better",
       "target": {"value": 0.667, "numerator": 12, "denominator": 18},
       "baseline": {"value": 0.556, "numerator": 10, "denominator": 18},
       "outcome": "better", "comparedTo": "<active config UUID>"}
    ],
    "excludedMetrics": [
      {"metric": "ml.completeOnly.precision", "reason": "verified_normal_labels_unavailable"},
      {"metric": "ml.completeOnly.falsePositiveRate", "reason": "verified_normal_labels_unavailable"}
    ],
    "checks": {"itemStatus": "succeeded", "executable": true, "methodUnavailableCount": 0, "applicability": "valid", "costLimit": "passed"},
    "costs": [
      {"metric": "execution_cost.itemP95Ms", "target": 41.2, "baseline": 37.9, "limit": 75.8,
       "limitRule": "max(latencyFloorMs, baseline*latencyP95RelativeLimit) and deadlineMs*latencyDeadlineRatio", "outcome": "within_limit"}
    ],
    "candidates": [
      {"configVersionId": "<config B UUID>", "modelKey": "iforest", "outcome": "selected", "excludedReason": null, "lostAt": null}
    ],
    "references": [
      {"metric": "composite.completeOnly.recall", "target": 0.667, "baseline": 0.556, "note": "composite_reference_only"},
      {"metric": "rule_ml.hrBoth", "target": 0.12, "baseline": 0.10, "note": "checker_agreement_not_accuracy"},
      {"metric": "execution_cost.processRssBytes", "target": 412000000, "baseline": 409000000, "note": "shared_worker_process_sample"}
    ],
    "thresholds": {"minLabeledAnomalyCount": 10, "minScenarioDenominator": 10, "ratioEpsilon": 0.0,
                   "latencyP95RelativeLimit": 2.0, "latencyFloorMs": 50, "latencyDeadlineRatio": 0.5,
                   "costUnmeasuredPolicy": "allow", "staleAcceptPolicy": "reject",
                   "tieBreak": ["recall_tp", "fpr_fp", "precision", "scenario_min", "itemP95Ms", "created_at_asc"]},
    "generatedAt": "<UTC>"
  },
  {"modelKey": "eif", "ruleVersion": "replacement-recommendation-v1", "comparisonId": "<comparison UUID>", "comparisonJobId": "<job UUID>", "evaluationSetId": "<set UUID>",
   "baselineConfigVersionId": null, "baselineEvaluationId": null, "baselineActiveRevision": 12,
   "targetVersionId": null, "targetEvaluationId": null, "summary": "EIF 계열은 현재 운영 중인 버전이 없어 추천하지 않습니다",
   "reason": null, "noRecommendationReason": "active_missing", "comparedRowCount": null, "comparedLabelCounts": null,
   "basis": [], "excludedMetrics": [], "checks": null, "costs": [], "references": [],
   "candidates": [{"configVersionId": "<config C UUID>", "modelKey": "eif", "outcome": "excluded", "excludedReason": "item_failed:method_not_executable:ml:eif", "lostAt": null}],
   "thresholds": "<iforest 항목과 동일, 생략>", "generatedAt": "<UTC>"}],
  "items": [
    {"configVersionId": "<active config UUID>", "modelKey": "iforest", "role": "active", "status": "succeeded", "errorCode": null,
     "evaluationId": "<run_evaluations UUID>", "metricVersion": "comparison-methods-v1",
     "applicability": "unmeasured", "reasonCodes": ["selected_paths_unavailable", "synthetic_set_evaluation"],
     "counts": {"populationCount": 1200, "scoredCount": 1188, "unavailableCount": 12, "unlabeledCount": 1180},
     "composite": {
       "detectionPolicyRevision": 2,
       "labelCounts": {"anomalyLabelCount": 20, "normalLabelCount": 0, "unlabeledCount": 1180},
       "completeOnly": {"basis": "complete_only", "confusion": {"tp": 10, "fp": null, "tn": null, "fn": 8},
                        "precision": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "recall": {"value": 0.556, "unit": "ratio", "numerator": 10, "denominator": 18, "coverage": 0.9, "reason": null},
                        "falsePositiveRate": {"value": null, "unit": "ratio", "numerator": null, "denominator": null, "coverage": null, "reason": "verified_normal_labels_unavailable"},
                        "coverage": 0.99, "unavailableCount": 12, "hitCount": 31},
       "decidable": {"basis": "decidable", "confusion": {"tp": 11, "fp": null, "tn": null, "fn": 8}, "coverage": 0.995, "unavailableCount": 6,
                     "biasNote": "partial_true_included_none_excluded"}},
     "categoryResults": {
       "hard": {"measurementStatus": "measured", "ruleCount": 2, "confusion": {"tp": 7, "fp": null, "tn": null, "fn": 13}, "unavailableCount": 0},
       "soft": {"measurementStatus": "measured", "ruleCount": 1, "confusion": {"tp": 2, "fp": null, "tn": null, "fn": 18}, "unavailableCount": 0},
       "ml": {"measurementStatus": "measured", "methodCount": 1, "confusion": {"tp": 9, "fp": null, "tn": null, "fn": 9}, "unavailableCount": 12,
              "completeOnly": {"recall": {"value": 0.5, "numerator": 9, "denominator": 18}, "precision": {"value": null, "reason": "verified_normal_labels_unavailable"}, "falsePositiveRate": {"value": null, "reason": "verified_normal_labels_unavailable"}}}},
     "methodResults": [
       {"methodId": "hard:process-status", "category": "hard", "measurementStatus": "measured",
        "confusion": {"tp": 7, "fp": null, "tn": null, "fn": 13}, "hitCount": 7, "unavailableCount": 0, "unavailableReasons": {},
        "resultCounts": {"hit": 7, "no_hit": 1193, "unavailable": 0, "not_applicable": 0}},
       {"methodId": "hard:negative-duration", "category": "hard", "measurementStatus": "measured",
        "confusion": {"tp": 0, "fp": null, "tn": null, "fn": 20}, "hitCount": 0, "unavailableCount": 0, "unavailableReasons": {},
        "resultCounts": {"hit": 0, "no_hit": 1050, "unavailable": 0, "not_applicable": 150}, "notApplicableReasons": {"end_time_absent": 150}},
       {"methodId": "soft:elapsed_ms-3f9a2c1d", "category": "soft", "measurementStatus": "measured", "confusion": {"tp": 2, "fp": null, "tn": null, "fn": 18}, "hitCount": 2,
        "unavailableCount": 0, "resultCounts": {"hit": 2, "no_hit": 1048, "unavailable": 0, "not_applicable": 150}},
       {"methodId": "ml:iforest", "category": "ml", "methodVersion": "process-iforest-v1", "measurementStatus": "measured",
        "confusion": {"tp": 9, "fp": null, "tn": null, "fn": 9}, "scoreMin": 0.31, "scoreMax": 0.92,
        "completeOnly": {"recall": {"value": 0.5, "numerator": 9, "denominator": 18}, "precision": {"value": null, "reason": "verified_normal_labels_unavailable"}, "falsePositiveRate": {"value": null, "reason": "verified_normal_labels_unavailable"}},
        "threshold": {"direction": "higher_is_anomaly", "value": "0.61", "quantile": 0.99},
        "unavailableCount": 12, "unavailableReasons": {"model_features_unavailable": 12}}],
     "evaluationEnvelope": {"rule_ml": {}, "synthetic": {}, "execution_cost": {}, "operational": {}}},
    {"configVersionId": "<config B UUID>", "modelKey": "iforest", "role": "candidate", "status": "succeeded", "errorCode": null,
     "evaluationId": "<run_evaluations UUID>", "metricVersion": "comparison-methods-v1",
     "applicability": "valid", "reasonCodes": ["synthetic_set_evaluation"],
     "counts": {"populationCount": 1200, "scoredCount": 1200, "unavailableCount": 0, "unlabeledCount": 1180},
     "composite": {
       "detectionPolicyRevision": 2,
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
    {"configVersionId": "<config C UUID>", "modelKey": "eif", "role": "candidate", "status": "failed",
     "errorCode": "method_not_executable:ml:eif", "evaluationId": null}
  ]
}
```

- 예시 JSON의 `"<…생략>"`·`"<… UUID>"`는 **자리표시 문자열**이며 이 문서의 예시 JSON은 schema 검증 대상이 아님(구문 parse만 확인). 예시 수치 정합: active 항목 라벨 20행 중 ML이 unavailable인 라벨 행 2 → `ml` 혼동행렬 tp 9 + fn 9 = 18 = `composite.completeOnly` 분모(complete 행만), `hard`·`soft`는 unavailable 0이라 분모 20
- 혼동행렬 칸별 null 규칙(WebUI 검토 의견 8): `fp`·`tn`은 `normal` 라벨 행에서만 세므로(`trainer/runner.py:482-485, 522-531`) `labelCounts.normalLabelCount=0`이면 `fp`·`tn`은 0이 아니라 **null**, `precision`·`falsePositiveRate`도 null+`verified_normal_labels_unavailable`. 같은 식으로 `anomalyLabelCount=0`이면 `tp`·`fn`·`recall` null. **충돌**: 현행 `classification_metrics`(`runner.py:487-492`)는 fp=0으로 두고 `precision=tp/tp`를 계산하므로 `comparison-methods-v1`은 이 null 규칙을 새로 적용(기존 `selected-paths-v3` 결과 재해석 없음). `hitCount`는 라벨 무관 hit 수
- `composite`: Detector 순수 함수(종합 판정)의 행별 결과를 집계만 함. `completeOnly`(현행 기준, `trainer/runner.py:530-531`)와 `decidable`(None만 제외) **병기** — 예시 active 항목: 라벨 20행 중 complete 18행(tp 10·fn 8), partial+True 1행(decidable에서 tp 11), partial+None 1행(둘 다 제외). **[확정 Q7 C, 10/21]** `completeOnly`·`decidable` 두 기준을 각각 `coverage`(분모/전체)·`unavailableCount`와 함께 **항상 병기**(생략 분기 없음). 편향 방향은 `biasNote`로 표기. `completeOnly.hitCount` = 라벨 무관 종합 판정 true 행 수(세트 경보율·경보/일 추정 참고용) [확정 10/21 R11]
- `categoryResults{hard,soft,ml}`: 대분류 결과(Detector 1단계 결과, detection-decision.md(#92)) 기준 혼동행렬. `hard`·`soft`는 고정된 룰 세트 리비전의 켜진 룰 수(`ruleCount`), `ml`은 그 모델 버전의 방법 수(`methodCount`, 추천안에서는 항상 1 — 비교 항목은 모델 버전 1개씩이며 활성 집합 전체를 재현하지 않음). 켜진 룰 0개·방법 0개는 `measurementStatus: skipped`. **`hard`·`soft` 결과는 모든 항목에서 동일 입력·동일 룰이므로 항목 간 차이는 `ml`과 조합(`composite`)에서만 생김**. U(정상 여부 미확인, 대분류 결과 None) 행의 집계 기준은 composite와 같은 Q7 C: `completeOnly`(대분류 방법 전부 succeeded인 행만)·`decidable`(대분류 결과 None만 제외, T는 partial이어도 포함) 두 기준을 병기하고 각각 `coverage`·`unavailableCount`를 붙임
- v1 운영 모델 버전과 v3 후보를 같은 세트로 비교할 때 Detector 행동 변경 2(방법 단위 실행 후 등록 Rule 예외가 상태 HR 이상을 지우지 않음, Detector 문서 D4-1)로 드물게 v1·v3의 `composite`가 같은 입력에서 달라질 수 있음 — 차이는 `items[].metricVersion`·`detectionPolicy`(v2)로 구분하고 결과를 재해석하지 않음
- `methodResults[]`: 켜진 하드룰·소프트룰(고정 리비전)·활성 ML 방법마다 1개, 방법별 결과 형식(detection-decision.md(#92) evidence v4)의 상태 집계. 행의 `category`(`hard`/`soft`/`ml`)는 evidence v4 `methodResults[]` 필드명과 맞춤(#92 11.5). **비활성 방법·꺼진 룰은 배열에 넣지 않음**(#92 11.5)이므로 `measurementStatus`는 `measured` / `unavailable`만(`skipped` 없음 — 대분류 단위 skipped는 `categoryResults`에만). **룰 결과 값 집계 `resultCounts`**(hard·soft 행): evidence v4 `rules[].result` 값 목록 **`hit` / `no_hit` / `unavailable` / `not_applicable`**(Detector #92 11/02) 그대로 4칸을 셈 — **4칸 합 = 그 방법이 시도한 행 수 = 항목 `counts.populationCount`**(라벨 유무 무관, 예시 1200). **`not_applicable`은 정상(no hit, N)으로 집계** — 혼동행렬에서 `no_hit`과 같은 칸(예 `hard:negative-duration`의 END_TIME 없음 `end_time_absent`, 소프트룰 `elapsed_ms`의 END_TIME 누락 null — 둘 다 "이상 아님"), `unavailable`만 분모에서 빠짐. 사유별 건수는 `notApplicableReasons`(hard, Detector reason 값)·`unavailableReasons`. `measurementStatus`는 **집계 측정 상태**이며 evidence v4 `methodResults[].status` enum(`succeeded/not_ready/failed` — v4에 `skipped` 없음, #92 11.5)과 다른 필드(이름 충돌 방지, Detector 의견 9). 실행 불가 방법이 활성인 모델 버전은 항목 자체가 `failed`(실행 절 사유 표)이므로 성공 항목의 `methodResults`에 `method_not_executable` 결과는 생기지 않음(예시 2번째 항목). 라벨 있는 행만 혼동행렬, `unavailableReasons`는 사유별 건수. ML `score` 척도는 방법마다 다르므로 비교·합산하지 않음(표시만)
- `evaluationEnvelope`: 모델 버전별 4가족 봉투 그대로. `synthetic`은 세트 합성 행 기준 측정(`synthetic-process-metrics-v1` 재사용, 모델 버전별 분모 동일) — 봉투 내부는 `evaluate`와 같은 `details.trials[]{scenario, severity, repetition, status, metrics{hr,sr,ml,combined{injectedCount, detectedCount, unjudgedCount, detectionRate}}}` 형식 유지(키 `hr`·`sr`는 현행 `synthetic-process-metrics-v1`의 이름이며 기존 결과 변경 금지 원칙으로 그대로 둠 — `hr` = 룰(hard+soft) 조합 결과, `sr`는 SR 폐기로 항상 null+사유; 새 metricVersion에서 `hard/soft` 개명 검토, `synthetic_execution.py:111-112`; 추천 규칙 (b)의 시나리오별 탐지율 입력 전제), `rule_ml`은 세트 원본 행 교차표(`rule-ml-comparison-v1`), `execution_cost`는 모델 버전별 실측, `operational`은 `unavailable/operational_report_separate_time_scoped`. ESB와 ADBench 분모 합산 금지 유지
- 묶음 수준 지표 요약은 두지 않음(모델 버전별 봉투를 하나로 합치지 않음) [확정 3절]. 순위 표시 없음, `manualApprovalRequired: true`
- `replacementRecommendations[]` [확정 — 사용자 10/21, 설계 방향 4.4절·WebUI 화면흐름 2.4절; 9.4 "같은 종류끼리"로 modelKey별 분할]: **교체는 시스템 '추천'만 자동, 운영 변경은 사람 '수락'으로만. 자동 교체 금지**. 비교 작업마다 **작업에 등장한 `modelKey`마다 추천 1건(또는 없음)**을 계산해 배열로 결과에 포함(활성·후보 항목의 `modelKey` 합집합, `modelKey` 사전순). 이전 초안의 단일 `replacementRecommendation` 객체는 이 배열로 대체(저장된 운영 결과 없음). 아래 규칙은 **배열 원소 1개(= `modelKey` 1개) 안에서** 적용되며 "활성"은 그 `modelKey`의 활성 버전, "후보"는 그 `modelKey`의 후보만
  - 규칙 버전 `replacement-recommendation-v1` **[확정 10/21 — 사용자 "A~C 추천안대로", 설계 방향 4.7절; 10/21 운영 버전 교체 추천 규칙 기획 R1~R17 전부 추천값 채택]**. 입력은 비교 작업 1건의 `resultSummary`만(다른 작업·운영 DB 조회 없음). 비교 전에 **활성·후보 양쪽 모두 complete인 행의 교집합**으로 라벨·주입 분모를 다시 세고(R16) `comparedRowCount`·`comparedLabelCounts`에 기록 — 활성 모델 버전이 세트 일부 행에서 unavailable이어도 추천 가능
  - (a) 후보 자격(순서대로 첫 실패 사유를 `candidates[].excludedReason`에 기록): ⓪ 후보의 `modelKey`가 이 원소의 `modelKey`와 같음(**다른 `modelKey`는 이 원소의 `candidates[]`에 넣지 않음** — 종류가 다른 모델끼리는 추천하지 않음, 9.4 R18) ① 항목 `status=succeeded`(`item_failed:<errorCode>`) ② 버전 `executable=true`(`not_executable`, 사후 어댑터 소실 방어) ③ 교집합 행 밖에서도 모든 `methodResults[].unavailableCount=0`은 요구하지 않음 — 교집합 행에서만 비교(R16) ④ `applicability=valid`(`applicability_<value>`) ⑤ `operationalEligibility.eligible=true`(`operational_ineligible`). 활성 항목은 `status=succeeded`여야 함(`active_item_failed`)
  - (b) 1차 지표 — **모델 단위 지표만** [확정 Detector 11/02 Q-E4]: 해당 `modelKey` 방법 결과(`methodResults[ml:<modelKey>]`, 추천안에서는 `categoryResults.ml`과 동일) 기준. 비교 집합 `P` = 활성·후보 모두 `value≠null`인 것: `ml.completeOnly.recall`(분모 동일 → 분자 `tp` 정수 비교), `ml.completeOnly.precision`(비율 비교, `ratioEpsilon` 안이면 동등), `ml.completeOnly.falsePositiveRate`(분자 `fp` 정수 비교), `synthetic.scenario.<scenario>.sev<severity>.mlDetectionRate`(봉투 `details.trials[].metrics.ml`의 같은 (scenario, severity) 모든 repetition 합산 Σdetected/Σinjected, 분자 정수 비교, 합산 분모 < `minScenarioDenominator`면 제외 `insufficient_denominator`, `status≠generated` trial은 양쪽 제외) [확정 R1·R2, 입력을 모델 단위로 전환]. **`composite`(종합 판정) 지표는 추천 근거가 아니라 `references[]` 참고 표시만**(`composite_reference_only`) — 룰 세트·정책·다른 종류 모델이 섞인 결과라 모델 교체의 효과를 분리하지 못하기 때문. anomaly 라벨 < `minLabeledAnomalyCount`면 recall 제외(R3). 한쪽이라도 null인 지표는 `excludedMetrics[]`에 `{metric, reason}`로 기록. **지배 규칙**: `P` 전부 같거나 우위 + 하나 이상 엄격 우위 → "활성보다 나음"; 하나라도 열위 `worse_on:<metric>`, 전부 동등 `equal_to_active`. 합산 점수·가중 평균은 만들지 않음(설계 방향 3절)
  - (c) 2차 — 실행 비용 한도(우위 요구 아님, 초과만 금지): 후보 `execution_cost.itemP95Ms` ≤ max(`latencyFloorMs`, 활성 × `latencyP95RelativeLimit`) **이고** ≤ 접수 시 고정한 운영 설정 `detectionPolicy.deadlineMs` × `latencyDeadlineRatio`(R5; 정책이 전역 값이 되어 모델 버전별 값이 아님). 활성 값 null이면 절대 한도만; 후보 값 null이면 `checks.costLimit="unmeasured"`로 기록하고 추천 허용(R6, `costUnmeasuredPolicy=allow`). `processRssBytes`·학습 시간은 한도 없이 참고(`references[]`, R7 — 비교 작업은 한 워커가 순차 평가하므로 RSS가 모델 버전 귀속 값이 아님). 초과 후보 `cost_limit_exceeded:itemP95Ms`
  - (d) 참고 지표는 `references[]`에만: `composite.completeOnly.*`·`composite.decidable`(`composite_reference_only`), `rule_ml` 4셀(`checker_agreement_not_accuracy`), `methodResults[].hitCount`, `categoryResults.hard/soft` 혼동행렬, `execution_cost.{totalMs, throughput, processRssBytes}`. 운영 보고(`/api/pipeline/reports/operational`)는 결과에 복사하지 않고 화면이 별도 호출
  - (e) 다수 후보 선택 — 사전식 6단계(R8, `thresholds.tieBreak`): 1 recall 분자 `tp` 큰 쪽 → 2 `fp` 작은 쪽(정의 시) → 3 precision 큰 쪽(정의 시, `ratioEpsilon` 밖) → 4 시나리오 탐지율 **최솟값** 큰 쪽 → 5 `itemP95Ms` 작은 쪽(둘 다 측정 시) → 6 `createdAt` **빠른** 쪽. 후보끼리 지배 관계는 요구하지 않음. 선택되지 않은 적격 후보는 `candidates[].outcome=eligible_not_selected`·`lostAt`(1..6)
  - 추천 없음(`targetVersionId=null`) 사유 9종, 먼저 걸리는 순서(R15 화면 문구는 기획 문서 3절): `active_missing`(그 `modelKey`의 활성 버전 없음 — 활성 집합이 비었거나 다른 종류만 운영 중; 첫 활성화는 사람이 직접 `apply`) → `active_item_failed` → `active_results_unavailable`(교집합 행 0) → `no_candidates` → `all_candidates_failed` → `all_candidates_ineligible` → `basis_metrics_unavailable`(`P` 공집합) → `no_candidate_better_than_active` → `cost_limit_exceeded`. 추천 없음이어도 `candidates[]`는 항상 채움
  - 필드: 기존 `targetVersionId, basis[], checks, reason, noRecommendationReason, generatedAt` 유지 + `modelKey, ruleVersion, comparisonId, comparisonJobId, evaluationSetId, baselineConfigVersionId, baselineEvaluationId, baselineActiveRevision(활성 집합 리비전), targetEvaluationId, summary, comparedRowCount, comparedLabelCounts, excludedMetrics[], costs[], candidates[](각 `modelKey` 포함), references[], thresholds` 추가. **기존 필드 형태 변경**(R17 [확정]): `basis[].target`·`baseline`은 스칼라 → `{value, numerator, denominator}` 객체, 행에 `family`·`direction`·`outcome` 추가(호환 원칙의 예외로 사용자 확정; 이 초안 안에서만 바뀐 것이며 운영 결과는 아직 없음). `basis[].metric` 키 형식 `ml.completeOnly.<name>`(= 해당 `modelKey` 방법 결과) / `synthetic.scenario.<s>.sev<n>.mlDetectionRate` / `execution_cost.<name>`; `references[].metric`에만 `composite.<basis>.<name>` / `rule_ml.<name>` / `synthetic.scenario.<s>.sev<n>.combinedDetectionRate`
  - `summary`는 **서버(Trainer)가 한글 1줄 생성**(R13): "{대상 displayName}이 현재 운영 {활성 displayName}보다 " + basis 행마다 "{지표 한글명} {target} > {baseline}" + ", 지연 P95 {ms}ms(한도 내)"; 추천 없음이면 사유 문구. 임계값(`thresholds`)은 **Trainer 설정**(환경변수 `TRAINER_RECOMMENDATION_*` 또는 설정 모듈 상수, R14)이며 사용값을 결과에 기록. 기본값: `minLabeledAnomalyCount 10`, `minScenarioDenominator 10`, `ratioEpsilon 0.0`, `latencyP95RelativeLimit 2.0`, `latencyFloorMs 50`, `latencyDeadlineRatio 0.5`, `costUnmeasuredPolicy allow`, `staleAcceptPolicy reject`(R3~R6 [확정])
  - 계산 시점·불변: Trainer가 모든 항목 평가 직후, 항목 `run_evaluations` 행·비교 묶음·job 성공·queue ack와 **같은 트랜잭션**에서 1회 계산해 `run_config_comparisons.summary`에 저장. API는 저장값 반환(재계산 없음). 카탈로그·임계가 뒤에 바뀌어도 과거 추천은 다시 쓰지 않음(`thresholds`·`ruleVersion`을 결과에 박는 이유); `executable`만 수락 시점에 gate가 다시 검사
  - stale(R9 [확정], 범위는 **같은 `modelKey`의 활성 변경 + 평가 세트/데이터 변경**으로 확정 — Detector 11/02 Q-E4; 추천 근거가 모델 단위 지표뿐이라 다른 종류 모델의 활성 변경은 추천 유효성에 영향 없음): 저장값은 그대로 두고 조회 시 파생 필드 `recommendationState` — `applied`(`targetVersionId`가 현재 활성 집합에 포함) / `current`(그 `modelKey`의 현재 활성 버전 == `baselineConfigVersionId` **이고** 평가 세트 `status=ready`) / `stale`(그 외 — 같은 `modelKey`가 다른 버전으로 교체·빼기됨(활성 없음 포함), 또는 평가 세트가 `ready`가 아님; 다른 `modelKey`의 활성화·빼기만으로는 stale **아님**) / `none`(`targetVersionId` null). `baselineActiveRevision`은 기록용(판정에 쓰지 않음). stale 상태의 수락 요청은 400 `accepted_recommendation_stale`(R10 [확정])
  - 추천 ⊂ gate: 자격 조건은 gate 중 결과만으로 확인 가능한 부분. 추천이 있어도 gate가 거부할 수 있고(산출물 hash 불일치·수락 시점 실행 불가), gate를 통과하는 버전이 추천되지 않을 수도 있음(열위·비용 초과) — 둘 다 정상
  - 수락 경로: 사람이 [수락하고 교체 요청] → `POST /api/configurations/{targetVersionId}/applications` `{operation:"replace", evaluationId: targetEvaluationId, expectedRevision: <화면이 조회한 현재 활성 집합 revision — baselineActiveRevision이 아님(다른 modelKey 변경으로 올라 있어도 추천은 유효)>, expectedActiveVersionId: baselineConfigVersionId, acceptedRecommendation:{comparisonJobId, modelKey, targetVersionId}}`([model-version](model-version.md) 활성 집합 절 — 같은 `modelKey`의 활성 버전과 교환). 추천을 따르지 않는 교체·추천 없음 상태의 교체·활성화(`apply`)·빼기(`retire`)는 `acceptedRecommendation` 없이 기존 요청 그대로 허용. 실패 시 현행대로 활성 집합 유지
- 같은 세트 위 모델 버전별 행은 `run_evaluations.evaluation_spec_hash`가 같으므로 기존 비교 인덱스 `(snapshot_id, evaluation_spec_hash, purpose)`(`002_core_erd.sql:183`)로 조회 가능

### 조회 API

- `GET /api/comparisons?sourceId=&evaluationSetId=&modelKey=&limit=&cursor=` [확정 10/21 N7, 9.4로 형식 전환]: `{items: [{comparisonId, jobId, evaluationSetId, baselines: [{modelKey, configVersionId}], activeModelSetRevision, status, itemCount, succeededCount, failedCount, completedAt, recommendations: [{modelKey, targetVersionId, state}]}], pagination}`
- `GET /api/comparisons/:id`: 위 `resultSummary` 전체(완료 전에는 `status`·접수 항목만) + 파생 필드 `replacementRecommendations[].recommendationState`(`current|applied|stale|none`, 조회 시 현재 활성 집합과 비교해 계산, 저장하지 않음)
- 모델 버전 목록 `latestComparison`([model-version](model-version.md) 조회 절)은 이 테이블에서 계산하며 그 버전의 `modelKey`에 해당하는 `recommendation: {modelKey, targetVersionId, state}` 요약 포함

### 저장 — 마이그레이션 [확정 10/21]

| 변경 | 내용 |
|---|---|
| `job_kind` enum | `evaluation_set`, `compare` 추가(`schema.dbml:21-30`) |
| `run_evaluations` | `comparison_id uuid NULL → run_config_comparisons`, `evaluation_set_id uuid NULL → data_evaluation_sets` 열 추가. **충돌**: `job_id UNIQUE`(`002:172`)는 비교 작업 1개가 구성 N개 행을 만들면 위반 → `UNIQUE(job_id)` → `UNIQUE(job_id, config_version_id)`로 완화 **[확정 10/21 N6, 설계 방향 4.7절]**(대안이던 자식 `evaluate` 작업 + `run_jobs.parent_job_id`는 채택하지 않음). 마이그레이션 DDL은 PostgreSQL 세션 |
| `run_config_comparisons` | `id PK, job_id UNIQUE, evaluation_set_id, source_id, baselines jsonb([{modelKey, configVersionId}], 추천안) 또는 baseline_config_version_id NULL(대안), active_model_set_revision bigint, rule_set_revision bigint, detection_policy_revision bigint, status, summary jsonb, completed_at`. `summary`에 `replacementRecommendations[]`(`replacement-recommendation-v1`, modelKey별) 포함 — 항목 평가 행과 같은 트랜잭션에서 1회 저장·불변, stale은 저장하지 않고 조회 시 파생 |
| `run_config_comparison_items` | `comparison_id, config_version_id, model_key text, role CHECK(active|candidate), status CHECK(succeeded|failed), error_code, evaluation_id NULL → run_evaluations; PK (comparison_id, config_version_id)` |
| `data_evaluation_sets` | 위 세트 절 |
| 004 결과 검증 트리거 | `kind='evaluate'`에만 걸린 조건이 있으면 `compare`에 확장(`004_erd_v03_contracts.sql:223-228` 확인 범위에는 evaluate 전용 조건 없음, detect·LLM만) |

- 기존 `evaluate` 작업·`run_evaluations` 행·`metrics` 형식은 변경 없음(새 열은 NULL)

## 적용 gate와의 관계 [확정 — 사용자 10/21, 설계 방향 4.4절]

- 최종 관문은 **사람의 수락**. gate는 **기술 무결성만** 검사: 버전 `executable=true`(실행 불가 방법 없음), 산출물 경로·hash 검증(`applications.prepare_config`), 평가 행의 `scored_count>0 AND unavailable_count=0`(모든 행 complete·방법 결과 unavailable 0), prepared 계보 아님
- "원본 validation 평가 필수"는 **"평가 세트 비교(합성 행 포함 허용) + 사람 수락"으로 대체** [확정]. 적용 요청 `evaluationId`에는 비교 작업이 만든 모델 버전별 `run_evaluations` 행(세트 `purpose=validation`)을 허용하며, 세트에 합성 행이 있어도 기술 무결성을 만족하면 `applicability='valid'`
  - **충돌(구현 Issue)**: 현행은 합성 평가를 `applicability='unmeasured'`·`synthetic_not_operational_validation`으로 강제(`trainer/runner.py:571-574`). `comparison-methods-v1`에서는 합성 행 포함 여부를 `reasonCodes`에 `synthetic_set_evaluation`(정보성)으로만 남기고 applicability는 기술 무결성으로 판단. 기존 `evaluate`(`synthetic-method-v1`) 결과는 재해석하지 않음
  - `synthetic-evaluation.md` "합성 측정 완료만으로 운영 적용의 원본 validation 검증을 대체하지 않음"은 변경 제안 표에 [확정 근거] 표기(아래)
- gate 조건 SQL(`configRoutes.ts:90-99`, `applications.py:102-109`)은 형식상 불변(`purpose='validation' AND applicability='valid' AND scored_count>0 AND unavailable_count=0`); 바뀌는 것은 Trainer가 비교 행의 applicability를 정하는 규칙과 `executable` 검사 추가
- `purpose=test` 세트의 행은 gate 증거로 쓰지 않음(현행 유지). 기존 `evaluate` 원본 validation 행도 계속 유효
- 자동 교체 금지. 추천(`replacementRecommendations[]`)은 화면 표시·수락 입력일 뿐 적용을 일으키지 않음

## `synthetic-evaluation.md` 변경 제안(수정하지 않음, 문장 목록)

| 위치 | 현재 문장 | 제안 |
|---|---|---|
| 범위·의미 1항 | "기존 `POST /api/jobs`의 `kind=evaluate` 선택 확장" | "`kind=evaluate` 선택 확장 **및** `kind=evaluation_set`(세트 생성)·`kind=compare`(세트 위 모델 버전 비교)의 합성 행 생성 규약" 추가 |
| 범위·의미 2항 | "기존 IF·피처 순서·train 전용 fit·임계값 고정; 합성 평가 중 모델/fit 재학습·운영 모델 버전 변경 없음" | "IF"를 "활성 ML 방법 0..N"으로 일반화, 나머지 유지 |
| 지원 프로필 표 아래 "P02/P03: train 전용 어댑터 기준선…" | 기준선 출처 미명시(현행 구현은 평가 구성의 학습 스냅샷) | "기준선은 **평가 세트가 지정한 스냅샷의 train 분할**에서 계산(세트 생성 시 1회)" 명시 |
| 평가 요청 절 "현재 실행 상한: 평가 cohort 2000행…" | 작업당 상한 | 세트당 상한으로 같은 값 적용, 비교 작업은 세트 행 수를 그대로 사용(재생성 없음) 추가 |
| 평가 요청 절 "snapshot 분할·회원/원본 행·선정 정책 불변; 생성 통계는 train만, 변형은 선택 validation/test 복사본만" | 구성 학습 스냅샷 기준 | "생성 통계는 **세트 스냅샷** train만" 으로 명시 |
| 결과·재현 절 "합성 산출물은 `evaluations/<job UUID>/<content SHA>.json`" | 작업별 저장 | "세트 산출물은 `evaluation-sets/<setId>/<content SHA>.json`, 작업별 평가 산출물은 기존 경로" 추가 |
| 결과·재현 절 "SR 실제 미연결은 null+사유" | SR 자리 고정 | #94: 통계 SR 폐기 — `sr*` 셀은 `not_applicable/sr_removed`, 룰 결과는 `hard`·`soft`로 표기, 실행 불가 ML 방법은 `method_not_executable` |
| 결과·재현 절 마지막 "합성 측정 완료만으로 운영 적용의 원본 validation 검증을 대체하지 않음" | 합성 평가는 gate 불통 | **[확정 근거: 사용자 10/21, 설계 방향 4.4절]** "평가 세트 비교(합성 행 포함 허용)의 기술 무결성 검사 + 사람 수락이 운영 적용 관문이며, 자동 교체는 금지"로 교체. `runner.py:571-574` 강제 unmeasured는 `evaluate` 경로에만 남김 |
| 제외 절 "최종 시나리오/모델 선정" | 유지 | "비교 결과의 자동 순위·자동 선정" 추가; "교체 추천(`replacementRecommendations[]`, modelKey별)은 선정이 아니라 수락 대기 제안" 보완 |

- `trainer-service.md` 영향: 완료 결과 절에 `kind=compare`의 `resultSummary` 형식 링크 추가("`replacementRecommendations[]`(modelKey별)은 `replacement-recommendation-v1` 규칙으로 완료 시 1회 계산·불변" 포함), `queues.md` Trainer 행에 `evaluation_set`·`compare` 추가

## 룰 세트와의 관계 [제안, #94]

- 비교 대상은 **모델 버전만**([model-version.md](model-version.md)). 룰 세트([rule-set.md](rule-set.md))는 버전이 없으므로 비교·교체·복원 대상이 아님 [확정 11/02 Q1]
- 비교 작업은 **접수 시점의 룰 세트 리비전과 운영 설정(`detectionPolicy`) 리비전을 고정**(`input_spec.ruleSetRevision`·`ruleSetContentHash`·`detectionPolicyRevision`·`detectionPolicy`)하고 모든 항목을 그 룰·정책으로 평가. 실행 중 룰·정책이 바뀌어도 작업은 접수 시점 스냅샷(`GET /api/rule-set/history/:revision`·`GET /api/detection-policy/history/:revision`)을 사용. 결과 `resultSummary.ruleSet = {revision, contentHash, fixedAt: "accepted"}`·`resultSummary.detectionPolicy = {revision, policy, fixedAt: "accepted"}`. 항목의 `composite`는 그 모델 버전 1개 + 고정 룰 세트 + 고정 정책으로 계산(활성 집합의 다른 종류 모델은 넣지 않음 — "모델 교체가 종합 판정에 주는 차이"를 종류 안에서 봄)
- 효과: `categoryResults.hard`·`soft`·`methodResults[]`의 룰 행은 모든 항목에서 같은 값 → 항목 간 차이는 ML 결과와 조합(`composite`)에서만. 추천 규칙(아래 R 표)은 그대로 적용되며 "모델 교체가 종합 판정에 주는 차이"를 비교하는 의미가 됨
- 조회 시 파생 `ruleSetCurrent: boolean`(현재 룰 리비전 == 고정 리비전)·`detectionPolicyCurrent: boolean`. **false여도 `recommendationState`는 stale로 바꾸지 않음**(모델 간 상대 우위는 룰·정책 변경과 대체로 독립). 화면은 "비교 당시 룰 r13·정책 r2, 현재 r15·r3" 안내만 — 결정 질문 Q-E1
- 평가 세트 생성(`evaluation_set`)은 룰과 무관(모집단·합성 정답만)

## PRD R1~R17 — 모델 단위 적용 여부 [제안, #94]

10/21 운영 버전 교체 추천 규칙 기획(R1~R17 전부 채택, R16은 대안). 기획의 "구성 버전"을 "모델 버전"으로 읽으면 규칙·임계·사유 코드 전부 유효.

| 항목 | 유지/삭제 | 비고 |
|---|---|---|
| R1 시나리오·심각도별 탐지율 1차 지표(**ML 탐지율** `mlDetectionRate`), R2 지배 규칙·분자 정수 비교(**`ml.completeOnly.*`** 입력), R3 최소 표본, R4 `ratioEpsilon`, R5 지연 한도, R6 비용 미측정 허용, R7 RSS·학습 시간 참고, R8 사전식 6단계, R9 stale(**같은 `modelKey` 활성 변경·평가 세트 변경**), R10 stale 수락 400, R11 `hitCount`, R12 활성 첫 평가, R13 서버 한글 summary, R14 Trainer 설정 임계, R15 사유 9종 문구, R16 교집합 분모, R17 `basis[]` 객체화 | **유지** | 모델 버전 단위로 그대로. R1·R2 입력을 composite → 모델 단위 지표로, R9 범위를 같은 `modelKey`로 전환(Detector 11/02 Q-E4) |
| **R18(신규, 사용자 11/02 9.4)** 교체 추천은 **같은 `modelKey`끼리만** — 기준선 = 같은 `modelKey`의 활성 버전, 후보도 같은 `modelKey`만, 추천은 `modelKey`별 1건 | **추가** | 비교 작업 자체는 종류 혼합 허용(≤8). 같은 `modelKey` 활성 버전이 없으면 그 종류는 `active_missing` |
| 사유 코드·예시의 `method_not_executable:sr:ewma` | 유지(예시만 `ml:eif`로) | SR 폐기로 실행 불가 사례는 ML 어댑터 소실뿐 |
| `references[]`의 `rule_ml` 4셀·`categoryResults` 혼동행렬 | 유지 | `rule_ml`의 "rule"은 hard+soft 조합 결과로 읽음(기존 metricVersion 이름 유지), `categoryResults`는 `hard/soft/ml` |
| 활성 모델 버전 없음(`active_missing`) | 유지, `modelKey` 단위로 | 그 종류의 활성 버전이 없으면(룰만 운영 중이거나 다른 종류만 운영 중) 추천 없음·후보 표만. 첫 활성화(`apply`)는 사람이 직접 선택 |
| 삭제 항목 | **없음** | 룰 세트 관련 추천 규칙은 원래 없었음 |

## 자동 비교 평가 — 10/21 §4.8 [제안, #94]

- 트리거: **새 모델 버전 생성 성공** — train 작업 성공(`createdFrom.kind=train`; 조합 저장 `compose`는 보류([model-version.md](model-version.md) Q-M5) — 대안 채택 시 `201`도 트리거). `duplicateOf`가 있는 저장(200)·버전 `executable=false`·`operationalEligibility.eligible=false`(prepared)는 생략하고 사유 기록
- 접수 주체·시점: API가 생성 결과 확정 **뒤** 별도 트랜잭션으로 `POST /api/jobs kind=compare`와 같은 경로로 접수(요청 관리자 승계 — `run_jobs_v03_contract` 분기 유지, [queues.md](queues.md) 변경 제안 절). `input_spec.auto = true`, `Idempotency-Key = auto-compare:<configVersionId>:<evaluationSetId>`(중복 실행 방지; 같은 버전·같은 세트는 1회)
- 비교 대상: 새 버전 + **같은 `modelKey`의 현재 활성 버전**(자동 포함 규칙 그대로, 9.4). 그 종류의 활성이 없으면 새 버전만(추천 없음 `active_missing`, 결과 표는 제공). 다른 종류의 활성 버전은 자동 비교에 넣지 않음(사람이 수동 비교로 섞을 수 있음)
- 기본 평가 세트 선택 순서(같은 `modelKey` 계열을 같은 세트로 이어 보도록 조정): ① 학습 요청의 `evaluationSetId` ② source의 기본 세트(`ops_sources` 설정 가칭 `default_evaluation_set_id`, 관리자가 세트 목록에서 [기본으로 지정]) ③ **같은 `modelKey` 활성 버전의 최근 비교(`latestComparison`)가 쓴 세트**(`status=ready`) ④ 같은 snapshot·`purpose=validation`·`status=ready`인 가장 최근 세트 ⑤ 없으면 **생략** + `latestComparison: {auto: true, status: "skipped", reason: "no_default_evaluation_set"}` (세트를 자동 생성하지 않음 — 합성 생성은 사람의 선택)
- 겹침: 새 버전의 학습 train이 세트 행과 겹치면 그 항목만 `training_evaluation_overlap`(현행 규칙). 세트 ④ 선택 시 같은 snapshot이면 분할이 서로소라 구조적으로 안 겹침(10/28 5.4); ③은 다른 snapshot일 수 있어 겹침 검사가 실제로 걸릴 수 있음
- 표시: 모델 버전 목록 `latestComparison.auto`, 작업 목록 `kind=compare`·`auto` 필터. 완료 알림·배지는 WebUI(`notifications.md`) — 자동 교체는 여전히 없음, 추천 계산은 수동 비교와 동일
- 실패: 접수 실패는 생성 결과에 영향 없음(`failed_to_enqueue` 표시, 사람이 [+ 새 비교 평가]로 재시도). 실행 실패는 일반 compare와 동일
- 수동 경로 [+ 새 비교 평가]는 그대로 유지 [확정 10/21]

## 확정 요약·남은 의존

- 확정(11/02): 비교·추천은 모델 버전 단위, 룰 세트 리비전 고정·결과 기록, 자동 비교 평가 신설(세부는 제안), 9.4 교체 추천은 같은 종류(modelKey)끼리
- 추천 전환(이 개정, [model-version.md](model-version.md) Q-M1 귀결 — 사용자 최종 확인 대상): 기준선 `baselines[]`(modelKey별)·`replacementRecommendations[]`·`items[].modelKey`·stale 기준 활성 집합 리비전·정책 리비전 고정·자동 비교 상대 = 같은 modelKey 활성·세트 선택 ③ 추가
- 결정 질문: Q-E1 룰·정책 리비전 변경 시 추천 stale 처리(제안: stale 아님, 안내만) / Q-E2 기본 세트 선택 순서 ②③④·세트 없을 때 생략 / Q-E3 자동 접수 멱등키 형식 / **Q-E4 확정(Detector 11/02)**: 추천 근거는 모델 단위 지표만(composite는 참고), stale 범위 = 같은 `modelKey` 활성 변경 + 평가 세트/데이터 변경(다른 `modelKey` 변경은 stale 아님) / **Q-E5** `replacementRecommendations[]` 배열(modelKey별) vs 단일 객체 + `modelKey` 필터(제안: 배열 — 한 작업에 종류가 섞일 수 있음) / **Q-E6** 비교 항목은 모델 버전 1개(n_ml = 1)인데 접수 시 고정한 운영 설정이 `k.ml ≥ 2`면 Detector 규칙(Q-M8, k > n은 fail-closed)대로는 `composite`를 계산할 수 없음 — (i) **추천**: 비교 계산에서만 `k.ml`을 1로 고정해 `composite` 참고값을 산출하고 항목·결과에 `policyAdjusted: true`(+ 적용한 정책 값) 표시 (ii) 대안: `composite` 생략 + 사유 코드(예 `composite_skipped:policy_k_ml_exceeds_item_models`). (i)의 근거: `composite`는 추천 근거가 아닌 참고 표시(Q-E4)이므로 근사값이어도 유용하고, 운영 정책 저장은 바꾸지 않음
- 확정(10/21, 설계 방향 4.4~4.7절): Q-C 세트 생성 기준 A·합성 없는 세트 허용(N5) / Q-E 기술 무결성 gate + 세트 비교 + 사람 수락, 자동 교체 금지 / Q-F 겹침 항목 failed / Q-L 모델 버전 ≤8·세트 불변 / N5 활성 없음 비교 허용·prepared 금지 / N6 `run_evaluations` UNIQUE 완화 / N7 kind·경로 이름 / N15 `comparison-methods-v1` / Detector Q7 C 병기 / 교체 추천 규칙 R1~R17(`replacement-recommendation-v1`)
- 남은 의존(결정 아님): Detector 조합 순수 함수·evidence v4 계약(`composite`·`categoryResults`·`methodResults` 최종 필드), v2 `detectionPolicy` 필드 세부(Detector 계약 Issue). 마이그레이션 DDL 문구는 PostgreSQL 세션
