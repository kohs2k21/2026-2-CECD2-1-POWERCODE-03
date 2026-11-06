# Trainer 서비스 계약 v1

- 계약 버전: `trainer-service-v1`; 기존 `/api/jobs` 요청·상태·결과 필드 유지
- 실행: API → PostgreSQL training queue → Trainer → 불변 후보/평가 → API 조회
- 외부 완성 가공 CSV·합성 주입 CSV: [prepared-csv-v1](prepared-csv-import.md)의 관리자 반입·model_input·snapshotId 계약 적용; SQL/SQLite·raw 모델 호환 추정·운영 적용 제외
- 학습 성공: 후보 생성만; 활성 버전 변경은 기존 관리자 승인·Detector 적용 경로
- 실행 상태: queued/running/succeeded/failed; 자동 재시도는 같은 job, 최대 3회·attempt 이력
- terminal failed 재실행: 새 idempotency key로 명시적 새 요청; 같은 key·같은 본문은 기존 job 반환, 다른 본문은 409
- Web 멱등키: sessionStorage 정상 시 새로고침 후 재사용, 저장 실패 시 현재 페이지의 메모리에서만 유지; 저장 실패 후 새로고침한 경우 작업 이력 확인 후 재실행 여부 결정
- 현행 갱신(Issue #128, 11/05): API가 카탈로그를 읽어 `methodCatalog`·`models`를 노출하고 `modelKey`·`parameters`를 접수 단계에서 검증한다(요청·발견 API 절). `excludedIntervals`·`esb_occurred_at`은 워커 지원 전이라 접수하지 않는다([models-ui.md](models-ui.md))
- 현행 갱신(Issue #116, 11/04 — #111 병합 반영): 워커의 카탈로그 기준 `modelKey`·`parameters` 검증과 오류 코드(요청·발견 API 절), `sr*` 셀 영구 `not_applicable`/`sr_removed`(완료 결과 절), `training_settings` 확장(학습·전처리 절). API 쪽 선차단 전환·`parameters` 전달은 API 구현 단계 몫 [제안]

## 요청·발견 API

- `GET /api/jobs/capabilities`: `contractVersion`, `methodCatalog`(`contracts/method-catalog.json` 전체, #128), `models[{key,displayName,supported}]`(`methods[]`에서 파생, `supported := executable.process`), `evaluationFamilies`, `fittedFeatures`, `snapshot`, `training.excludedIntervals`(학습 제외 구간 접수 가능 여부, #128 추가. 지금 false)
- 모델 — API 현행(`jobRoutes.ts`, #128): 접수 단계에서 워커와 같은 카탈로그·같은 코드로 검증한다 — 카탈로그에 없는 키 422 `unsupported_model`, 실행 불가 422 `method_not_executable:<methodId>`, `parameterSchema` 위반 422 `method_parameters_invalid:<methodId>`. `parameters`는 `input_spec`으로 그대로 전달하고 `null`·생략은 키를 남기지 않는다. `modelKey` 생략 시 기존 IF
- 모델 — 워커 현행 [#111 구현]: `trainer/runner.py`가 큐의 `input_spec`을 `methods.registry.resolve_training_method`로 카탈로그 기준 재검증 — 카탈로그에 없는 `modelKey` → `unsupported_model`, 등록됐지만 `executable.process=false` → `method_not_executable:<methodId>`, `parameters`가 카탈로그 `parameterSchema` 위반 → `method_parameters_invalid:<methodId>`(작업 `failed`의 `errorCode`, 형식 `코드` 또는 `코드:식별자`). `modelKey`·`parameters` 생략은 IF 기본값(확정 파라미터는 `training_settings.parameters`에 기록)
- 학습 범위 — API 현행(#128): `timeBasis`는 `persisted_at`만 접수하고(키 생략과 같음) `esb_occurred_at`은 422 `config_schema_not_supported_yet`, `excludedIntervals`는 형식 검사 뒤 같은 코드로 거절한다. 워커가 두 키를 아직 적용하지 않아 받으면 제외되지 않은 채 성공하기 때문이다([models-ui.md](models-ui.md))
- `rule_only`: modelKey 생략 필수; 지정 시 422 `model_not_selected`(API·워커 같은 코드). 워커는 카탈로그 검증을 `model_not_selected` 검사보다 먼저 수행하므로 **`rule_only` 요청에 잘못된 `parameters`가 있어도 `method_parameters_invalid:<methodId>`로 거절**(#111 동작)
- `GET /api/jobs/feature-sets?sourceId=<uuid>`: `scope=global_registered_catalog`, sourceId, hasMore, items
- items: `{id, contentHash, featureNames: string[], supported: boolean, reason: string|null}`; 등록된 공통 피처 카탈로그, 출처별 데이터 존재·학습 가능성 보장 아님
- train 접수 시 피처 이름·연산·깊이·타입/단위·requires_fit 검사; 미지원 정의 422, rule_only의 fit 필요 피처 422
- 목록 최대 200개·최신순; hasMore=true이면 잘린 목록, 등록 기능은 별도 범위
- `POST /api/jobs`: 기존 train/evaluate 필드·권한·멱등키 유지, train에 optional modelKey 추가
- `GET /api/jobs`, `GET /api/jobs/:id`: 기존 attemptCount + maxAttempts/nextRunAt; 재시도 중 errorCode와 terminal failed 구분

## 완료 결과

- `resultSummary.serviceContractVersion`: `trainer-service-v1`
- `dataContract`: sourceId/snapshotId/snapshotHash/schemaVersion/selectionSpec/splitSpec/featureSetId/featureSetHash/ruleVersionIds
- `modelManifest`: modelKey/modelArtifactId/modelHash/fitArtifactId/fitHash/featureSetId/featureNames/definitionsHash/thresholdSpec; Rule 전용은 null
- 운영 상태 [#132 구현]: schemaVersion 3 구성의 train·evaluate 결과에만 `activeModelSetRevision`(정수)·`detectionPolicy {revision, policy, fixedAt: "attempt_start"}`을 추가. v1·v2 구성의 결과는 그대로이고 이때 리비전을 읽지 않음. 기록 위치·실패 코드는 [model-version.md](model-version.md) 판독 규칙 절
- `evaluationEnvelope`: rule_ml/synthetic/execution_cost/operational 4개 항목, [JSON Schema](trainer-evaluation-envelope.schema.json)
- 각 항목: status(measured/unavailable/not_applicable/failed), reason, metrics, metricVersion, optional details(출처·측정 조건)
- measured의 metrics: 지표명→`{value,unit,numerator,denominator,coverage,reason}`; 미산출 값 null+reason, coverage 0~1 또는 null
- 비율 지표 분자/분모 명시; 시간·용량 등 비율 아닌 값의 numerator/denominator는 null. 분모 0은 value=null, reason 필수
- `rule_ml`: `rule-ml-comparison-v1`; 동일 snapshot의 PROCESS 관측 revision·구성·선택 경로 기준 실제 상태 HR와 ML 비교. metrics의 hrBoth/hrRuleOnly/hrMlOnly/hrNeither는 비율, numerator=셀 건수, denominator=양쪽 succeeded+boolean인 비교 가능 건수, coverage=비교 가능/전체. hrTotal/hrComparable/hrMissing은 건수
- 무라벨 일치도는 정확도·오탐률 아님. 경로 skipped/실패/기한 초과/미정 상태는 비교 분모 제외. `details`에 sourceId/snapshotId/configVersionId/purpose/선택 경로/상태 Rule 정책·offline_snapshot 범위 고정
- `sr*` 셀(키 집합 유지) [#111 구현]: 통계 SR 폐기(#94)로 **영구 `not_applicable`/`sr_removed`** — `srBoth`/`srRuleOnly`/`srMlOnly`/`srNeither` value=null·reason=`sr_removed`, `srTotal`·`srMissing`=전체 건수(total), `srComparable`=0, `details.srStatus=not_applicable`·`details.srReason=sr_removed`. SR 프로토타입(#75)은 research 전용이며 서버 런타임에 연결하지 않음; 기존 사용자 Rule·HR 결과를 SR로 대체 금지
- `execution_cost`: `execution-cost-v1`; metrics의 totalMs/preprocessMs/trainMs/evaluationMs/inferenceMs/ruleMs/itemP50Ms/itemP95Ms/batchP50Ms/batchP95Ms/queueWaitMs(ms), throughput(rows/s), processRssBytes(bytes). 해당 실행에서 관측하지 않은 단계 null+not_observed
- 시간은 monotonic 실측. item 분위수는 실제 평가 행/Detector 1행 샘플, batch 분위수는 해당 작업의 배치 1회(totalMs) 분포이며 sampleCount 명시. throughput=전체 대상 행/totalMs; 처리 성공 건수와 별도
- 메모리는 완료 시점 현재 워커 프로세스 RSS 1회 샘플, 컨테이너 peak 아님. 지원하지 않는 OS/관측 실패 null. queueWaitMs는 DB 시계 기준 enqueue→attempt 시작(재시도 대기 포함). 큐 적체는 운영 API 별도
- `details`: batchSize/concurrency/warmCold(mixed_unclassified)/samplingPeriod(per_batch_completion)/memoryScope(process_current_rss_sample)/resourceLimits(not_observed)/clock/표본 수. 실제 준비 상태·컨테이너 제한을 추정값으로 대체하지 않음
- `synthetic`: 선택 `syntheticEvaluation` 요청의 실제 생성·고정 모델 평가 결과; 미요청은 unavailable/not_implemented 유지. 프로필 등록·요청·결과·재현 규약은 [합성 평가 계약](synthetic-evaluation.md)
- `operational`: unavailable/operational_report_separate_time_scoped; 운영 실적은 source/config/기간/보고 시간대를 명시한 별도 API에서 조회. 과거 오프라인 결과에 현재 실적을 덮어쓰지 않음
- ESB 발생→판정 지연은 추론 시간과 별도. 신뢰할 발생 시각·시계 정합성 미확인 시 esbLatencyMs=null/source_clock_unverified
- 기존 라벨 평가는 metrics/reasonCodes/evaluationId로 유지. 새 selected-paths-v3의 applicability는 선택 경로 기술적 완전성만 반영; 정답 라벨 부족은 정보성 사유·정의 불가 precision/recall/falsePositiveRate=null. 성능 점수 자동 합격·승인 없음, validation 전체 선택 경로 성공 및 파일/hash/모델·fit·스키마 검사 유지. 과거 v2 결과 재해석·재작성 없음
- 기존 결과 변경 금지; 이전 작업은 새 확장 필드가 없을 수 있음
- 결과 공개 조건: 후보/평가 저장과 job 성공·queue ack를 같은 DB 트랜잭션으로 확정

## 학습·전처리

- snapshot 기준 시각: `persisted_at`; 기간·cutoff 안 P 관측 중 execution별 최신 revision 1개만 멤버(`revision_no`·`persisted_at`·`id` 내림차순, Backend 보고 규칙과 동일; `selection_spec.revisionRule=latest-per-execution-v1`). 최소 행·그룹 검사는 선택 후 기준. 멤버를 같은 시각 블록으로 묶어 시간순 70/15/15
- 연구 startTime 분할과 별도 규약; train 내부 모델 fit/판정선 보정 기존 방식 유지
- 새 fit 연산: group_median / group_centered; groupBy=`P.adapter_type`, value=기존 허용 숫자 표현식
- 예: `{"name":"adapter_total_centered","expression":{"op":"group_centered","groupBy":"P.adapter_type","value":{"op":"field","field":"P.total_count"}}}`
- DB feature_version.requires_fit=true 필수; 연산/플래그 불일치·다른 fit 연산 거부
- 신규 학습 기본 `train-success-only-v1`: snapshot train 중 status=S, 명시적 anomaly 라벨 제외. F/P/N/미정/결측은 모델·fit 통계 입력에서 모두 제외. S는 학습 후보이며 정상 정답 보장 아님
- trainingSelection: policyVersion/split/populationCount/selectedCount/excludedCount/excludedExplicitAnomalyCount/statusCounts/normalGroundTruth=false; config·resultSummary·fit 참조에 저장, API 과거 버전별 그대로 조회. rule_only는 선정 정보만 기록하고 모델 fit 미실행
- snapshot membership와 validation/test는 필터링하지 않음. 평가 F/P/N/미정 상태 보존, S-only 학습 필터 재사용 금지
- 중앙값: 그룹별 유한 관측값; 결측 그룹/처음 보는 그룹은 missing, 전체 중앙값 자동 대체 없음
- fit 상태: 기존 fit.joblib에 정의 hash·그룹 통계·train snapshot 참조와 함께 저장; 모델 hash와 동일 수준 검증
- Trainer/Detector: 같은 정의 순서·fit 상태·transform 함수 재사용
- 모델별 fit 계산값을 observation+featureSet만 키인 전역 materialization에 저장하지 않음
- fit 연산 지원 경로: 불변 PROCESS 입력→Detector 직접 계산. 구형 feature queue/materialize_set은 fit 상태가 없으므로 명시적 거부 유지
- Rule 전용 후보는 fit 필요 피처 미지원; ML/병렬 후보의 Rule은 같은 검증된 fit으로 계산 가능
- 파일 이동 후 DB 확정 전 실패: 후보/성공 결과 미공개, attempt별 파일 경로 격리; 미등록 파일은 자동 재사용/활성화하지 않음
- 기존 피처 계산·IF 파일 형식 호환 유지; 지원하지 않는 정의는 성공 대체 없음
- 학습 후보 `ml_config_models.training_settings` [#111 구현]: 기존 키(`snapshotId`·`split`·`featureNames`·`definitionsHash`·`splitVersion`·`thresholdPolicy`·`trainingSelection`) 유지 + `methodId`·`methodVersion`·`parameters`(확정 파라미터 = 카탈로그 기본값 + 요청 값) 추가. configuration_spec schemaVersion 1·2 틀은 그대로, 마이그레이션 없음([model-version.md](model-version.md) 불변성·저장소 절). prepared 경로(`trainer/prepared_training.py`)는 아직 미반영 — 후속에서 맞춤. schemaVersion 3 행은 `timeBasis`·`trainingPeriod`도 저장하고 `trainingSelection.trainingPeriod`를 구성·결과에 남김. fit 참조에는 fit 필요 피처가 있을 때만 남음(그 밖에는 fit 참조 자체가 없음) [#132]

## 책임·범위

- Trainer: 학습/기존 평가·후보/불변 결과 저장
- Detector: 해시 검증된 동일 모델·fit 추론, 준비 후 승인된 버전만 적용
- API: 입력 검증·작업 접수·상태/결과 조회
- 신규 후보 statusRulePolicy: `{version:"process-status-hr-v1",sourceId,mapping:{S:"no_hit",F:"critical",P:"warning",N:"warning"}}`; source·버전·매핑 엄격 검증. F 위험/P·N 주의, P/N만으로 먹통·실패 확정 금지. 미정 상태는 unavailable
- 등록 사용자 Rule과 상태 HR 결과 별도 보존; 기존 Rule 또는 HR을 SR로 별칭 처리하지 않음(v1·v2 구성 현행 서술 — 룰 세트·모델 버전 분리 뒤의 대응은 아래 #94 변경 제안 절). 병렬/rule_only/ml_only 선택 모드·기한·수동 승인 보존. 신규 기본 정책은 새 후보에 고정하며 기존 구성·이력 자동 변경 없음
- Detector evidence schemaVersion=3: 기존 rules/models에 checkers.hr/sr/ml(status/anomalous/evidence), telemetry(위 실행 비용 가족), statusRulePolicy, observation(id/revisionNo/inputAsOf) 추가. 판정·계측·성공 job/queue ack 동일 트랜잭션, lease 소유권 상실 시 공개 차단. v3 모델 버전·v2 정책(`k:{hard,soft,ml}`)의 판정 기록은 evidence v4(detection-decision.md(#92) 11절) — 이 행의 v3 evidence 서술은 v1·v2 구성에 한함
- 운영 지표 수집: Detector/API; 최초 후보 적용 전 운영 실적 없는 상태 허용
- 운영·비용·후보 보고 API와 UTC/시간대·revision 중복 규약: [Backend 보고 계약](../backend/README.md)
- 배포 경계: server 내부 모듈만; research 런타임 import 금지

## 변경 제안 — Issue #94 (룰·모델 분리, 학습 제외 구간; 위 현행 서술은 #94에서 수정하지 않음 — #111 병합분만 #116에서 현행 절에 반영)

Created: 2026-11-02T18:00:00+09:00 / Updated: 2026-11-04T12:00:00+09:00 / Author: Claude Code (Trainer 세션 builder) / Status: draft(이 절만)

- 근거: 사용자 11/02 결정(학습 제외 구간, ESB 발생 시각 기준, 일별 거래량 그래프 위 구간 선택, 9.4 그래프 단위 월→일→시간→분), 10/28 전체 기획 보완안의 학습 흐름 Q-B1~B3(총괄 기록), #74 변경 제안 표 승계. 코드 변경 없음(계약만)
- 2차 개정(11/02 9.4·WebUI 대조): v3 train 요청에서 `detectionPolicy` 제거(운영 설정 전역 값, [model-version.md](model-version.md)); 거래량 집계 `bucket ∈ {month, day, hour, minute}`와 단위별 범위 상한; `excludedIntervals[].reasonCode` 선택(기본 `other`); `trainingSelection.trainingPeriod` 추가

### 요청 확장 — `POST /api/jobs kind=train` [제안]

```json
{
  "kind": "train",
  "sourceId": "<source UUID>",
  "from": "2026-10-01T00:00:00+09:00",
  "until": "2026-11-01T00:00:00+09:00",
  "timeBasis": "esb_occurred_at",
  "modelKey": "iforest",
  "parameters": null,
  "featureSetId": "<feature set UUID>",
  "excludedIntervals": [
    {"from": "2026-10-03T02:00:00+09:00", "until": "2026-10-03T06:30:00+09:00", "reasonCode": "known_incident", "note": "DB 장애"},
    {"from": "2026-10-20T22:00:00+09:00", "until": "2026-10-21T01:00:00+09:00", "note": null}
  ],
  "evaluationSetId": null
}
```

| 키 | 규칙 |
|---|---|
| `timeBasis` | `persisted_at`(현행, 키 생략 시 기본 — 기존 요청 호환) / `esb_occurred_at`(`data_observations.source_log_occurred_at`). snapshot 기간 자르기 `[from, until)`의 기준(10/28 Q-B2 ★A "둘 다 선택"). 새 학습 화면은 거래량 그래프가 ESB 발생 시각 기준이므로 `esb_occurred_at`를 보냄 |
| `excludedIntervals[]` | 0..32개. 각 `{from, until, reasonCode?, note?}`: ISO 8601(오프셋 필수), `from < until`, `[from, until)`가 요청 기간 안(밖이면 400 `excluded_interval_out_of_range`), 서로 겹치면 400 `excluded_interval_overlap`(서버 병합 없음 — **화면이 드래그 결과를 먼저 병합해 보낸 뒤** 보여 준 구간과 저장 구간이 같아야 함, WebUI 11/02). `reasonCode`는 **선택**, 생략 시 서버가 `other`로 저장; 값 ∈ `known_incident` / `maintenance` / `collection_anomaly` / `value_spike` / `other`. `note` ≤ 200자 선택. 구간 비교는 **항상 ESB 발생 시각**(`source_log_occurred_at`) 기준 [확정 11/02 Q7] — `timeBasis`와 무관 |
| `evaluationSetId` | 선택. 학습 성공 뒤 자동 비교 평가(상대 = 같은 `modelKey`의 활성 버전)에 쓸 세트([config-comparison-evaluation.md](config-comparison-evaluation.md) 자동 비교 절). null이면 기본 세트 규칙 |
| `modelKey`·`parameters` | #74 변경 제안 승계: 카탈로그 `ml:*` 키·`method_not_executable`·`parameters` 스키마 검증. 키 전부 생략(v3 키 없음) 시 현행 v1·v2 경로 |
| (없음) `detectionPolicy` | **v3 요청에 없음**(2차 개정): 종합 판정 정책은 운영 설정 전역 값([model-version.md](model-version.md) 운영 설정 절)이며 모델 버전·학습 작업에 묶이지 않음. v3 키가 있는 요청에 `detectionPolicy`가 있으면 400 `invalid_detection_policy`. v1 형식 요청(`{mode, deadlineMs, combination}` 필수)은 현행 그대로 |
| (없음) `ruleVersionIds` | v3 요청에는 없음. 룰은 룰 세트([rule-set.md](rule-set.md))가 판정 시점에 적용 |

### 선정 정책 v2 — `train-success-excluded-v2` [제안]

- 조건: snapshot **train 분할** ∧ `status = S` ∧ 명시 anomaly 라벨 아님(현행 `train-success-only-v1`) ∧ **ESB 발생 시각이 모든 제외 구간 밖** ∧ (제외 구간이 1개 이상이면) ESB 발생 시각 존재·`clock_status` 신뢰. 시각 없음·불신 행은 "구간 판단 불가"로 학습에서 제외하고 `excludedTimeUnknownCount`에 집계(Q-B2 A)
- `policyVersion` 기록: 요청에 `excludedIntervals` 키가 있으면(빈 배열 포함) v2, 없으면 v1. 빈 배열 v2는 v1과 같은 행을 고르지만 `excludedIntervalsHash`가 빈 목록 hash로 남음
- **validation/test 분할에는 적용하지 않음**(현행 원칙 유지, 10/28 5.2). 제외 구간을 평가 정답(이상 라벨)으로 바꾸지 않음
- 적용 지점: 모델 fit·판정선 보정·fit 연산(`group_median`·`group_centered`)·합성 기준선 **두 경로**(ADBench `select_training`, ESB `fit_generator`) 모두 공용 판단 함수(`is_training_eligible(row, selection)` 가칭)로. `reference_reason`의 S 검사는 그대로 두고 기준선 fit에서만 추가 호출(10/28 5.2)
- 최소 행: 제외 후 행 수가 최소 행·그룹·블록(`worker/VALIDATION.md:145`)·`minTrainRows`(카탈로그) 미만이면 작업 `failed` + `train_rows_insufficient_after_exclusion` + 건수. 사전 미리보기는 거래량 집계 API(아래)의 구간 밖 S 건수 합으로 근사(train 분할 비율 70%는 화면이 안내), 정확한 검사는 접수·실행 시
- `trainingSelection` 확장(config·resultSummary·fit 참조에 저장, 기존 키 유지): `policyVersion`, `timeBasis`, `excludedIntervalCount`, `excludedByIntervalCount`, `excludedTimeUnknownCount`, `excludedIntervalsHash`(= `[{from, until}]`를 from 순 정렬한 compact JSON SHA-256, UTC 정규화), **`trainingPeriod {from, until, timeBasis}`**(요청 기간 그대로 — 모델 버전 목록·상세 응답에 노출되어 수식 추천 "운영 모델 학습 시작 ~ 현재"의 입력이 됨, [llm-recommendation-job.md](llm-recommendation-job.md)). 구간 목록 자체는 `run_jobs.input_spec`·`ml_config_models.training_settings.excludedIntervals`에 저장([model-version.md](model-version.md), 상세 응답 `models[].trainingSettings`로 노출)
- 학습 선정 개체(10/28 Q-B1 ★b `data_training_selections`)는 **이번 범위에서 보류**: 제외 구간은 train 요청에 포함(a′). snapshot 단독 생성(Q-B3 A/B)이 결정되면 `trainingSelectionId` 참조로 승격하고 평가 세트도 같은 개체를 참조 — 결정 질문 Q-T1

### 거래량 집계 API — #128 구현(구현에서 정한 값은 [models-ui.md](models-ui.md)), DB 인덱스는 아래 DB 요구

- `GET /api/pipeline/reports/volume?sourceId=<uuid>&from=<ISO>&until=<ISO>&bucket=month|day|hour|minute&timeBasis=esb_occurred_at|persisted_at&timezone=Asia/Seoul`
- 응답: `{sourceId, timeBasis, bucket, timezone, from, until, totalCount, timeUnknownCount, buckets: [{bucketStart, processCount, statusCounts: {S, F, P, N, other}}]}`. 빈 버킷은 0으로 채움(그래프 연속), `bucketStart`는 `timezone` 기준 경계(월은 1일 00:00, 일은 00:00, 시·분은 정각)를 UTC ISO로
- 기준: PROCESS 관측(`data_processes` ⋈ `data_observations`), `timeBasis=esb_occurred_at`이면 `source_log_occurred_at`(null은 `timeUnknownCount`로 분리, 버킷에 넣지 않음). revision 중복은 Backend 보고 규칙(execution별 최신 revision 1개)과 동일 — snapshot 멤버 규칙과 일치. UTC/시간대 규약은 [Backend 보고 계약](../backend/README.md)
- 단위·한도 [확정 11/02 9.4 "월 → 일 → 시간 → 분"; 상한은 제안, WebUI 11/02 대조값]: `month` 범위 제한 없음 / `day` ≤ 3년(1096일) / `hour` ≤ 92일 / `minute` ≤ 7일 — 초과 400 `volume_range_too_large` + `{bucket, maxDays}`. 화면 확대·축소(마우스 휠, 주식 차트처럼)는 보이는 범위에 맞는 단위로 **재조회**(범위가 그 단위 상한 안이면 더 작은 단위 허용). 한 응답의 버킷 수 상한은 단위 상한에서 파생(minute 7일 = 10,080개)
- DB 요구: `data_observations(source_id, source_log_occurred_at)` 인덱스(현재 `persisted_at` 인덱스만, `002_core_erd.sql:51`). 재생(CSV) 데이터는 `persisted_at`이 재생 시각이라 ESB 시각 기준이 의미 있음(10/28 5.1)
- 용도: 새 학습 "기간 단계"(조회 범위 → [조회] → 일별 그래프 → 드래그/두 번 클릭으로 제외 구간 N개). 그래프는 선택 보조일 뿐, 제외 구간 자동 확정 없음

### capabilities·완료 결과 — #74 변경 제안 승계(모델 단위)

| 현재 문구 | 제안 |
|---|---|
| `models[{key,displayName,supported}]` | 유지 + `methodCatalog`([method-catalog.md](method-catalog.md) v2, `supported := executable.process`) — #128 반영(현행 절) |
| "모델: `iforest`만 지원; … 422 `unsupported_model`" | 카탈로그 `ml:*` 중 `executable.process=true`만 허용, 그 외 422 `method_not_executable:<methodId>`; `unsupported_model`은 카탈로그에 없는 키로 축소 — 워커 #111, API #128 반영(현행 절) |
| "`rule_only`: modelKey 생략 필수" | v1 요청에 한함. v3는 룰 전용 학습 없음(룰은 학습 대상 아님) |
| `modelManifest` 단일 | **단일 유지**(변경 없음 — 모델 1개 = 버전 1개, [model-version.md](model-version.md) Q-M1 확정 11/02). `modelManifests[]`는 도입하지 않음 |
| `rule_ml` 가족 `hrBoth/hrRuleOnly/…`·`sr*` 셀 | `hr`은 "룰(hard+soft 조합 결과)"로 읽고 이름은 유지(기존 결과 변경 금지). `sr*` 셀은 영구 `not_applicable/sr_removed`(SR 폐기 — #111 반영 완료, 현행 절 `sr*` 항목과 동일) — 새 metricVersion에서 제거 검토 |
| "병렬/rule_only/ml_only 선택 모드·기한·수동 승인 보존" | v1 구성에 한함; v3는 활성 모델 집합 리비전 + 룰 세트 리비전 + 운영 설정 `detectionPolicy` 리비전(세 가지, evidence v4 `activeModelSetRevision`·`ruleSetRevision`·`detectionPolicyRevision`) 보존 |
| "기존 Rule 또는 HR을 SR로 별칭 처리하지 않음" | 유지하되 SR 폐기 뒤에는 "등록 Rule(`rule:*`)·상태 HR(`hr:*`) 결과를 `hard:*`/`soft:*`로 **자동 번역하지 않음**(v1·v2 행은 읽기 투영만, [model-version.md](model-version.md) 조회 절)"으로 보완 |
| `kind=compare` 결과 | `replacementRecommendations[]`(modelKey별, 같은 종류끼리만)는 `replacement-recommendation-v1`로 완료 시 1회 계산·불변; `resultSummary.ruleSet{revision, contentHash}`·`detectionPolicy{revision, policy}`·`activeModelSetRevision` 기록 |

### PostgreSQL 세션이 찾은 계약 누락 3건 — 명시(10/21 인수인계 4절)

1. `run_jobs_v03_contract` CHECK(`004_erd_v03_contracts.sql:71-87`, `011:13-32`에서 재정의): kind별 queue·producer·role 조합을 **열거**하므로 새 kind는 반드시 분기에 추가. 이 계약이 요구하는 추가: `recommend_thresholds` → `llm_recommendation` 분기([llm-recommendation-job.md](llm-recommendation-job.md)). train 요청 확장 키(`excludedIntervals`·`timeBasis`)는 `input_spec` jsonb 안이라 CHECK 무관. **자동 비교 평가**가 시스템이 접수하는 `compare`는 현행 분기(`producer_kind='user'`·`requester_role='admin'`)를 만족하도록 **학습 요청 관리자를 승계**해 접수(분기 확장 없음)
2. `erd_v03_validate_job`의 llm_spec 필수 kind 목록(`004:213` → `011:60`)·LLM 완료 시 `run_llm_results` 필수 목록(`004:227` → `011:76-77`)·`run_llm_results_kind_check`(`004:112` → `011:36-37`)·`run_llm_results_check`(`004:120-121` → `011:39-41`): `recommend_thresholds` 추가 필요. `recommend_methods`는 011에 이미 있고 보류 상태여도 유지
3. `ml_config_versions` 불변 트리거: `004_feature_pipeline.sql:84-96` `pipeline_immutable`로 **이미 존재** → #74 "선택: 불변 트리거 추가"는 불필요. 룰 세트 이력 테이블([rule-set.md](rule-set.md))은 **새로** 불변 트리거 필요

### 결정 질문

| # | 질문 | 제안 |
|---|---|---|
| Q-T1 | 제외 구간 고정 위치: train 요청 포함(a′, 이번) vs 불변 학습 선정 개체(10/28 ★b) | a′로 시작, snapshot 단독 생성(Q-B3) 결정 시 b로 승격 |
| Q-T2 | `timeBasis` 기본값 `persisted_at`(호환) 유지 | 유지, 새 화면은 명시 전송 |
| Q-T3 | 제외 구간 상한 32·사유 코드 5종(`reasonCode` 선택, 기본 `other`) | 제안값 |
| Q-T4 | 거래량 집계 단위별 상한(month 무제한 / day 3년 / hour 92일 / minute 7일) | WebUI 대조 제안값. DB 응답 시간 측정 뒤 조정 |
