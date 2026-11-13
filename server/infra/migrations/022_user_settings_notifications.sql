-- 022: 사용자별 설정·알림 읽음 상태(WebUI 알림 패널·설정 › 알림·운영 현황 위젯 배치). API만 읽고 쓴다.
-- 새 표 4개만 만든다. 001~021 표·함수·트리거·enum·권한은 바꾸지 않는다. 새 함수·트리거·시퀀스 없음.
-- 전제: 001~021 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용.
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음(되돌리기 = 새 표 4개 DROP, 다른 표 영향 없음).
--   잠금: 다른 표를 참조하지 않으므로(FK 없음) 기존 표를 잠그지 않는다. 적용 중 작업자를 멈출 필요 없음.
-- 사용자 키: 계정은 DB가 아니라 API 파일 저장소에 있다 → 인증된 계정 id 문자열(actor_id)로 둔다(014 run_decision_reads.actor_id와 같음). users 표 FK 없음.
--   계정을 지워도 행은 남는다(같은 id가 다시 생기지 않는 한 읽히지 않음). 정리는 운영 절차(README 022 절).
-- 알림 항목 자체는 저장하지 않는다: API가 읽을 때 run_decision_events(006·012)·판정·중간 기록·장애 묶음(019)에서 만든다(contracts/notifications.md).
--   알림 시각 = run_decision_events.id(GENERATED identity, advisory lock 81736 아래 → id 순서 = 커밋 순서). 읽음 상태는 이 순번으로 저장한다.
-- 표:
--   ops_user_notification_settings  알림 설정 3값(판정 기준 3택·위험도 하한·위험도 미정 포함). 행 없음 = 기본값(interim·info·true, API가 응답).
--   ops_user_widget_layouts         화면별 위젯 배치 문서(지금은 operations_overview 하나). 형식 검사는 API(contracts/notifications.md), DB는 객체·크기만.
--   ops_user_notification_cursors   "모두 읽음" 순번: 알림 순번이 read_through 이하인 항목은 읽음.
--   ops_user_notification_reads     항목별 읽음: 그 항목의 알림 순번이 read_seq 이하이면 읽음(항목이 다시 알리면 = 순번이 커지면 다시 읽지 않음).
BEGIN;

-- 1. 알림 설정. 값 이름·의미는 Detector 위험도 틀 Q-N1·Q-N1-1(contracts/notifications.md "알림 설정").
CREATE TABLE ops_user_notification_settings (
 actor_id text PRIMARY KEY CHECK (actor_id <> '' AND length(actor_id) <= 128),
 decision_basis text NOT NULL CHECK (decision_basis IN ('interim','final_any','final_complete')),
 min_risk_level text NOT NULL CHECK (min_risk_level IN ('info','warning','critical')),
 include_unrated boolean NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- 2. 위젯 배치. layout = API가 검사한 {version, items[]} 객체 그대로(16KB 상한은 API 본문 상한과 같음).
CREATE TABLE ops_user_widget_layouts (
 actor_id text NOT NULL CHECK (actor_id <> '' AND length(actor_id) <= 128),
 screen text NOT NULL CHECK (screen IN ('operations_overview')),
 layout jsonb NOT NULL CHECK (jsonb_typeof(layout) = 'object' AND octet_length(layout::text) <= 16384),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (actor_id, screen)
);

-- 3. 모두 읽음 순번(사용자당 1행). 값은 run_decision_events.id 범위(API가 현재 최대 id 이하로만 쓴다, 줄이지 않음).
CREATE TABLE ops_user_notification_cursors (
 actor_id text PRIMARY KEY CHECK (actor_id <> '' AND length(actor_id) <= 128),
 read_through bigint NOT NULL CHECK (read_through >= 0),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- 4. 항목별 읽음. item_key = 'incident:<사건 id>' | 'job:<detect 작업 id>' | 'decision:<작업 없는 판정 id>'(contracts/notifications.md).
--    API가 read_through 이하이거나 조회 범위 밖으로 밀려난 행을 지운다(DELETE 권한) → 사용자당 행 수는 최근 알림 수 이하.
CREATE TABLE ops_user_notification_reads (
 actor_id text NOT NULL CHECK (actor_id <> '' AND length(actor_id) <= 128),
 item_key text NOT NULL CHECK (item_key ~ '^(incident|job|decision):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
 read_seq bigint NOT NULL CHECK (read_seq >= 0),
 read_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (actor_id, item_key)
);

COMMIT;
