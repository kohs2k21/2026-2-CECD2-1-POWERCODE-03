Created: 2026-11-03T18:00:00+09:00
Updated: 2026-11-03T18:00:00+09:00
Author: #105 Claude Code(WebUI builder)
Status: current

# 이상 분석 판정 목록·상세 UI 계약

- 범위: `/analysis` 판정 결과별 목록, `/analysis/d/:decisionId` PROCESS 판정 상세(#105, 상위 #101). 화면 기준 와이어프레임 #93 `section[data-view="analysis"]`
- 연결: `GET /api/analysis/*` 읽기 API 3개(`backend/src/routes/analysisRoutes.ts`·`services/analysisReadModel.ts`). 쓰기 경로 없음. 기존 `GET /api/pipeline/decisions`·SSE `GET /api/pipeline/events`는 그대로
- 인증: 기존 Bearer. 없음·잘못된 토큰 401, user/admin 모두 조회. 설명 요청은 기존 `POST /api/jobs {kind:"explain"}`(pipeline.md)
- 저장소: 기존 테이블만 조회(마이그레이션 없음). 판정 값은 저장값 그대로 반환하고 다시 계산하지 않는다. 조회 실패 503 `analysis_storage_unavailable`(오류 원문 미노출)
- 잘못된 요청 400: `unknown_analysis_filter`, `invalid_category`, `invalid_signal`, `invalid_completeness`, `invalid_sort`, `invalid_page`, `invalid_page_size`, `invalid_q`, `invalid_decision_id`. 없는 판정 404 `decision_not_found`
- 화면 오류 제목: 404 `decision_not_found` = `<항목> · decision_not_found`(오류 코드 그대로), 그 외 404·503 = `<항목> 미연결`, 기타 = 서버 메시지 또는 조회 실패. 재조회 실패 시 이전 결과 + `<오류 제목> · 이전 관측값 표시`. 실패를 빈 목록·성공으로 바꾸지 않는다

## 판정 항목

판정 항목 = 최종 판정 행(`run_process_decisions`) + 최종 판정이 없는 v03 detect 작업(`run_jobs` kind=detect, status≠succeeded — succeeded detect 작업은 판정 행이 반드시 있음).

| `kind` | 출처 | `id` | `stage` | `signal` |
|---|---|---|---|---|
| `final` | 판정 행 | 판정 ID | `final`, 같은 작업에 `superseded` 중간 기록이 있으면 `replaced` | 아래 이상 신호 규칙 |
| `interim` | 작업(queued·running·blocked) + 닫히지 않은 첫 이상·근거 추가 기록 | 작업 ID | 마지막 열린 기록이 `evidence_added`면 `updated`, 아니면 `provisional` | `yes`(중간 기록은 종합 이상일 때만 쓴다) |
| `pending` | 작업(queued·running·blocked), 열린 중간 기록 없음 | 작업 ID | `pending` | `pending` |
| `failed` | 작업 failed(판정 행 없음, 중간 기록 유무 무관) | 작업 ID | `failed` | `failed` |

- 이상 신호(`final`): 운영 리포트 `operational-v1`과 같은 규칙. `yes` = status≠`unavailable` ∧ `evidence.anomalous=true` / `no` = status=`complete` ∧ `evidence.anomalous=false` / `unknown` = 그 밖(부분+false, 판정불가, anomalous 없음·null). 미완료를 정상으로 바꾸지 않는다
- 열린 기록: 같은 시도에 `superseded`·`no_final`이 없는 `first_anomaly`·`evidence_added`. 여러 시도에 있으면 시도 번호·순번이 가장 큰 기록(새 시도가 아직 첫 이상을 쓰지 않으면 이전 시도 기록이 열린 채 남는다, 012 규칙)
- `occurredAt`: final = `decided_at`, interim = 마지막 열린 기록 `created_at`, pending = 작업 `created_at`, failed = 작업 `finished_at`(없으면 `created_at`). 정렬 기준
- 문맥: `processObservationId`(작업은 입력 manifest의 PROCESS)의 `data_processes`(거래 ID·프로세스 ID·채널·로그 상태·응답 코드)와 그 TRANSACTION `data_transactions.interface_id`. 없으면 null

항목 필드: `id`, `kind`, `stage`, `signal`, `decisionId`(final만), `jobId`, `jobStatus`(작업 항목만), `sourceId`, `processObservationId`, `configVersionId`(작업은 입력 구성), `status`·`ruleStatus`·`mlStatus`·`decisionCode`·`anomalyScore`(final만, 그 외 null. 점수는 문자열), `evidence`(final = 판정 evidence, interim = 마지막 열린 기록 payload, pending·failed = null), `occurredAt`, `decidedAt`(final만), `transactionId`, `processId`, `interfaceId`, `channelId`, `processStatus`, `responseCode`. 시각은 UTC ISO(마이크로초).

## 분류(사이드바 판정 결과별)

| `category` | 화면 | 조건 |
|---|---|---|
| `all` | 전체 | 모든 항목 |
| `anomaly` | 이상 | `signal=yes`(final 이상 + interim) |
| `in_progress` | 판정 중·중간 판정 | `kind` ∈ pending·interim |
| `undetermined` | 정상 여부 미확인 | `signal=unknown` |
| `failed_replaced` | 판정 실패·대체 | `stage` ∈ failed·replaced |

- 사이드바 분류(순서 그대로): 전체 / 이상 / 판정 중·중간 판정 / 정상 여부 미확인 / 판정 실패·대체. 와이어프레임 판정 결과별 보기에서 이상 징후만 보류(evidence v4 구현 뒤 별도 Issue). 정상(이상 신호 없음) 분류는 와이어프레임에 없어 두지 않는다 — 정상 판정은 전체에 있고 검색 필터 이상 신호 = `이상 신호 없음`(`signal=no`)으로 찾는다
- 분류끼리 겹칠 수 있다(대체된 최종 판정은 이상 신호 분류와 판정 실패·대체에 모두, 중간 판정은 이상과 판정 중·중간 판정에 모두)
- 이상 / 이상 징후 구분·위험도별 보기는 evidence v4(`detection-decision.md` #92) 구현 뒤 별도 Issue. 현재 v3 판정에는 등급이 없어 추정하지 않고 이상 신호가 있으면 `이상`으로 보인다. v4 `composite.grade=anomaly_sign`이 저장된 항목만 결과 문구가 `이상 징후`

## GET /api/analysis/decisions

- 쿼리(모두 선택): `category`(기본 all), `q`(앞뒤 공백 제거 후 1~100자, 제어 문자 거절, 빈 값 = 검색 없음), `signal` yes|no|unknown, `completeness` complete|partial|unavailable(최종 판정 status, 작업 항목은 제외됨), `sort` newest(기본)|oldest, `page` 1 이상 정수(기본 1), `pageSize` 5|10|20(기본 10). 그 밖의 키·배열 값 400
- 검색: 항목 `id`·`jobId`·거래 ID·프로세스 ID·인터페이스 ID 부분 일치, 대소문자 무시. 와일드카드 없음(`%`·`_`도 글자 그대로)
- 응답: `observedAt`, `counts`(분류 5개 건수 — **DB 전체 기준, 검색·필터와 무관**), `total`(조건에 맞는 건수), `page`, `pageSize`, `latestDecidedAt`(최근 최종 판정 시각, 없으면 null), `latestEventId`(조회 시점 `run_decision_events` 최대 순번 문자열, 없으면 `"0"`), `items[]`(정렬 `occurredAt`·`id` 같은 방향)
- 페이지가 범위를 넘으면 빈 `items` + 실제 `total`(화면이 마지막 페이지로 replace)
- 건수·페이지·커서는 한 REPEATABLE READ 읽기 전용 스냅샷에서 읽는다
- 성능: 요청마다 전체 판정을 훑어 건수를 센다(로컬 일회용 PostgreSQL 17 기준 판정 5만 건 약 0.2초). 판정이 크게 늘면 요약 표·색인 검토(현재 범위 밖)

## GET /api/analysis/decisions/:id

- `:id` = 판정 ID 또는 detect 작업 ID. 작업 ID인데 그 작업의 최종 판정이 있으면 판정을 돌려준다(응답 `id` = 판정 ID, 화면이 주소를 replace). 쿼리 없음
- 응답: 판정 항목 필드 + `configuration`(당시 구성 `id`·`createdAt`·`detectionMode`(`configuration_spec.detectionPolicy.mode`)·`ruleCount`(`ml_config_rules`)·`models[]`(`modelKey`·`adapterName`), 없으면 null) + `job`(detect 작업 `id`·`status`·`attemptCount`·`maxAttempts`·`errorCode`·`createdAt`·`finishedAt`, 작업 없는 이전 형식 판정은 null) + `explanation`(요청자 본인의 이 판정 최근 explain 작업 `jobId`·`status`·`errorCode`·`createdAt`, 없으면 null)

## GET /api/analysis/decisions/:id/interims

- `:id` 해석은 상세와 같다. 응답 `{id, jobId, items[]}`. 작업 없는 이전 형식 판정은 `jobId=null`, `items=[]`
- `items[]`: 그 작업의 `run_decision_interims` 전부, 시도 번호·순번 순. `id`, `attemptId`, `attemptNo`, `seq`, `kind`(first_anomaly·evidence_added·superseded·no_final), `evidenceVersion`, `supersededByAttemptId`, `supersededByAttemptNo`, `reasonCode`(no_final), `createdAt`, `payload`(첫 이상·근거 추가 = 그 시점 전체 스냅샷, 대체됨·최종 없음 = `{}`)
- 중간 판정 기록은 최종 판정을 대신하지 않는다(pipeline.md). 설명 요청 대상은 최종 판정만

## 화면

- 주소(navigation.md): 목록 조건 `category`·`q`·`signal`·`completeness`·`sort`·`page`·`pageSize`(기본값은 주소에서 생략, 잘못된 값은 기본값으로 replace). 상세 `/analysis/d/:decisionId`는 목록 쿼리를 그대로 달고 다녀 `목록` 링크가 같은 조건으로 돌아간다. 분류·필터·정렬·페이지 이동은 push, 검색어는 입력 300ms 뒤 replace
- 사이드바: SideNav 한 벌(그룹 `판정`, 5분류 + 건수, 다른 조건 유지·페이지 1). 넓게 보기(브라우저 저장소 `esb_layout_wide_analysis`)는 사이드바를 숨긴다
- 목록: 검색(판정 ID, 거래 ID, 프로세스, 인터페이스), 검색 필터(이상 신호·검사 완료, 바로 반영·`조건 초기화`), 정렬(최신순·과거순), 요약 줄(검색결과 N건·최근 감지·실시간 연결 상태. 적용 중인 필터는 검색 필터 대화상자의 선택값으로만 보인다), 행(거래 ID·인터페이스·채널·Rule/ML 수행 상태·모델·입력 출처 / 판정 결과·단계·검사 완료·시각·판정 ID 앞 8자·프로세스), 아래(전체 N건·이번 세션 실시간 수신 M건·페이지 번호 창(번호 글자가 접근 가능한 이름, 현재 페이지 `aria-current=page`)·페이지당 5/10/20)
- 실시간: 첫 목록 응답의 `latestEventId` 다음부터 SSE(`Last-Event-ID`)를 받아 과거 이벤트를 다시 받지 않는다. 이벤트가 오면 2초 안의 이벤트를 묶어 목록(건수 포함)과 최종 판정이 아닌 상세·판정 기록을 한 번 다시 조회한다. 주기 조회 없음, 끊기면 3초 뒤 같은 커서로 재연결. SSE는 최종 판정 이벤트만 보내므로(`pipeline.md`) 판정 중·중간 판정 항목은 다음 재조회 때 바뀐다
- 상세 배치: [판정 요약 | AI 설명] → 판정 기록 → 판정 근거(와이어프레임의 Process Flow 자리는 보류라 판정 요약이 그 자리에 온다). 좁은 화면은 같은 순서로 한 줄씩
- 상세: 판정 요약(결과·단계 배지 + 와이어프레임 항목 중 저장값이 있는 것: 판정 ID·판정 시각·당시 룰 리비전(v4 `ruleSetRevision`이 있을 때만)·당시 운영 머신러닝 모델(당시 구성의 모델)·판정 단계·판정 결과·검사 완료·입력 출처·거래 ID·프로세스·인터페이스 · 채널·로그 상태 · 응답 코드, 최종 판정은 판정 코드·이상 점수·수행 상태(`Rule … · ML …`) 추가. 위험도·업무 분류·처리 시간은 생략), AI 설명, 판정 기록(와이어프레임 문구: `시도 N · 중간 판정 · 근거 <방법>`(v4 payload의 `basisMethods`), `시도 N · 근거 추가 · <방법>`(같은 시도 직전 기록보다 늘어난 근거 방법). `<방법>`은 표시 이름(detection-decision.md C18·C22): `hard:process-status` → `이상 규칙(상태가 S가 아님)`, `hard:negative-duration` → `이상 규칙(시작 시각이 종료 시각보다 늦음)`, 그 밖의 `hard:*` → `이상 규칙`, `soft:*` → `이상 징후 규칙`, `ml:<key>` → `머신러닝 모델(<key>)`, 알 수 없는 접두는 생략(`근거`·`근거 추가`만). 방법 ID는 툴팁(`title`). 시각 칸은 시각, 진행 중 줄만 `진행 중`, 시각 없는 그 밖의 줄은 `—`, `시도 N 결과 · 대체됨`, `시도 N 실패 · 최종 없음(판정 실패)`(사유 코드는 툴팁), 최종은 검사 완료면 `최종 · 모든 방법 종료`·부분·판정불가면 `최종`(기한 도달·실패를 구분할 저장값 없음, 검사 결과는 요약에), 진행 중 줄 `진행 중 · 남은 방법 결과 대기`는 작업이 queued·running일 때만, 그 밖의 작업 상태·작업 없음은 줄 없음), 판정 근거(최종만: 미판정 사유 `DecisionDiagnostics`, evidence `rules[]` 표(와이어프레임 dMethods 열 이름: 방법·버전·결과·측정값(`상태 <관측 상태>`)·위험도 단계)·`models[]` 표). 값이 없으면 `— 확인 불가` 또는 `—`
- 판정 기한(`detectionPolicy.deadlineMs`)·정책 k는 판정 정책 값이라 숫자를 화면에 보이지 않는다(와이어프레임 사용자 9.8). 미판정 사유는 이름(예: 처리 기한 초과)만
- AI 설명: 최종 판정만 요청(그 외·데모 판정은 버튼 비활성 + 사유). 요청 → `GET /api/jobs/:id`를 queued·running일 때만 5초 주기·`요청 중` 표시, 그 밖의 상태(blocked 등)는 주기 조회 없이 상태 코드 그대로, 실패는 작업 상태·오류 코드 그대로 + `설명 다시 요청`. 상세 `explanation`이 있으면 그 작업을 이어서 보여 주고, 그 작업을 읽는 중이거나 읽기에 실패하면 오류를 그대로 보이고 새 요청 버튼을 막는다(중복 요청 방지)
- evidence 읽기(`frontend/src/features/analysis/data/evidence.ts`): v3(`rules`·`models`·`checkers`·`detectionPolicy`·`input`·`demo`) + v4 읽기(`methodResults[]`·`categoryResults`·`composite`, schemaVersion 4·`composite`가 없으면 null → v3 표시). 형식이 틀린 항목은 버린다
- 범위 밖(별도 Issue): 처리 상태 버튼(보류·완료)·분류함, 조사·조치 기록, Process Flow 트리, 미확인 표시, 이상 징후·위험도별 보기, 대분류·방법별 결과 표(v4 구현 후), WINDOW 판정 상세, 관리자 링크(당시 모델·평가)
- 폐지: `/analysis/process-decisions`(저장된 탐지 판정 화면 → `/analysis`로 replace), 옛 실시간 이상 이벤트 분류·상세 화면과 그 화면이 부르던 `GET /api/anomaly/events/:id/detail`·`POST …/feedback`(백엔드 미구현) 호출. `/api/anomaly/*` 백엔드·알림 패널의 실시간 이벤트(데모 SSE)는 그대로이며 알림 항목은 `/analysis`로 이동한다
