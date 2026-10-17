# Trainer 서비스 계약 v1

- 계약 버전: `trainer-service-v1`; 기존 `/api/jobs` 요청·상태·결과 필드 유지
- 실행: API → PostgreSQL training queue → Trainer → 불변 후보/평가 → API 조회
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
- 각 항목: status(measured/unavailable/not_applicable/failed), reason, metrics, metricVersion
- measured의 metrics: 지표명→`{value,unit,numerator,denominator,coverage,reason}`; 미산출 값 null+reason, coverage 0~1 또는 null
- 비율 지표 분자/분모 명시; 시간·용량 등 비율 아닌 값의 numerator/denominator는 null. 분모 0은 value=null, reason 필수
- 이번 버전 4가족 계산 미구현: status=unavailable, metrics=null, reason=not_implemented; 숫자 0 대체 금지
- 기존 라벨 평가는 기존 metrics/applicability/reasonCodes/evaluationId로 별도 유지; 4가족 측정 완료로 해석 금지
- 기존 결과 변경 금지; 이전 작업은 새 확장 필드가 없을 수 있음
- 결과 공개 조건: 후보/평가 저장과 job 성공·queue ack를 같은 DB 트랜잭션으로 확정

## 학습·전처리

- snapshot 기준 시각: `persisted_at`; 동일 execution의 겹치는 기간을 블록으로 묶어 시간순 70/15/15
- 연구 startTime 분할과 별도 규약; train 내부 모델 fit/판정선 보정 기존 방식 유지
- 새 fit 연산: group_median / group_centered; groupBy=`P.adapter_type`, value=기존 허용 숫자 표현식
- 예: `{"name":"adapter_total_centered","expression":{"op":"group_centered","groupBy":"P.adapter_type","value":{"op":"field","field":"P.total_count"}}}`
- DB feature_version.requires_fit=true 필수; 연산/플래그 불일치·다른 fit 연산 거부
- fit 대상: snapshot train 행 중 명시적 anomaly 라벨 제외; 미라벨은 검증된 정상으로 간주하지 않음
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
- 다음 평가 이슈: Rule·ML 교차·합성·실행 비용·운영 지표의 실제 계산
- 운영 지표 수집: Detector/API; 최초 후보 적용 전 운영 실적 없는 상태 허용
- 배포 경계: server 내부 모듈만; research 런타임 import 금지
