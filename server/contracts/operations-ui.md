Created: 2026-10-09T23:47:02.2964769+09:00
Updated: 2026-11-03T18:00:00+09:00
Author: frontend_operations_builder / #103·#105 Claude Code(WebUI builder)
Status: current

# 운영 관측 · 이상 근거 조회 UI 계약

- 범위: `/operations` user/admin 공용 운영 조회(개요 위젯·수집 탭, #103). 관리자 탐지 작업 조회 모델과 별도
- 연결: `GET /api/operations/*` 읽기 API 6개 백엔드 구현(#103, `backend/src/routes/operationsRoutes.ts`·`services/operationsReadModel.ts`). 아래 이벤트 상세·피드백 API는 미연결
- 인증: 기존 Bearer·401 세션 정리·403 권한 부족; 서버의 사용자 권한 검사 필수
- 권한: `/api/operations/*`는 user/admin 모두 GET만(집계만), 쓰기 경로 없음. 입력 모드 변경은 기존 관리자 전용 `POST /api/inputs/:sourceId/mode`(user 403)
- 데이터: 운영 집계·구성 버전·판정 근거만 허용; 원본 값·BODY·고객 로그·인증값·오류 원문(`last_error`)·입력 설정 원본 반환 제외
- 저장소: 기존 테이블만 조회(마이그레이션·스키마 변경 없음). 조회 실패 503 `operations_storage_unavailable`(오류 원문 미노출), 잘못된 요청 400(`invalid_source_id`·`invalid_layer`·`invalid_days`·`unknown_operations_filter`), 없는 원천 404 `source_not_found`
- 화면 오류 제목: 404 `source_not_found` = `<항목> · 원천 없음`, 그 외 404·503 = `<항목> 미연결`, 기타 = 조회 실패
- null 의미: 저장·계측되지 않은 값은 `null`(개요는 키 생략) = 화면 `측정 불가`/`—`. 0은 실제로 센 모집단이 0일 때만. 고정값·추정값·성공 대체 금지
- 조회(공통): 계정별 query key·AbortSignal·세션 변경 지연 응답 제외(`useAccountQuery`); 자동 재시도·포커스/재연결 재조회 없음, 수동 새로고침(`운영 관측 새로고침`). 예외: 수집 현황은 입력 실행 `running`일 때만 5초(`RUNNING_POLL_MS`)

## GET /api/operations/overview

- 허용: user/admin; 읽기 전용, 쿼리 없음. 범위: 전체 원천
- 필수: `observedAt` ISO 시각(DB 시계), `maxAgeMs` 서버 신선도 정책의 양수 ms(현재 60000)
- 선택: `activeConfigurationVersion`, `endToEndLatencyMs`, `queueWaitP95Ms`, `collectorEps`, `cpuPercent`, `memoryPercent`, `diskPercent`, `alertDeliverySuccessPercent`, `activeConfiguration`
- 수치: 유한·0 이상, Percent 항목 100 이하
- 현재 제공: `collectorEps` = 최근 60초 저장 관측 수/60(전 계층). `queueWaitP95Ms` = 전체 원천 detect 작업 생성 → 첫 시도 시작 최근 1시간 p95(표본 없으면 생략). cpu·memory·disk·알림 전달률은 계측 없음 → 키 생략
- `endToEndLatencyMs`: ESB 로그 발생 → 판정 저장 실측(최근 1시간 `latency_status=measured` 중앙값); API 수신 시각 기준 재기산 금지. 현재 Detector가 항상 `unavailable`로 저장(원천 시계 미검증) → 키 생략 = 화면 `원천 발생 → 판정: 측정 불가`
- `activeConfiguration`: `ops_system_state` 운영 포인터. 없으면 `null`(운영 구성 없음). 객체 `configVersionId`, `sourceId`, `revision`, `changedAt`, `detectionMode`(`configuration_spec.detectionPolicy.mode`), `ruleCount`(`ml_config_rules`), `ruleSeverityCounts`(info/warning/critical), `models[]`(`modelKey`, `adapterName`). #94 룰 세트·활성 모델 집합 구현 후 그 기준으로 교체
- `stages` 선택 키: `collector`, `redaction`, `observationQueue`, `detector`, `judgmentStore`
- 단계 선택 필드: `state` healthy/degraded/stopped/unknown, `pendingCount` 0 이상 정수, `oldestWaitMs` 0 이상 ms, `lastProcessedAt` ISO 시각
- 단계 의미: Collector 수집 → 민감 필드 제거 → 관측 저장·ID 큐 등록 → Detector 조회·추론 → 판정 저장
- 현재 출처: collector `lastProcessedAt` = 최신 `data_observations.persisted_at`; observationQueue `pendingCount`·`oldestWaitMs` = `detection_logs` pending 수·최장 대기; detector `pendingCount` = leased(처리 중) 수, `lastProcessedAt` = 최신 detector 계측 시각; judgmentStore `lastProcessedAt` = 최신 `decided_at`. `redaction`(별도 기록 없음)·모든 `state`(건강 판정 근거 없음)는 생략
- 누락: — 확인 불가; 0 대기량은 실제 제공 시에만 0 표시
- 신선도: 현재 시각 - observedAt > maxAgeMs 시 오래됨; 미래 관측은 확인 불가; 조회 실패·오래됨에 정상 배지 금지
- 이전값: 재조회 실패·오래됨 시 최근 관측 시각과 상태를 함께 보존
- SSE: 기존 RealtimeAnomalyProvider 공유; 연결됨은 이벤트 연결 상태, Collector/큐/Detector health 증거 아님

## GET /api/operations/collection?sourceId=

- 허용: user/admin(입력 모드는 읽기 전용 요약). 수집 탭 카드·개요 `수집 요약` 위젯
- `source`: `id`, `datasetKey`, `displayName`, `ingestionMethod`
- `input`: 입력 전환 미등록 원천이면 `null`. `mode` api/csv/paused, `revision`, `status`(현재 실행 running/paused/completed/failed 또는 null), `updatedAt`, `workerHeartbeatAt`, `csv`(`replayKey`, `recordCount`, `rate` 초당 행, `repeat`, `position`, `total`=recordCount×repeat) 또는 null, `api`(`pollSeconds`) 또는 null. `lastError`·설정 원본 미포함(관리자는 `/api/inputs`)
- `layers[]`: T/P/M/B 4행 고정. `storedCount`(누적), `lastMinuteCount`(최근 1분 저장), `lastPersistedAt`, `revisionArrivalCount`(`revision_no>1`). 해당 계층 관측이 없으면 0·null(전수 집계). BODY 값은 수집하지 않음(B는 참조 행 수만) — 화면 `수집 안 함 · 판정 입력 아님`
- `hourly`: `{layer:'T', basis:'persisted_at', items[24]}` 최근 24시간 시간별 T 저장 건수(마지막 = 진행 중인 현재 시간). 원천 로그 시각이 아닌 저장 시각 기준
- `history[]`: 최근 입력 실행 10건(`ops_input_runs`) `id`, `createdAt`, `mode` api/csv, `replayKey`, `status`, `lastSuccessAt`, `acceptedCount`. 모드 전환 감사 이력 테이블이 없어 실행 단위 이력

## GET /api/operations/latency?sourceId=

- 허용: user/admin. `window` = `1h`, `stages[]` 순서 고정: `collector`, `persist`, `detectionQueue`, `detector`, `judgmentStore`
- 각 단계: `samples`, `p50Ms`, `p95Ms`, `recentFailures`, `queuePending`(+`oldestWaitMs`). 표본 없으면 분위수 null
- collector·persist: 고객 API 조회 표본(`ops_api_poll_samples.duration_ms`·`db_commit_ms`). CSV 재생은 표본 없음 → null. collector 실패 = `error_code` 있는 표본 수(표본 없으면 null)
- detectionQueue: 대기 = detect 작업 생성 → 첫 시도 시작(`run_jobs`·`run_job_attempts`). `queuePending`·`oldestWaitMs` = 해당 원천 `detection_logs` pending. 원천 범위는 `payload.sourceId`, sourceId 없는 이전 CSV 재생 메시지는 `payload.observationId`의 관측 원천으로 확인(`pg_queue.py` claim 범위와 같은 규칙, 누락 없음)
- detector: `ops_pipeline_measurements(stage='detector')`. 실패 = 1시간 내 failed detect 작업 수
- judgmentStore: 저장 소요 미기록(`decision_committed_at` 미사용) → null
- `endToEnd`: `{p50Ms:null, p95Ms:null, reason:'source_clock_unverified'}`

## GET /api/operations/source-fields?sourceId=&layer=T|P|M|B

- 허용: user/admin. 최근 저장 관측 최대 1000건(`sampleLimit`) 표본, `sampleSize`
- `fields[]`: `name`, `presentCount`(값 있음), `nullCount`(키 있음·null), `missingCount`(원천 미제공 `missing_fields`). 값 자체 미반환
- `qualityFlags[]`: `flag`(예: `invalid_numeric:<field>`, `execution_identity_unverified`), `count`
- 화면: 필드·지원 여부 = 제공/미제공, 결측·품질 = (null+미제공)/표본 + 해당 필드 품질 표시. BODY는 `수집 안 함 · 판정 입력 아님`

## GET /api/operations/storage

- 허용: user/admin. `measuredAt`, `databaseBytes` = `pg_database_size(current_database())` 실측
- `disk`: `ops_storage_measurements` 최신 measured 행(`capacityBytes`, `usedBytes`, `availableBytes`, `warningState`, `measuredAt`). 현재 기록하는 작업 없음 → `null` = 화면 `데이터 마운트 · 측정 불가`

## GET /api/operations/decision-trend?days=1..14

- 허용: user/admin. 기본 `days=3`, 시간대 `Asia/Seoul` 판정일
- 현재 운영 구성(`activeConfiguration`) 기준. 운영 구성 없으면 `{configVersionId:null, items:[], reason:'no_active_configuration'}`
- 집계: 운영 리포트 `operational-v1`(`/api/pipeline/reports/operational`) SQL·정책 재사용, live·replay 범위 합산(입력 범위 미확인 관측 제외)
- `items[]`: `day` YYYY-MM-DD, `decisionCount`, `anomalySignalRatio` = 이상/판정 가능(분모 0이면 null), `unavailableRatio` = (판정 − 판정 가능)/판정(판정 0이면 null) — 화면 날짜/판정/이상 신호/정상 여부 미확인

## 개요 위젯·배치

- 위젯: 단계 흐름 / 최근 판정(`/api/pipeline/decisions?limit=4`, 판정마다 상세 링크 `/analysis/d/:decisionId`) / 판정 추이 / 현재 운영 룰·모델(운영 설정 링크는 관리자만) / 큐 적체(개요 기준 전체 원천: 대기 수·최장 대기·`queueWaitP95Ms`) / 수집 요약(수집 탭 링크). 저장공간은 추가 목록에만(기본 배치 제외). 배치된 위젯만 조회
- 배치: react-grid-layout 6열 격자(x, y, w, h), 위젯별 기본·최소·최대 크기, 세로 압축. 위젯 영역(화면이 아닌 위젯 영역 너비) 1000px 미만·격자 측정 전은 읽는 순서 배치(위젯 영역 672px 이상 2열, 미만 1열, 높이는 내용만큼, 끌기·크기 조절 없음). 격자 자식·배열 순서 = 읽는 순서(y, x) = Tab·낭독 순서
- 편집(#100 이전 홈 위젯 방식 이식): 끌어서 이동·오른쪽 아래 손잡이로 크기 조절(다른 위젯 부드럽게 재배치), `위젯 추가` 목록(축소 모양을 끌어 격자에 놓기 또는 `추가` 버튼), 모서리 제거 배지, 편집 중 살짝 흔들림·끌 때 들림(Motion, `prefers-reduced-motion` 시 멈춤), 키보드 대안 앞으로/뒤로·제거 버튼(이동 후 끝에 닿으면 반대 방향 버튼, 제거 후 이웃 위젯 제거 버튼 또는 `위젯 추가`로 초점 이동), 목록 축소 모양은 5px 이상 끌 때만 끌기 시작(클릭은 동작 없음), `배치 저장`·`기본 배치로 초기화`·`편집 취소`. 편집 중 배치는 화면 초안(`draftStore`, `/operations`) → 탭 이동 유지, 이탈 확인
- 저장: 계정별 브라우저 저장소(`esb_operations_widgets_<accountId>`, `{version:2, items:[{i,x,y,w,h}]}`; 이전 순서 배열은 그 순서로 기본 크기 재배치, 그 외 형식은 기본 배치), 읽기·쓰기 실패 시 현재 화면만 유지·실패 표시. 서버 계정별 저장은 하위 Issue 8에서 `WidgetLayoutStore` 구현 교체
- 미제공 집계의 고정값·성공률·미리보기 가짜 운영 상태 금지

## GET /api/anomaly/events/:eventId/detail

- #105: 백엔드 미구현인 이 상세·아래 피드백 API를 부르던 옛 이상 분석 실시간 이벤트 화면을 폐지했다(프론트 호출 없음). 이상 분석은 저장 판정 기준 `analysis-ui.md`로 대체. 아래 두 절은 향후 조사·조치 기록 Issue의 참고 기록

- 허용: user/admin; 별도 읽기 모델, 기존 SSE v1 필수 필드 변경 없음
- 필수: 요청과 일치하는 `eventId`; 다른 이벤트 응답의 상세 표시 금지
- 선택: 한국어 `summary`, 당시 `configurationVersion`, 실제 구성 ID `configurationId`, 실제 평가 ID `evaluationId`
- 선택: `decisionState` complete/partial/unavailable, 업무 `workflowStatus` Open/Resolved
- Rule/ML: 선택 객체 `rule`, `ml`; `execution` completed/partial/unavailable/not-run, 선택 `version`, `reason`
- 근거: `measurements`의 `name`, 선택 `value`, `threshold`, `unit`; 측정값·기준은 유한 숫자 또는 문자열
- 원인 가설: 별도 `hypotheses` 문자열 배열; 사실·정답·확정 원인으로 표시 금지
- 미제공/잘못된 선택 메타데이터: 확인 불가; 없는 Rule/ML 실행·임계값·구성·workflow 추정 금지
- 원본 BODY·거래 스냅샷·민감 필드 반환 제외; 프론트 모델은 명시한 필드만 복사
- 오류: 404/503 근거 미연결, 기타 조회 실패; 수신 SSE 기본 이벤트는 유지, 이전 상세 존재 시 이전값 문구 표시
- 조회: 계정+eventId 전용 key·AbortSignal·세션 지연 응답 제외; 자동 404 폴링 없음
- 관리자 링크: `configurationId` 유무에 따라 `당시 구성 보기` / `운영 구성 목록` 문구로 `/detection/operations`(운영 설정) 이동. 당시 구성 상세 표시는 운영 설정 하위 Issue에서 추가(#100에서 옛 운영 버전 화면 폐지)
- 관리자 링크: 실제 `evaluationId` → `/detection/evaluations/<id>`(평가 상세); 미제공 ID 임의 생성 금지

## 업무 분류와 SSE 선택 메타데이터

- SSE v1 추가 선택 필드: `workflowStatus` Open/Resolved; 원천 `status`와 별도
- 기존 필수 검증·metadata 없는 유효 v1 이벤트 호환 유지
- 잘못된 workflowStatus는 기본 이벤트 수신 유지·workflow 해석 제외
- 목록: 제공된 workflowStatus만 분류; 미제공 시 보류/완료 0건 배지 금지·확인 불가
- 일부 제공: 분류함 제목의 상태 제공 건수 기준·나머지 이벤트 업무 상태 미확인
- 상세 GET workflow는 해당 이벤트에만 표시; 전체 목록 상태로 임의 전파 없음

## POST /api/anomaly/events/:eventId/feedback

- 허용: admin; 기존 서버 인증·관리자 검사 필수, user 쓰기 403
- 가용성: 상세 `capabilities.saveFeedback === true`인 관리자만 제출; 미제공·false·조회 실패 시 저장 비활성
- 요청: `kind` action/suspected-false-positive/insufficient-evidence, `reason` trim 후 1~2000자
- 성공: `eventId` 요청 일치, 실제 저장 `feedbackId`, 실제 `savedAt` 시각 필수
- 실패/미연결: 초안 유지·서버 저장 완료 표시 금지; 비일치/불완전 성공 응답도 미확인 처리
- 저장 의미: 조사·조치 기록; 업무 상태 전환·확정 라벨·학습 입력 승인과 별도
- 초안: `/analysis` 세션 draft의 이벤트별 사유/유형, 일반 사용자 편집 UI 없음
- 보호: 기존 페이지 이탈/새로고침 초안 보호·계정 변경 초기화; 요청 unmount/계정 변경 abort·지연 완료 제외
- 구현 경계: 신규 상세/피드백 API 서버 미연결; 로컬 초안의 서버 저장 성공 가장 없음
