-- 024 [제안 — DB 세션 확정 전, 번호는 dev 022·WebUI 023 다음]: 관계 규칙 재판정 탐색 주기 작업의 실행 기록·영속 커서(contracts/relation-rules.md 6.1 "커서 저장 위치", 10절).
-- 새 표 1개·트리거 1개만 만든다. 001~021 표·함수·트리거·enum·권한은 바꾸지 않는다. 새 함수 없음(004 erd_v03_immutable() 재사용).
-- 왜 새 표인가: ops_system_state.detector_state는 활성 구성 전환(applications.py)이 통째로 덮어쓰고 API가 그대로 내보내며,
--   ops_collection_checkpoints는 Collector 소유라 Detector 커서를 둘 자리가 아니다. 019 run_incident_runs와 같은 append-only 실행 기록으로 둔다.
-- 전제: 001~021 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 역할 스크립트(scripts/database-roles.sql) 재적용은 023·024·025를 모두 적용한 뒤 한 번(infra/database/README.md 024 절 배포 순서).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음(표가 비어 있으면 DROP TABLE로 되돌림).
--   잠금: 새 표의 FK 생성이 ops_sources에 짧은 SHARE ROW EXCLUSIVE를 잡는다.
-- 표 run_recheck_runs: Detector 재판정 탐색 1회(원본 1개) = 1행. 실행 트랜잭션 끝에 INSERT(커서가 움직였거나 작업을 넣었거나 첫 실행일 때만).
--   transaction_cursor_at·transaction_cursor_id = 출발점 (a) 거래 관측 커서 — 마지막으로 처리한 TRANSACTION 관측 (persisted_at, id). 첫 실행은 (지금 − 30초 − 24시간, 0 uuid).
--   event_cursor = 출발점 (b) 판정 이벤트 커서 — 마지막으로 읽은 run_decision_events.id. 첫 실행은 그때의 최대 id.
--   마지막 커서 = (source_id)별 seq 최대 행. seq는 잠금 81742 아래에서만 INSERT하므로 커밋 순서와 같다.
-- 불변: BEFORE UPDATE OR DELETE OR TRUNCATE FOR EACH STATEMENT 트리거(019와 같음) + 권한은 SELECT·INSERT만.
-- 직렬화: Detector가 탐색 트랜잭션에서 pg_try_advisory_xact_lock(81742, 0)을 시도하고 못 잡으면 그 회차를 건너뛴다(이 마이그레이션은 잡지 않음).
--   같은 트랜잭션에서 재판정 작업 생성이 pg_advisory_xact_lock(81740, …)을 잡는다(순서 81742 → 81740만). 81741과 같은 트랜잭션에서 잡지 않는다.
-- 1회 보장은 이 표가 아니라 run_input_manifests UNIQUE(source_id, content_hash)(004)다. 이 표가 없어져도 재판정이 두 번 생기지 않는다(커서가 처음부터 다시 읽을 뿐).
BEGIN;

CREATE TABLE run_recheck_runs (
 id uuid PRIMARY KEY,
 seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 source_id uuid NOT NULL REFERENCES ops_sources(id),
 transaction_cursor_at timestamptz NOT NULL,
 transaction_cursor_id uuid NOT NULL,
 event_cursor bigint NOT NULL CHECK (event_cursor >= 0),
 transactions integer NOT NULL CHECK (transactions >= 0),
 candidates integer NOT NULL CHECK (candidates >= 0),
 enqueued integer NOT NULL CHECK (enqueued >= 0),
 started_at timestamptz NOT NULL,
 finished_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT run_recheck_runs_time_check CHECK (finished_at >= started_at)
);
CREATE INDEX run_recheck_runs_cursor_idx ON run_recheck_runs(source_id, seq DESC);
CREATE TRIGGER recheck_run_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON run_recheck_runs
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

COMMIT;
