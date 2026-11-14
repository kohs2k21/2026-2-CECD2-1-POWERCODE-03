-- 028: 작업 알림(학습·비교 평가 종료) 읽음 상태와 알림 설정 열(WebUI 알림 패널·탐지 설정 메뉴 배지, 와이어프레임 badgeDetect·notifJobs). API만 읽고 쓴다.
-- 022 표 2개에 열 1개씩 더하고 새 표 1개를 만든다. 001~027 표·함수·트리거·enum·권한 정의는 바꾸지 않는다. 새 함수·트리거·시퀀스·색인 없음.
-- 전제: 022·026 적용 뒤 1회 실행(023~025·027과 의존 없음)(psql -v ON_ERROR_STOP=1 -f), 022 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용(새 표 GRANT 1줄).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음
--   (되돌리기 = 새 표 DROP + 두 열 DROP COLUMN, 이전 API는 이 열·표를 읽지 않는다).
--   잠금: 022 설정·순번 표에 ACCESS EXCLUSIVE를 잠깐(상수 DEFAULT·NULL 열이라 행 재작성 없음, 검사할 CHECK 없음). 새 표는 FK가 없어 run_jobs 등 다른 표를 잠그지 않는다.
--   작업자를 멈출 필요 없음.
--   기존 API 호환: 기존 설정 행은 include_jobs DEFAULT true를 받는다. 이 열을 모르는 이전 API의 INSERT는 DEFAULT를, upsert는 이 열을 건드리지 않아 저장값이 유지된다.
--   → migration·역할 스크립트를 API보다 먼저 적용한다(새 API는 이 열·표를 읽으므로 없으면 알림·설정 조회가 503).
-- 알림 항목은 저장하지 않는다(022와 같음): API가 읽을 때 run_jobs에서 만든다(contracts/notifications.md "작업 알림" 절).
--   대상 = v03 training 큐의 train·compare 작업 중 succeeded·failed로 끝난 것(finished_at 기준 조회 기간 안), 관리자만·include_jobs가 켜진 계정만.
--   run_jobs 색인을 더하지 않는 근거: API는 contract_version='v03' AND queue='training' AND status IN (...) 조건으로 016 run_jobs_v03_queue_idx
--   (queue,status,…) 앞부분을 쓴다. training 큐 행은 사람이 요청한 작업뿐이라(판정 작업 detect는 detection 큐) 수가 작다.
-- 읽음 판단(판정 알림의 run_decision_events 순번과 별도 축 — 작업 종료는 그 순번을 받지 않는다): 종료 시각(run_jobs.finished_at) 기준.
--   미확인 = finished_at > max(모두 읽음 작업 시각 jobs_read_through, 그 작업 읽음 시각 read_through).
-- 표·열:
--   ops_user_notification_settings.include_jobs  작업 알림 켜기(기본 true, 와이어프레임 관리자 기본값). 일반 사용자에게는 API가 작업 알림을 만들지 않는다.
--   ops_user_notification_cursors.jobs_read_through  "모두 읽음"의 작업 종료 시각 경계(NULL = 없음). 화면이 본 응답의 latestJobAt을 보낸다(API가 지금 시각 이하로 줄이고 줄이지 않음).
--   ops_user_job_notification_reads  작업 알림 항목별 읽음: 그 작업의 finished_at이 read_through 이하이면 읽음(다시 끝나면 = 시각이 커지면 다시 미확인).
BEGIN;

-- 1. 알림 설정: 작업 알림 켜기.
ALTER TABLE ops_user_notification_settings ADD COLUMN include_jobs boolean NOT NULL DEFAULT true;

-- 2. 모두 읽음: 작업 종료 시각 경계(판정 알림 순번 read_through와 같은 요청에서 함께 쓴다).
ALTER TABLE ops_user_notification_cursors ADD COLUMN jobs_read_through timestamptz;

-- 3. 작업 알림 항목별 읽음. job_id = run_jobs.id(FK 없음: run_jobs를 잠그지 않고, 작업 행이 없어지면 API 정리 DELETE가 지운다).
--    API가 모두 읽음 경계 이하이거나 최대 조회 기간(30일)보다 오래된 작업의 행을 지운다(DELETE 권한) → 사용자당 행 수는 최근 작업 알림 수 이하.
CREATE TABLE ops_user_job_notification_reads (
 actor_id text NOT NULL CHECK (actor_id <> '' AND length(actor_id) <= 128),
 job_id uuid NOT NULL,
 read_through timestamptz NOT NULL,
 read_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (actor_id, job_id)
);

COMMIT;
