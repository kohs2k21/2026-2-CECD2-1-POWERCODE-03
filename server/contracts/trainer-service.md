# Trainer 서비스 계약 v1

- 계약 버전: `trainer-service-v1`; 기존 `/api/jobs` 요청·상태·결과 필드 유지
- 실행: API → PostgreSQL training queue → Trainer → 불변 후보/평가 → API 조회
- 외부 완성 가공 CSV·합성 주입 CSV: [prepared-csv-v1](prepared-csv-import.md)의 관리자 반입·model_input·snapshotId 계약 적용; SQL/SQLite·raw 모델 호환 추정·운영 적용 제외
- 학습 성공: 후보 생성만; 활성 버전 변경은 기존 관리자 승인·Detector 적용 경로
- 실행 상태: queued/running/succeeded/failed; 자동 재시도는 같은 job, 최대 3회·attempt 이력
- terminal failed 재실행: 새 idempotency key로 명시적 새 요청; 같은 key·같은 본문은 기존 job 반환, 다른 본문은 409
- Web 멱등키: sessionStorage 정상 시 새로고침 후 재사용, 저장 실패 시 현재 페이지의 메모리에서만 유지; 저장 실패 후 새로고침한 경우 작업 이력 확인 후 재실행 여부 결정

## 요청·발견 API

- `GET /api/jobs/capabilities`: `contractVersion`, `models[{key,displayName,supported}]`, `evaluationFamilies`, `fittedFeatures`, `snapshot`
- 모델: `iforest`만 지원; train의 `modelKey` 생략 시 기존 IF, 다른 값 422 `unsupported_model`
- `rule_only`: modelKey 생략 필수; 지정 시 422 `model_not_selected`
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
- `evaluationEnvelope`: rule_ml/synthetic/execution_cost/operational 4개 항목, [JSON Schema](trainer-evaluation-envelope.schema.json)
- 각 항목: status(measured/unavailable/not_applicable/failed), reason, metrics, metricVersion, optional details(출처·측정 조건)
- measured의 metrics: 지표명→`{value,unit,numerator,denominator,coverage,reason}`; 미산출 값 null+reason, coverage 0~1 또는 null
- 비율 지표 분자/분모 명시; 시간·용량 등 비율 아닌 값의 numerator/denominator는 null. 분모 0은 value=null, reason 필수
- `rule_ml`: `rule-ml-comparison-v1`; 동일 snapshot의 PROCESS 관측 revision·구성·선택 경로 기준 실제 상태 HR와 ML 비교. metrics의 hrBoth/hrRuleOnly/hrMlOnly/hrNeither는 비율, numerator=셀 건수, denominator=양쪽 succeeded+boolean인 비교 가능 건수, coverage=비교 가능/전체. hrTotal/hrComparable/hrMissing은 건수
- 무라벨 일치도는 정확도·오탐률 아님. 경로 skipped/실패/기한 초과/미정 상태는 비교 분모 제외. `details`에 sourceId/snapshotId/configVersionId/purpose/선택 경로/상태 Rule 정책·offline_snapshot 범위 고정
- `sr*` 동일 집계 규약; 런타임 SR 미연결은 셀 value=null, reason=sr_experiment_pending. SR 집계기는 통제 fixture만 검증하며 기존 사용자 Rule·HR 결과로 대체 금지
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

- snapshot 기준 시각: `persisted_at`; 동일 execution의 겹치는 기간을 블록으로 묶어 시간순 70/15/15
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

## 책임·범위

- Trainer: 학습/기존 평가·후보/불변 결과 저장
- Detector: 해시 검증된 동일 모델·fit 추론, 준비 후 승인된 버전만 적용
- API: 입력 검증·작업 접수·상태/결과 조회
- 신규 후보 statusRulePolicy: `{version:"process-status-hr-v1",sourceId,mapping:{S:"no_hit",F:"critical",P:"warning",N:"warning"}}`; source·버전·매핑 엄격 검증. F 위험/P·N 주의, P/N만으로 먹통·실패 확정 금지. 미정 상태는 unavailable
- 등록 사용자 Rule과 상태 HR 결과 별도 보존; 기존 Rule 또는 HR을 SR로 별칭 처리하지 않음. 병렬/rule_only/ml_only 선택 모드·기한·수동 승인 보존. 신규 기본 정책은 새 후보에 고정하며 기존 구성·이력 자동 변경 없음
- Detector evidence schemaVersion=3: 기존 rules/models에 checkers.hr/sr/ml(status/anomalous/evidence), telemetry(위 실행 비용 가족), statusRulePolicy, observation(id/revisionNo/inputAsOf) 추가. 판정·계측·성공 job/queue ack 동일 트랜잭션, lease 소유권 상실 시 공개 차단
- 운영 지표 수집: Detector/API; 최초 후보 적용 전 운영 실적 없는 상태 허용
- 운영·비용·후보 보고 API와 UTC/시간대·revision 중복 규약: [Backend 보고 계약](../backend/README.md)
- 배포 경계: server 내부 모듈만; research 런타임 import 금지
