Created: 2026-11-03T18:00:00+09:00
Updated: 2026-11-14T18:00:00+09:00
Author: #105 Claude Code(WebUI builder)
Status: current

# 이상 분석 판정 목록·상세·처리 UI 계약

- 범위: `/analysis` 판정 결과별·위험도별 목록과 분류함, `/analysis/d/:decisionId` PROCESS 판정 상세(Process Flow·처리 상태·조사·조치 기록 포함)(#105, 상위 #101). 화면 기준 와이어프레임 #93 `section[data-view="analysis"]`
- 연결: `/api/analysis/*`(`backend/src/routes/analysisRoutes.ts`, 읽기 `services/analysisReadModel.ts`, 쓰기 `services/analysisWorkflow.ts`) — 읽기 4개(목록·상세·판정 기록·Process Flow), 처리 상태 변경·읽음 표시, 조사·조치 기록 읽기·쓰기. 기존 `GET /api/pipeline/decisions` 그대로, SSE `GET /api/pipeline/events`는 `include=interims` 선택 추가(pipeline.md, 아래 실시간)
- 인증·역할: 기존 Bearer(쿠키 없음 → 별도 CSRF 토큰 없음, 기존 쓰기 API와 같음). 없음·잘못된 토큰 401. 읽기·처리 상태 변경·읽음 표시 = user/admin 모두(와이어프레임 dWorkflow는 역할 구분 없음, 요청자 기록). 조사·조치 기록 읽기·쓰기 = admin만, user는 403 `admin_required`(와이어프레임 `feedbackBox.hidden = !admin`). 설명 요청은 기존 `POST /api/jobs {kind:"explain"}`(pipeline.md)
- 저장소: 판정 값은 저장값 그대로 반환하고 다시 계산하지 않는다. 처리 상태·조사·조치 기록·읽음은 `014_analysis_workflow.sql` 새 표 3개(기존 표 변경 없음, infra/database/README.md "014"), 조사·조치 기록의 확인 결과는 `029_feedback_outcome.sql` 열 1개(같은 README "029"). 조회·저장 실패 503 `analysis_storage_unavailable`(오류 원문 미노출)
- 대상 키(014): detect 작업이 있으면 작업 ID, 작업 없는 이전 형식 판정이면 판정 ID. 작업 항목(판정 중·중간 판정·판정 실패)에 남긴 처리 상태·기록·읽음은 그 작업의 최종 판정으로 이어진다
- 잘못된 요청 400: `unknown_analysis_filter`, `invalid_category`, `invalid_signal`, `invalid_completeness`, `invalid_sort`, `invalid_page`, `invalid_page_size`, `invalid_q`, `invalid_decision_id`, `invalid_workflow_request`, `invalid_feedback_request`, `invalid_read_request`, `invalid_json`(본문 JSON 해석 실패). 본문 해석 단계의 다른 요청 오류는 그 4xx 그대로: 16KB 초과 413 `request_too_large`, 지원하지 않는 charset 415 `unsupported_charset`·Content-Encoding 415 `unsupported_encoding`, 전송 중단 400 `request_aborted`, 그 밖 `invalid_request`(503은 저장소 오류만). 없는 판정 404 `decision_not_found`. 처리 상태 충돌 409 `workflow_state_conflict`·`workflow_transition_not_allowed`
- 화면 오류 제목: 404 `decision_not_found` = `<항목> · decision_not_found`(오류 코드 그대로), 그 외 404·503 = `<항목> 미연결`, 기타 = 서버 메시지 또는 조회 실패. 재조회 실패 시 이전 결과 + `<오류 제목> · 이전 관측값 표시`. 실패를 빈 목록·성공으로 바꾸지 않는다

## 판정 항목

판정 항목 = 최종 판정 행(`run_process_decisions`) + 최종 판정이 없는 v03·v04 detect 작업(`run_jobs` kind=detect, status≠succeeded — succeeded detect 작업은 판정 행이 반드시 있음).

| `kind` | 출처 | `id` | `stage` | `signal` |
|---|---|---|---|---|
| `final` | 판정 행 | 판정 ID | `final`, 같은 작업에 `superseded` 중간 기록이 있으면 `replaced` | 아래 이상 신호 규칙 |
| `interim` | 작업(queued·running·blocked) + 닫히지 않은 첫 이상·근거 추가 기록 | 작업 ID | 마지막 열린 기록이 `evidence_added`면 `updated`, 아니면 `provisional` | `yes`(중간 기록은 종합 이상일 때만 쓴다) |
| `pending` | 작업(queued·running·blocked), 열린 중간 기록 없음 | 작업 ID | `pending` | `pending` |
| `failed` | 작업 failed(판정 행 없음, 중간 기록 유무 무관) | 작업 ID | `failed` | `failed` |

- 이상 신호(`final`): 운영 리포트 `operational-v1`과 같은 규칙. `yes` = status≠`unavailable` ∧ `evidence.anomalous=true` / `no` = status=`complete` ∧ `evidence.anomalous=false` / `unknown` = 그 밖(부분+false, 판정불가, anomalous 없음·null). 미완료를 정상으로 바꾸지 않는다
- 열린 기록: 같은 시도에 `superseded`·`no_final`이 없는 `first_anomaly`·`evidence_added`. 여러 시도에 있으면 시도 번호·순번이 가장 큰 기록(새 시도가 아직 첫 이상을 쓰지 않으면 이전 시도 기록이 열린 채 남는다, 012 규칙)
- `occurredAt`: final = `decided_at`, interim = 마지막 열린 기록 `created_at`, pending = 작업 `created_at`, failed = 작업 `finished_at`(없으면 `created_at`). 정렬 기준
- 문맥: `processObservationId`(작업은 입력 manifest의 PROCESS)의 `data_processes`(거래 ID·프로세스 ID·채널·로그 상태·응답 코드)와 인터페이스 ID(`interfaceId`) = 실행 그래프 거래 노드 대표 행([process-graph.md](process-graph.md) "거래 노드 대표 행")의 `data_transactions.interface_id` — 먼저 읽는 TRANSACTION은 검증된 부모 연결(FK)이 있으면 그 행, 없으면 같은 원천·같은 거래 ID의 최신 리비전(실제 적재분은 FK가 비어 있음), 대표가 모호(재시도 번호 동률·없음, 실행 id 없음)하면 null. 그 밖에 없으면 null
- 등급 `grade`: 저장된 evidence v4(`schemaVersion` 4 + `composite` 객체)의 `composite.grade`(anomaly·anomaly_sign·normal·undetermined)만. v3·없음·알 수 없는 값 = null(detection-decision.md U6, 재계산 없음 D7). 중간 판정 항목은 마지막 열린 기록 payload의 값
- 위험도 `riskStatus`·`riskLevel`: 이상 신호(`signal=yes`)가 아니면 `not_applicable`·null. 이상 신호이면 저장된 v4 `composite.risk.status=rated`이고 `level`이 critical·warning·info일 때만 `rated` + 그 단계, 그 밖(v3 판정, v4 unrated, 형식이 틀린 risk) = `unrated`(위험도 미정)·null. 단계를 만들거나 다시 계산하지 않는다(D7·U6). 대체됨·최종 없음으로 닫힌 중간 기록의 위험도는 최종 판정에 옮기지 않는다(13.1: 같은 시도 안에서만 오르고, 철회는 대체됨·최종 없음 때만)
- 처리 상태 `workflowState`: 대상의 마지막 변경 `to_state`, 없으면 `detected`(감지됨). `workflowChangedAt` = 마지막 변경 시각(없으면 null)
- 미확인 `unread`(목록 응답만): 요청자의 열람 시각(화면이 본 항목 시각, 아래 읽음 표시)이 없거나 항목 `occurredAt`보다 이르면 true(본 뒤 중간 판정·최종 판정이 새로 생기면 다시 미확인). 상세·판정 기록 응답에는 없다

항목 필드: `id`, `kind`, `stage`, `signal`, `decisionId`(final만), `jobId`, `jobStatus`(작업 항목만), `sourceId`, `processObservationId`, `configVersionId`(작업은 입력 구성, v04 작업·판정은 구성이 없어 null), `status`·`ruleStatus`·`mlStatus`·`decisionCode`·`anomalyScore`(final만, 그 외 null. 점수는 문자열), `evidence`(final = 판정 evidence, interim = 마지막 열린 기록 payload, pending·failed = null), `occurredAt`, `decidedAt`(final만), `transactionId`, `processId`, `interfaceId`, `channelId`, `processStatus`, `responseCode`, `responseCodeMessage`(응답 코드 사전 메시지, 사전에 없으면 null — [response-codes-ui.md](response-codes-ui.md) 5절), `grade`, `riskStatus`, `riskLevel`, `workflowState`, `workflowChangedAt`, (목록만) `unread`·`causes`. 시각은 UTC ISO(마이크로초).
- 이상 원인 `causes`(목록만, 사용자 11/13 목록 행 이상 원인 줄): 이상 신호(`signal=yes`)가 아니면 `[]`. 저장 evidence에서 이상(anomalous=true)인 방법만 옮기며 다시 판정하지 않는다. 항목 `{category(hard·soft·ml), methodId, ruleKey, code, observedStatus, fieldName, operator, threshold, unit, observedValue, modelName, score, scoreThreshold}`(해당 없는 키는 null)
  - v4(`schemaVersion` 4 + `methodResults[]`): `methodResults[]` anomalous=true 항목. 종합 근거 `composite.basisMethods`에 든 방법 먼저, 나머지는 저장 순서. 상태 규칙 `observedStatus` = 그 방법 `evidence[0].observedStatus`(없으면 v3 호환 `rules[]` 상태 규칙 값), 이상 징후 규칙 = `condition`(`fieldName`은 방법 카탈로그 `softRuleFields[].displayName`, 없으면 필드 ID)·`observedValue`, 머신러닝 모델 = `modelName`(카탈로그 `methods[].displayName`, 없으면 모델 키)·`score`(`scoreDetail.raw`, 없으면 `score`)·`scoreThreshold`(`scoreDetail.threshold`)
  - v3: `rules[]` result=hit(상태 규칙 → `hard:process-status`+`observedStatus`, 그 밖의 등록 Rule → `category=soft`·`ruleKey`) + `models[]` anomalous=true(`ml:<modelKey>`, `score` = 판정 행 `anomalyScore`)
  - 문구는 화면이 만든다(아래 화면 목록 행)

## 분류(사이드바)

| 보기 | `category` | 화면 | 조건 |
|---|---|---|---|
| 판정 결과별 | `all` | 전체 | 모든 항목 |
| | `anomaly` | 이상 | `signal=yes` ∧ `grade`≠anomaly_sign(v3 등급 null은 이상) |
| | `anomaly_sign` | 이상 징후 | `signal=yes` ∧ `grade`=anomaly_sign |
| | `normal` | 정상 | `signal=no`(최종 판정 complete ∧ `evidence.anomalous=false`, v03·v04 같음) |
| | `in_progress` | 판정 중·중간 판정 | `kind` ∈ pending·interim |
| | `undetermined` | 정상 여부 미확인 | `signal=unknown` |
| | `failed_replaced` | 판정 실패·대체 | `stage` ∈ failed·replaced |
| 위험도별 | `risk_all` | 전체 | `signal=yes` |
| | `risk_critical`·`risk_warning`·`risk_info` | 위험·주의·참고 | `riskLevel` = critical·warning·info |
| | `risk_unrated` | 위험도 미정 | `riskStatus=unrated` |
| 분류함(두 보기 공통) | `workflow_open`·`workflow_resolved` | 보류·완료 | `workflowState` = open·resolved |

- 순서는 와이어프레임 CATS·RISK_CATS·WORKFLOW 그대로에 정상을 이상 징후 다음에 더했다(사용자 11/13: 전체에 정상 판정도 섞여 보여 정상 분류를 따로 둔다). 정상 = 검색 필터 이상 신호 `이상 신호 없음`(`signal=no`)과 같은 조건. 위험도별 보기에는 이상 신호가 있는 항목만 나온다
- 분류끼리 겹칠 수 있다(대체된 최종 판정은 이상 신호 분류와 판정 실패·대체에 모두, 중간 판정은 이상과 판정 중·중간 판정에 모두, 분류함은 판정 분류와 별개)
- 이전 주소 호환: `category=anomaly`는 이제 이상만(저장 등급 이상 징후 제외, 와이어프레임 sigMatch와 같음)이고 이상 징후는 `category=anomaly_sign`. `signal=yes`(이상 신호 있음)는 둘 다 포함
- 위험도별 `위험도 미정` 항목은 와이어프레임처럼 약하게(흐린 글자·13px) 보인다
- 과거(v3) 판정과 Detector v1·legacy 정책 판정은 evidence v4가 나와도 v3로 남는다(D5) → 등급 null(이상), 이상이면 위험도 미정(U6)

## GET /api/analysis/decisions

- 쿼리(모두 선택): `category`(API 기본 all, 위 14개), `q`(앞뒤 공백 제거 후 1~100자, 제어 문자 거절, 빈 값 = 검색 없음), `signal` yes(이상 신호 있음)|anomaly(이상 분류 조건)|anomaly_sign(이상 징후 분류 조건)|no|unknown, `completeness` complete|partial|unavailable(최종 판정 status, 작업 항목은 제외됨), `sort` newest(기본)|oldest, `page` 1 이상 정수(기본 1), `pageSize` 5|10|20(기본 10). 그 밖의 키·배열 값 400
- 검색: 항목 `id`·`jobId`·거래 ID·프로세스 ID·인터페이스 ID 부분 일치, 대소문자 무시. 와일드카드 없음(`%`·`_`도 글자 그대로)
- 응답: `observedAt`, `counts`(분류 14개 건수, 위 순서 — **DB 전체 기준, 검색·필터와 무관**, 읽음과 무관), `total`(조건에 맞는 건수), `page`, `pageSize`, `latestDecidedAt`(최근 최종 판정 시각, 없으면 null), `latestEventId`(조회 시점 `run_decision_events` 최대 순번 문자열, 없으면 `"0"`), `items[]`(정렬 `occurredAt`·`id` 같은 방향)
- 페이지가 범위를 넘으면 빈 `items` + 실제 `total`(화면이 마지막 페이지로 replace)
- 건수·페이지·커서는 한 REPEATABLE READ 읽기 전용 스냅샷에서 읽는다
- 성능: 요청마다 전체 판정을 훑어 건수를 센다(로컬 일회용 PostgreSQL 17 기준 판정 5만 건 약 0.2초). 판정이 크게 늘면 요약 표·색인 검토(현재 범위 밖)

## GET /api/analysis/decisions/:id

- `:id` = 판정 ID 또는 detect 작업 ID. 작업 ID인데 그 작업의 최종 판정이 있으면 판정을 돌려준다(응답 `id` = 판정 ID, 화면이 주소를 replace). 쿼리 없음
- 응답: 판정 항목 필드 + `workflow`(`state`·`changedAt`) + `configuration`(당시 구성 `id`·`createdAt`·`detectionMode`(`configuration_spec.detectionPolicy.mode`)·`ruleCount`(`ml_config_rules`)·`models[]`(`modelKey`·`adapterName`), 없으면 null) + `job`(detect 작업 `id`·`status`·`attemptCount`·`maxAttempts`·`errorCode`·`createdAt`·`finishedAt`, 작업 없는 이전 형식 판정은 null) + `explanation`(요청자 본인의 이 판정 최근 explain 작업 `jobId`·`status`·`errorCode`·`createdAt`, 없으면 null)

## GET /api/analysis/decisions/:id/interims

- `:id` 해석은 상세와 같다. 응답 `{id, jobId, items[]}`. 작업 없는 이전 형식 판정은 `jobId=null`, `items=[]`
- `items[]`: 그 작업의 `run_decision_interims` 전부, 시도 번호·순번 순. `id`, `attemptId`, `attemptNo`, `seq`, `kind`(first_anomaly·evidence_added·superseded·no_final), `evidenceVersion`, `supersededByAttemptId`, `supersededByAttemptNo`, `reasonCode`(no_final), `createdAt`, `payload`(첫 이상·근거 추가 = 그 시점 전체 스냅샷, 대체됨·최종 없음 = `{}`)
- 중간 판정 기록은 최종 판정을 대신하지 않는다(pipeline.md). 설명 요청 대상은 최종 판정만

## GET /api/analysis/decisions/:id/flow (Process Flow)

- `:id` 해석은 상세와 같다. 응답 `{id, jobId, transaction, processes[], messages[], truncated}`. 조회 시점 저장값(판정 시점 스냅샷 아님)
- 묶는 기준: 판정 PROCESS(`processObservationId`)와 같은 원본(source) 안 같은 거래 ID 문자열. 수집기는 부모 연결 FK(`transaction_observation_id`·`process_observation_id`)를 채우지 않으므로(현재 코드) 문자열로 묶되 화면 표시용이며 부모 연결을 만들거나 저장하지 않는다. TRANSACTION은 검증된 FK가 있으면 그 행
- 실행(execution)마다 가장 큰 리비전 1건. 판정 PROCESS 실행은 판정에 쓰인 관측 그대로(`focus=true`). 노드 목록은 PROCESS 최대 200건·MESSAGE 최대 500건, 넘으면 `truncated=true`. `counts` `{processes, messages, errors}` = 상한과 무관한 실제 건수(errors = 상태 F PROCESS)
- 응답 필드(와이어프레임 선택 노드 원본 정보 항목만): `transaction` `{transactionId, interfaceId, channelId(START_CHANNEL), status, responseCode, processTimeMs(보고된 처리 시간), processCount}`, `processes[]` `{observationId, processId, dependsOn, status, responseCode, totalCount, focus}`, `messages[]` `{observationId, messageId, processId, direction, status, dataSize}`. 숫자는 문자열
- 같은 조회로 만든 실행 그래프(노드·간선)는 형제 경로 `GET …/graph`(process-graph.md). 이 응답은 바뀌지 않는다
- **BODY·응답 메시지 원문·원천 시각 원문은 반환하지 않는다**(admin 포함). 근거: BODY 값 열에 런타임 역할 권한 없음(`database-roles.sql`), BODY는 API 자동 전달 없음(schema.dbml `data_message_bodies` 주석·pipeline.md), 운영 화면 계약도 원본 값·BODY 제외(operations-ui.md). 일반 사용자가 원문 BODY를 보는 계약은 없다

## PUT /api/analysis/decisions/:id/workflow (처리 상태)

- 본문(JSON, 키 정확히 2개): `{state, expectedState}`, 값 detected|open|resolved. 형식 오류 400 `invalid_workflow_request`
- 현재 상태 ≠ `expectedState` → 409 `workflow_state_conflict`(다른 사용자가 먼저 바꿈, 화면은 상세를 다시 읽는다). 허용 전환(와이어프레임 dWorkflow): detected → open·resolved, open → detected·resolved, resolved → detected. 그 밖(같은 상태 포함) 409 `workflow_transition_not_allowed`. 동시 요청은 하나만 성공(나머지 409 `workflow_state_conflict`)
- 성공 200 `{id, workflow: {state, changedAt}}`. 변경 이력 1행(이전·다음 상태·요청자 ID·시각)을 append-only로 남긴다. 판정 값은 바뀌지 않는다

## PUT /api/analysis/decisions/:id/read (읽음 표시)

- 본문(JSON, 키 정확히 1개): `{occurredAt}` = 화면이 본 항목 시각(목록·상세 응답 값 그대로, `YYYY-MM-DDTHH:MM:SS[.소수 1~6자리](Z|±hh:mm)`). 형식·해석 실패 400 `invalid_read_request`
- 열람 시각 = min(`occurredAt`, 지금), 기존 값보다 뒤로 가지 않는다(대상·사용자당 1행). 그래서 화면을 연 뒤 저장된 중간 판정·최종 판정(더 늦은 항목 시각)은 미확인으로 남는다(열람 경합 포함). 200 `{id, readAt}`. 화면은 상세를 열 때 항목(`id`·`occurredAt`)마다 한 번 보낸다

## 조사·조치 기록 — GET·POST /api/analysis/decisions/:id/feedback (admin)

- POST 본문(JSON, 키 정확히 2개 `{kind, reason}` 또는 3개 `{kind, reason, outcome}`). `kind` ∈ action(조치 기록)·suspected-false-positive(오탐 의심)·insufficient-evidence(근거 부족), `reason` 앞뒤 공백 제거 후 1~2000자(줄바꿈·탭 외 제어 문자 거절), `outcome`(확인 결과, 029) ∈ actual-anomaly(실제 이상)·normal(정상)·undetermined(판단 불가) 또는 null — 키 생략·null = 미입력(이전 화면의 키 2개 본문은 그대로 받는다). 그 밖(다른 키, 목록 밖 값·대소문자 다름·빈 문자열·숫자·배열) 400 `invalid_feedback_request`. 성공 201 `{id, item}`
- GET 200 `{id, items[]}`(최신순). 항목 `{id, kind, reason, outcome, actorId, actorEmail, createdAt}`(`outcome` 미입력·029 이전 기록 = null). `actorEmail` = 계정 email(관리자 전용 응답, 계정이 없거나 조회 실패면 null)
- 확인 결과 근거: 기획 "통계적 이상이 진짜 이상인가" 추가 수단 — 기존 조사·조치 기록에 확인 결과(실제 이상·정상·판단 불가)를 누적. 값은 그 기록을 남긴 관리자의 확인이며 기록 유형과 독립(조합 제한 없음). 기록마다 따로 남고(append-only) 판정의 "현재 확인 결과"를 따로 계산하지 않는다
- append-only(수정·삭제 API·권한 없음). 확인 결과도 확정 라벨·학습 입력이 아니다: 판정 값·`data_label_evidence`를 바꾸지 않고 Trainer·LLM·Detector 권한이 없다(와이어프레임 "선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다")

## 화면

- 주소(navigation.md): 목록 조건 `category`·`q`·`signal`·`completeness`·`sort`·`page`·`pageSize`(기본값은 주소에서 생략 — 분류 기본값은 `risk_all`, 잘못된 값은 기본값으로 replace). 상세 `/analysis/d/:decisionId`는 목록 쿼리를 그대로 달고 다녀 `목록` 링크가 같은 조건으로 돌아간다. 분류·필터·정렬·페이지 이동은 push, 검색어는 입력 300ms 뒤 replace
- 사이드바: SideNav 한 벌. 맨 위 보기 전환(`위험도별` / `판정 결과별`, `aria-pressed`, 바꾸면 그 보기의 전체 분류·1쪽), 그 보기의 그룹(`위험도` 5분류 또는 `판정` 7분류) + 구분선 아래 그룹 `분류함`(보류·완료), 건수, 다른 조건 유지·페이지 1. 보기는 분류에서 정한다(위험도 분류 = 위험도별, 판정 분류 = 판정 결과별, 분류함은 직전 보기 유지 — 분류함 주소로 바로 들어오면 와이어프레임 기본 위험도별). 기본 분류는 와이어프레임대로 위험도별 `전체`(`risk_all`, 사용자 11/03 결정): 분류 쿼리가 없는 `/analysis`는 위험도별 전체이고, 판정 결과별 전체는 주소에 `category=all`로 남는다(기존 `category=…` 주소는 그대로 동작). API 기본값은 그대로 `all`이므로 화면은 위험도별 전체일 때 `category=risk_all`을 보낸다. 넓게 보기(브라우저 저장소 `esb_layout_wide_analysis`)는 사이드바를 숨긴다
- 목록: 검색(판정 ID, 거래 ID, 프로세스, 인터페이스), 검색 필터(이상 신호 = 전체·이상·이상 징후·이상 신호 없음·정상 여부 미확인 — 이전 주소 `signal=yes`는 그대로 받아 `이상 신호 있음`(와이어프레임 signalLabel)으로 그 값이 선택돼 있을 때만 보임, 검사 완료, 바로 반영·`조건 초기화`), 목록 제목 `<분류> 판정`, 정렬(최신순·과거순), 요약 줄(검색결과 N건·최근 감지·실시간 연결 상태. 적용 중인 필터는 검색 필터 대화상자의 선택값으로만 보인다), 행(왼쪽: 미확인 점(`title=미확인`, 요청자 기준)·거래 ID, 그 아래 이상 원인 한 줄(`causes`, 사용자 11/13 — 첫 원인 + 더 있으면 ` 외 N건`, 원인이 없으면 줄 없음), Rule/ML 수행 상태·모델·입력 출처(작은 글자) / 오른쪽: 판정 결과·단계·위험도(단계 배지 / `위험도 미정` / `위험도 —`)·검사 완료·시각·판정 ID 앞 8자·프로세스·`인터페이스 <ID> · 채널 <ID>`(오른쪽 아래 작은 글자)). 이상 원인 문구: 상태 규칙 `상태 <코드> <뜻>`(결과 코드 표시와 같음, 응답 코드가 있으면 ` · 응답 코드 <값>`, 사전 메시지가 있으면 뒤에 ` (<메시지>)`), 그 밖의 이상 규칙 = 승인 규칙 이름(예: `시작 시각이 종료 시각보다 늦음`), 이상 징후 규칙 `<필드 표시 이름> <관측값> <단위> (기준 ≥|>|≤|< <임계값> <단위>)`, 과거 등록 Rule `이상 징후 규칙(과거 등록 Rule)`, 머신러닝 모델 `<모델 이름> 원점수 <값> (판정선 <값>)`, 아래(전체 N건·이번 세션 실시간 수신 M건·페이지 번호 창(번호 글자가 접근 가능한 이름, 현재 페이지 `aria-current=page`)·페이지당 5/10/20)
- 실시간: 첫 목록 응답의 `latestEventId` 다음부터 SSE(`/api/pipeline/events?include=interims`, `Last-Event-ID`)를 받아 과거 이벤트를 다시 받지 않는다. 최종 판정(`event: decision`)·중간 판정 기록(`event: interim`) 이벤트가 오면 2초 안의 이벤트를 묶어 목록(건수 포함)과 최종 판정이 아닌 상세·판정 기록을 한 번 다시 조회한다. `이번 세션 실시간 수신 M건`은 두 종류를 함께 센다. 주기 조회 없음, 끊기면 3초 뒤 같은 커서로 재연결. 판정 중(작업 대기) 항목은 이벤트가 없어 다음 재조회 때 바뀐다. 처리 상태·읽음 변경은 SSE가 아니라 변경한 화면의 재조회로만 반영된다(다른 사용자의 변경은 다음 재조회 때)
- 상세 배치(와이어프레임): 상단 줄 `← 목록 · 판정 상세 / 거래 ID` + 처리 상태 버튼 + 넓게 → [Process Flow | AI 판정 근거 요약] → 판정 기록(admin은 저장된 조사·조치 기록을 읽기 전용으로 함께, 아래) → 판정 요약 → 판정 근거(조사·조치 기록 입력은 아래 처리 상태 대화상자 안, 사용자 11/13). 좁은 화면은 같은 순서로 한 줄씩. 열면 읽음 표시(PUT …/read)를 보내고 성공하면 목록을 다시 조회한다(실패는 화면을 막지 않음)
- 처리 상태 버튼(와이어프레임 dWorkflow): user는 감지됨 `[처리 보류][처리 완료]`, 보류 `[원래 상태로 복원][처리 완료]`, 완료 `[원래 상태로 복원]`(바로 변경). admin은 아래 `로그 분류하기`. 성공·실패 모두 상세·목록 재조회, 실패는 `업무 분류 실패 · <오류 코드>`(와이어프레임 항목 이름 업무 분류, 설명 작업 실패 표시와 같은 형식) 그대로이며 재조회로 상태가 바뀌어도 다음 요청 전까지 남는다
  - admin(사용자 11/13): 보류·완료 버튼을 하나로 합친 `[로그 분류하기]`(IconTags)가 가운데 대화상자 `업무 분류`를 연다. 감지됨 `[로그 분류하기]`, 보류 `[원래 상태로 복원][로그 분류하기]`, 완료 `[원래 상태로 복원]`. 대화상자 = 기록 유형·확인 결과(선택)·조사·조치 사유(선택) + 현재 상태에서 허용된 처리 버튼만(감지됨 `[처리 보류][처리 완료]`, 보류 `[처리 완료]`, 주 버튼 처리 완료). 취소 버튼 없음 — X·Esc·바깥 클릭으로 닫는다(처리 중에는 닫지 않음). 안에서 보류·완료를 누르면 사유가 있을 때 기록을 먼저 저장(POST …/feedback, 성공하면 입력을 비움)하고 처리 상태를 바꾼 뒤 닫는다. 기록 저장 실패면 상태를 바꾸지 않고 대화상자 안에 `피드백 저장 실패 · <오류 코드>`, 상태 변경 실패는 대화상자를 연 채 `업무 분류 실패 · <오류 코드>`, 다시 눌러도 이미 저장한 기록은 다시 저장하지 않는다
  - user: 조사·조치 기록 권한이 없어(403 `admin_required`) 대화상자 없이 바로 바꾼다. `원래 상태로 복원`은 누구나 바로 바꾼다
- Process Flow(와이어프레임 dTree·dFlowStats): 머리 `(프로세스 N건 · 메시지 M건 · 에러 K건)`(응답 `counts`의 실제 건수, 에러 = 상태 F PROCESS), 노드 `Transaction: <거래 ID>` → `Focus Process: <ID>`/`Process: <ID>` → `Message: <방향> · <메시지 ID>`(같은 Process ID의 첫 노드 아래, 맞는 노드가 없으면 Process와 같은 단계 끝), 노드 앞 상태 글자. 기본 선택 = Focus Process, 노드를 누르면 `선택 노드 원본 정보` 표(필드·값): TRANSACTION_ID·INTERFACE_ID·CHANNEL·STATUS·DURATION_TIME / PROCESS_ID·DEPENDS_ON·STATUS(`· Code <응답 코드>`)·DURATION_TIME(PROCESS 원천 속성 없음 → `— 확인 불가`)·TOTAL_RECORD_COUNT / MESSAGE_ID·PROCESS_ID·DIRECTION·STATUS·DATA_SIZE(`<n> bytes`). 와이어프레임의 BODY 행은 두지 않는다(조회하지 않음). 값이 없으면 `— 확인 불가`. `truncated`는 화면에 표시하지 않는다(문구 근거 없음). 노드 목록은 상한까지지만 머리 건수는 `counts`의 실제 건수다
- 상세: 판정 요약(결과·단계·위험도(단계가 저장된 이상만) 배지 + 와이어프레임 항목 중 저장값이 있는 것: 판정 ID·판정 시각·당시 룰 버전(v4 `ruleSetRevision`이 있을 때만, 숫자만 표시 — 사용자 11/04)·당시 운영 머신러닝 모델(당시 구성의 모델)·판정 단계·판정 결과(v4 근거가 있으면 `· 근거 <대분류 표시 이름>`)·검사 완료·위험도(이상 아님 `— · 이상일 때만 표시`, 단계 `위험|주의|참고` + `· 결정 근거 <방법>` + `· 단계 없는 근거 <방법>`, 단계 없음 `위험도 미정`, 방법은 표시 이름·ID는 툴팁)·입력 출처·업무 분류(감지됨·보류·완료)·거래 ID·프로세스·인터페이스 · 채널·로그 상태 · 응답 코드(사전 메시지가 있으면 ` (<메시지>)`), 최종 판정은 판정 코드·이상 점수·수행 상태(`Rule … · ML …`) 추가. 처리 시간은 PROCESS 원천 속성이 없어 생략. 아래 `위험도 기준 표 <composite.risk.levelTable>`(v4에 있을 때만)), AI 판정 근거 요약, 판정 기록(와이어프레임 문구: `시도 N · 중간 판정 · 근거 <방법>`(v4 payload의 `basisMethods`), `시도 N · 근거 추가 · <방법>`(같은 시도 직전 기록보다 늘어난 근거 방법). `<방법>`은 표시 이름(detection-decision.md C18·C22): `hard:process-status` → `이상 규칙(상태가 S가 아님)`, `hard:negative-duration` → `이상 규칙(시작 시각이 종료 시각보다 늦음)`, 그 밖의 `hard:*` → `이상 규칙`, `soft:*` → `이상 징후 규칙`, `ml:<key>` → `머신러닝 모델(<key>)`, 알 수 없는 접두는 생략(`근거`·`근거 추가`만). 방법 ID는 툴팁(`title`). 시각 칸은 시각, 진행 중 줄만 `진행 중`, 시각 없는 그 밖의 줄은 `—`, `시도 N 결과 · 대체됨`, `시도 N 실패 · 최종 없음(판정 실패)`(사유 코드는 툴팁), 최종은 검사 완료면 `최종 · 모든 방법 종료`·부분·판정불가면 `최종`(기한 도달·실패를 구분할 저장값 없음, 검사 결과는 요약에), 진행 중 줄 `진행 중 · 남은 방법 결과 대기`는 작업이 queued·running일 때만, 그 밖의 작업 상태·작업 없음은 줄 없음), 판정 근거(최종 판정 또는 v4 evidence가 있는 중간 판정. 최종은 미판정 사유 `DecisionDiagnostics` 먼저):
  - v4: `종합 근거` 표(대분류·켠 방법(`categoryResults.n`, 없으면 방법 수)·이상·정상 확인·미판정·실행 중(결과 대기 방법)·대분류 결과(저장 `categoryResults.result`: 이상·정상 확인·미판정(결과 대기 방법이 있으면 판정 중)·꺼짐), 캡션 `대분류 결과를 합산하거나 점수로 바꾸지 않습니다`) + `세부 방법별 결과` 표(와이어프레임 dMethods 열: 대분류·방법(표시 이름, ID 툴팁)·실행 상태(완료·실패·미완료, 결과 대기 `not_ready`+`cause=pending`은 실행 중)·결과(이상·정상 확인·미판정, 결과 대기 —)·측정값(이상 징후 규칙 `<필드> <관측값> <단위>`, 머신러닝 모델 `원점수 <raw>`(+ `· 판정선 대비 <threshold_ratio>배`), 상태 규칙 `상태 <관측 상태>`(v3 키 `rules[]`), 그 밖 이상 결과 코드)·당시 기준(이상 징후 규칙 `<필드> ≥|>|≤|< <임계값> <단위>`, 이상 규칙 4.3 conditionText, 머신러닝 모델 `판정선 <threshold>`)·위험도 단계(이상이 아니면 —, 저장 `riskLevel`, 이상인데 단계 없음(ML) `단계 없음`, 사유 툴팁)·처리 시간(`elapsedMs` ms, C24)·사유(`cause=timeout` → 기한 초과(코드 툴팁), 그 밖 원인·세부 코드 그대로, 판단 안 함 `reason` 코드), 캡션 `머신러닝 모델 점수는 모델마다 척도가 달라 모델끼리 비교하거나 위험도에 반영하지 않습니다`). `categoryResults.*.k`·`composite.policy`·`detectionPolicy`는 보이지 않는다(C23)
  - v3(최종만): `Rule 판정 근거` 표(방법·버전·결과·측정값(`상태 <관측 상태>`)·위험도 단계) — 방법은 상태 규칙 `이상 규칙(상태가 S가 아님)`, 그 밖의 등록 Rule `이상 징후 규칙(과거 등록 Rule)`(U6), Rule 키는 툴팁 — + `ML 판정 근거` 표. 값이 없으면 `— 확인 불가` 또는 `—`
- 조사·조치 기록(admin만, 와이어프레임 feedbackBox 항목): 입력은 위 `로그 분류하기` 대화상자 안 — 기록 유형(조치 기록·오탐 의심·근거 부족)·확인 결과(029, 기획 문구 그대로 `실제 이상`·`정상`·`판단 불가`, 기본 `—` = 미입력)·조사·조치 사유(선택, 최대 2000자, 자리표시 `확인한 사실과 조치 사유를 입력하세요.`)·대화상자 설명 `선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다.`. 빈 사유면 기록 없이 상태만 바꾼다(별도 `피드백 저장` 버튼 없음). 확인 결과는 기록에 붙으므로(사유 1~2000자) 확인 결과를 고르고 사유가 비면 저장·상태 변경 없이 사유 아래 `조사·조치 사유를 입력하세요.`(와이어프레임 feedbackBox 빈 사유 안내 그대로)를 보이고 고른 값은 남긴다 — 사유를 넣거나 확인 결과를 `—`로 돌리면 안내가 사라진다. 확인 결과를 고르지 않으면 본문에 `outcome` 키를 보내지 않는다. 기록 저장에 성공하면 사유와 확인 결과를 함께 비운다(다시 눌러도 두 번 저장하지 않음). 저장된 기록은 대화상자가 아니라 판정 기록 카드에 읽기 전용 줄로 시각 순으로 함께 보인다(사용자 11/13): `<시각>` / `<유형> · <작성자 email>`(확인 결과가 있으면 `<유형> · 확인 결과 <값> · <작성자 email>`) / 사유(줄바꿈 그대로). 기록은 상세를 열 때 읽고(GET …/feedback), 읽기 실패는 카드 안에 `조사·조치 기록 미연결` 등 오류 그대로 + 다시 시도. user는 읽지 않는다(판정 기록 그대로)
- 판정 기한(`detectionPolicy.deadlineMs`)·정책 k는 판정 정책 값이라 숫자를 화면에 보이지 않는다(와이어프레임 사용자 9.8). 미판정 사유는 이름(예: 처리 기한 초과)만
- AI 판정 근거 요약(설명 작업 `explain`, 사용자 11/04 이름 변경): 최종 판정만 요청(그 외·데모 판정은 버튼 비활성 + 사유). 요청 → `GET /api/jobs/:id`를 queued·running일 때만 5초 주기·`요청 중` 표시, 그 밖의 상태(blocked 등)는 주기 조회 없이 상태 코드 그대로, 실패는 작업 상태·오류 코드 그대로 + `설명 다시 요청`. 상세 `explanation`이 있으면 그 작업을 이어서 보여 주고, 그 작업을 읽는 중이거나 읽기에 실패하면 오류를 그대로 보이고 새 요청 버튼을 막는다(중복 요청 방지). LLM 생성 서비스가 꺼져 있으면(`GET /api/llm/status`, 화면에 들어갈 때 1회 + 30초마다, [llm-recommendation-job.md](llm-recommendation-job.md) 가용성 절) 요청 버튼 비활성 + `설명 요청 · generation_service_unavailable`, 요청 중인 작업은 `요청 중`을 두고 상태 줄에 ` · generation_service_unavailable`을 덧붙인다. 상태 조회 실패는 막지 않는다(접수 503)
- 프로토콜 경로 설명(관리자, [protocol-explanation-job.md](protocol-explanation-job.md) 11절 확정 문구): 최종 판정의 `evidence.protocolReview`가 알림 목록과 같은 판별을 통과하면(프로토콜 알림에서 이동한 판정) AI 판정 근거 요약 아래 "LLM 설명" 칸 [설명 보기] → `explain_protocol` 알림 대상(`decisionId` = 이 판정). 결과·진행 중·실패(`설명 실패 · <코드>`)·LLM 꺼짐(`설명 · generation_service_unavailable`)은 그 절과 같다. user에게는 칸도 요청도 없다
- 장식 아이콘(사용자 11/04·11/05, #100 이전 화면 아이콘 복원, Tabler·`aria-hidden`, `frontend/src/features/analysis/icons.ts`): 사이드바 전 그룹과 목록 제목의 분류 아이콘(전체 IconInbox·위험 IconFlame·주의 IconAlertTriangle·참고 IconInfoCircle·위험도 미정 IconHelpCircle·이상 IconAlertOctagon·이상 징후 IconAlertCircle·정상 IconShieldCheck·판정 중 IconClock·미확인 IconQuestionMark·실패·대체 IconCircleX·보류 IconFolderOpen·완료 IconCircleCheck), 목록 줄·판정 요약 제목 = 저장된 위험도 단계 아이콘(단계가 있는 이상만, 배지와 같은 글자 색), 처리 상태 버튼(원래 상태로 복원 IconRefresh·처리 보류 IconFolderOpen·처리 완료 IconCircleCheck·로그 분류하기 IconTags(이전 대응 없음, 사용자 11/13 새 버튼)), Process Flow 제목 IconBinaryTree·노드(Transaction IconDatabase·Process IconCodeDots(상태 F면 오류 색)·Message IconMessage2)·`선택 노드 원본 정보` IconListDetails, AI 판정 근거 요약 제목·설명 요청 버튼 IconBrain. 판정 기록·판정 근거·조사·조치 기록은 이전 대응 아이콘이 없어 두지 않는다
- evidence 읽기(`frontend/src/features/analysis/data/evidence.ts`): v3(`rules`·`models`·`checkers`·`detectionPolicy`·`input`·`demo`) + v4 읽기(`methodResults[]`·`categoryResults`·`composite`, schemaVersion 4·`composite`가 없으면 null → v3 표시). 형식이 틀린 항목은 버린다
- 범위 밖(별도 Issue): WINDOW 판정 상세, 관리자 링크(당시 모델·평가), 와이어프레임 종합 근거 설명 문단(dCatsNote), 이상 분석 메뉴의 미확인 건수 표시, 조사·조치 기록 수정·삭제, 확인 결과의 목록·분류함 표시·필터·집계(정밀도 추정)·"정상" 유형의 예외 규칙 후보 제안(기획에 화면·문구 미정)
- 폐지: `/analysis/process-decisions`(저장된 탐지 판정 화면 → `/analysis`로 replace), 옛 실시간 이상 이벤트 분류·상세 화면과 그 화면이 부르던 `GET /api/anomaly/events/:id/detail`·`POST …/feedback`(백엔드 미구현) 호출. `/api/anomaly/*` 백엔드·알림 패널의 실시간 이벤트(데모 SSE)는 그대로이며 알림 항목은 `/analysis`로 이동한다
