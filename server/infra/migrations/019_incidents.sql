-- 019: 거래 간 장애 묶음(층 C). Detector가 쓰고 API가 읽는다.
-- 새 표 4개·트리거 4개만 만든다. 001~018 표·함수·트리거·enum·권한은 바꾸지 않는다. 새 함수 없음(004 erd_v03_immutable() 재사용).
-- 전제: 001~018 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용.
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음.
--   잠금: 새 표의 FK 생성이 ops_sources·run_process_decisions에 짧은 SHARE ROW EXCLUSIVE를 잡는다(014와 같음). 적용 중 Detector 판정 INSERT가 잠시 기다릴 수 있다.
-- 표:
--   run_incident_runs    Detector 주기 실행 1회 = 1행. 실행 트랜잭션 끝에 INSERT. cursor_from·cursor_to = 그 실행이 읽은 run_decision_events.id 범위.
--   run_incidents        장애 묶음. 끝 시각·구성원 수는 저장하지 않는다(읽을 때 계산). continues_from = 1시간 구간으로 끊긴 앞 구간(생성 시 확정, 사슬은 일직선).
--   run_incident_members 판정 1건은 최대 1개 묶음에 속한다(decision_id PK). Detector는 INSERT … ON CONFLICT (decision_id) DO NOTHING으로 넣는다.
--   run_incident_events  병합 기록. kind는 merged_into 하나(text + CHECK, 늘릴 때 CHECK만 교체). 묶음당 merged_into 최대 1건(partial UNIQUE).
-- added_by_run FK(묶음·구성원·이벤트 → 실행)는 DEFERRABLE INITIALLY DEFERRED: 실행 행을 같은 트랜잭션 마지막에 넣으므로 검사는 COMMIT 때 한다.
--   실행 행이 끝내 없으면 COMMIT이 23503으로 실패하고 트랜잭션 전체가 되돌아간다.
-- 불변: 4표 모두 BEFORE UPDATE OR DELETE OR TRUNCATE FOR EACH STATEMENT 트리거(015 방식, 006의 행 단위보다 넓게 TRUNCATE·0행 문장까지 거부) + 권한은 SELECT·INSERT만.
-- 직렬화: Detector가 묶음 계산 트랜잭션에서 pg_try_advisory_xact_lock(81741, 0)을 시도하고 못 잡으면 그 회차를 건너뛴다(이 마이그레이션은 잡지 않음).
--   "병합 대상이 이미 병합되지 않았어야 함"은 Detector가 이 잠금 안에서 확인한다(트리거 없음).
-- 커서: run_decision_events.id는 GENERATED ALWAYS identity이고 006·012 트리거가 pg_advisory_xact_lock(81736, 0) 아래에서 넣으므로 id 순서 = 커밋 순서.
BEGIN;

-- 1. 실행 기록. 마지막 커서 = (source_id, criteria_version)별 cursor_to 최대 행(인덱스).
CREATE TABLE run_incident_runs (
 id uuid PRIMARY KEY,
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 criteria_version text NOT NULL CHECK (criteria_version <> ''),
 cursor_from bigint NOT NULL CHECK (cursor_from >= 0),
 cursor_to bigint NOT NULL,
 added_members integer NOT NULL CHECK (added_members >= 0),
 opened_incidents integer NOT NULL CHECK (opened_incidents >= 0),
 merged_incidents integer NOT NULL CHECK (merged_incidents >= 0),
 started_at timestamptz NOT NULL,
 finished_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT run_incident_runs_cursor_check CHECK (cursor_to >= cursor_from),
 CONSTRAINT run_incident_runs_time_check CHECK (finished_at >= started_at)
);
CREATE INDEX run_incident_runs_cursor_idx ON run_incident_runs(source_id, criteria_version, cursor_to DESC);
CREATE TRIGGER incident_run_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON run_incident_runs
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

-- 2. 장애 묶음. criteria = 묶음 기준(시간 창·허브·채널·응답 코드 등) 객체, opened_at = 첫 구성원의 발생 시각(time_basis 기준).
--    UNIQUE(source_id, criteria_version, group_key, opened_at): 같은 묶음을 두 번 만들지 않게 한다(재실행 멱등).
CREATE TABLE run_incidents (
 id uuid PRIMARY KEY,
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 criteria jsonb NOT NULL CHECK (jsonb_typeof(criteria) = 'object'),
 criteria_version text NOT NULL CHECK (criteria_version <> ''),
 group_key text NOT NULL CHECK (group_key <> ''),
 time_basis text NOT NULL CHECK (time_basis IN ('process_time_raw','decided_at','source_log_occurred_at')),
 opened_at timestamptz NOT NULL,
 continues_from uuid REFERENCES run_incidents(id),
 added_by_run uuid NOT NULL REFERENCES run_incident_runs(id) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT run_incidents_identity_key UNIQUE (source_id, criteria_version, group_key, opened_at),
 CONSTRAINT run_incidents_continues_check CHECK (continues_from <> id)
);
-- WebUI 장애 목록·상세가 원천별 최근 묶음을 ORDER BY opened_at DESC로 읽는다(id DESC = 페이지 넘김용 안정 순서). 지금은 4열 UNIQUE뿐이라 추가.
CREATE INDEX run_incidents_recent_idx ON run_incidents(source_id, opened_at DESC, id DESC);
-- 앞 구간 하나를 잇는 다음 구간은 최대 1개(사슬이 갈라지지 않음).
CREATE UNIQUE INDEX run_incidents_continues_idx ON run_incidents(continues_from) WHERE continues_from IS NOT NULL;
CREATE TRIGGER incident_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON run_incidents
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

-- 3. 구성원. occurred_at = 묶음에 쓴 시각, occurred_basis = 그 시각의 출처(원문 시각 해석 실패 구성원은 decided_at으로 대체되므로 묶음 time_basis와 다를 수 있다).
CREATE TABLE run_incident_members (
 decision_id uuid PRIMARY KEY REFERENCES run_process_decisions(id),
 incident_id uuid NOT NULL REFERENCES run_incidents(id),
 occurred_at timestamptz NOT NULL,
 occurred_basis text NOT NULL CHECK (occurred_basis IN ('process_time_raw','decided_at','source_log_occurred_at')),
 added_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 added_by_run uuid NOT NULL REFERENCES run_incident_runs(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX run_incident_members_incident_idx ON run_incident_members(incident_id, occurred_at);
CREATE TRIGGER incident_member_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON run_incident_members
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

-- 4. 병합 이벤트. merged_into는 대상 필수·자기 자신 금지, 묶음당 1건.
CREATE TABLE run_incident_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 incident_id uuid NOT NULL REFERENCES run_incidents(id),
 kind text NOT NULL CHECK (kind IN ('merged_into')),
 target_incident_id uuid REFERENCES run_incidents(id),
 added_by_run uuid NOT NULL REFERENCES run_incident_runs(id) DEFERRABLE INITIALLY DEFERRED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT run_incident_events_target_check CHECK (kind <> 'merged_into' OR target_incident_id IS NOT NULL),
 CONSTRAINT run_incident_events_self_check CHECK (target_incident_id <> incident_id)
);
CREATE UNIQUE INDEX run_incident_events_merged_idx ON run_incident_events(incident_id) WHERE kind = 'merged_into';
CREATE TRIGGER incident_event_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON run_incident_events
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

COMMIT;
