-- 015: 운영 리비전 표 3개와 일관 읽기 뷰 (#110, 계약 server/contracts/rule-set.md·model-version.md).
-- 새 표 3개·뷰 1개·트리거 3개만 만든다. 001~014 표·함수·트리거·enum은 바꾸지 않는다. 새 함수 없음.
-- 전제: 001~014 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용.
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 기존 표 잠금 없음(FK는 새 표 자기 참조뿐). 자동 down migration 없음.
-- append-only: 트리거 1개가 UPDATE·DELETE·TRUNCATE를 문장 단위로 거부한다(0행 문장 포함). 함수는 004 erd_v03_immutable() 재사용 —
--   NEW·OLD를 읽지 않고 RAISE만 하므로 문장 수준·TRUNCATE에서도 그대로 동작하고 표를 참조하지 않아 search_path 경로가 없다(013 제외 사유와 같음).
-- 현재 값 = 표별 최대 revision 행. 세 표의 저장은 API가 공통 잠금 pg_advisory_xact_lock(81739, 0)을 잡고 직렬화한다
--   (이 마이그레이션은 잡지 않음. API는 UPDATE 권한이 없어 SELECT … FOR UPDATE를 쓸 수 없다).
BEGIN;
-- r0의 한글 리터럴 보호: psql -f는 문장별로 보내므로 세션 client_encoding이 UTF8이 아니어도 이후 문장을 UTF-8로 해석한다. COMMIT·ROLLBACK에서 원래 값으로 돌아간다.
SET LOCAL client_encoding = 'UTF8';

-- 1. 룰 세트 리비전(rule-set.md 저장소 요구사항). snapshot = GET /api/rule-set의 hardRules[]·softRules[] 전체.
CREATE TABLE ops_rule_set_revisions (
 revision bigint PRIMARY KEY CHECK (revision >= 0),
 content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
 snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot->'hardRules') IS NOT DISTINCT FROM 'array'
  AND jsonb_typeof(snapshot->'softRules') IS NOT DISTINCT FROM 'array'),
 changes jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'array'),
 summary jsonb NOT NULL CHECK (jsonb_typeof(summary) = 'object'),
 note text,
 restored_from_revision bigint REFERENCES ops_rule_set_revisions(revision),
 saved_by_actor_id text NOT NULL CHECK (saved_by_actor_id <> ''),
 saved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (restored_from_revision < revision)
);
CREATE TRIGGER rule_set_revision_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ops_rule_set_revisions
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

-- 2. 활성 모델 집합 리비전(model-version.md 불변성·저장소). snapshot = {"models": [...]}.
CREATE TABLE ops_active_model_set_revisions (
 revision bigint PRIMARY KEY CHECK (revision >= 0),
 snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot->'models') IS NOT DISTINCT FROM 'array'),
 changes jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'array'),
 note text,
 restored_from_revision bigint REFERENCES ops_active_model_set_revisions(revision),
 changed_by_actor_id text NOT NULL CHECK (changed_by_actor_id <> ''),
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (restored_from_revision < revision)
);
CREATE TRIGGER active_model_set_revision_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ops_active_model_set_revisions
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

-- 3. 판정 정책 리비전(model-version.md 운영 설정). 행 없음 = 리비전 0(내장 기본 정책), 첫 저장이 리비전 1.
CREATE TABLE ops_detection_policy_revisions (
 revision bigint PRIMARY KEY CHECK (revision >= 1),
 policy jsonb NOT NULL CHECK (jsonb_typeof(policy) = 'object'),
 note text,
 restored_from_revision bigint REFERENCES ops_detection_policy_revisions(revision),
 saved_by_actor_id text NOT NULL CHECK (saved_by_actor_id <> ''),
 saved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (restored_from_revision < revision)
);
CREATE TRIGGER detection_policy_revision_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON ops_detection_policy_revisions
 FOR EACH STATEMENT EXECUTE FUNCTION erd_v03_immutable();

-- 4. r0 시드. 룰 세트: 하드룰 2개 켬·소프트룰 없음(항목은 detection-decision.md 4.3 값 + editable).
--    content_hash = rule-set.md 리비전 형식 절의 정규 객체(contentHashVersion 1)
--    {"hardRules":[{"enabled":true,"id":"hard:negative-duration"},{"enabled":true,"id":"hard:process-status"}],"softRules":[]}의
--    UTF-8 바이트 SHA-256. 재계산 대조는 tests/erd-v03-revisions.mjs.
INSERT INTO ops_rule_set_revisions (revision, content_hash, snapshot, changes, summary, saved_by_actor_id) VALUES (0,
 '80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89',
 '{"hardRules": [
   {"id": "hard:process-status", "methodVersion": "process-status-hr-v1", "displayName": "상태가 S가 아님", "conditionText": "STATUS ≠ S",
    "severity": {"byCode": {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning", "PROCESS_STATUS_N": "warning"}},
    "enabled": true, "editable": ["enabled"]},
   {"id": "hard:negative-duration", "methodVersion": "negative-duration-v1", "displayName": "시작 시각이 종료 시각보다 늦음",
    "conditionText": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)",
    "severity": "critical", "enabled": true, "editable": ["enabled"]}
  ], "softRules": []}',
 '[]',
 '{"hardEnabled": 0, "hardDisabled": 0, "softAdded": 0, "softUpdated": 0, "softRemoved": 0, "softEnabled": 0, "softDisabled": 0}',
 'system');
-- 활성 집합: r0 = 빈 집합 행. 현재 값 = 최대 revision 행이므로 행이 있어야 조회·복원(restored_from_revision = 0)이 룰 세트와 같은 경로로 간다.
INSERT INTO ops_active_model_set_revisions (revision, snapshot, changes, changed_by_actor_id)
 VALUES (0, '{"models": []}', '[]', 'system');

-- 5. 일관 읽기(model-version.md 일관 읽기 요구사항): 세 리비전을 한 문장 = 한 스냅샷으로 읽는 1행 뷰. READ COMMITTED에서도 서로 어긋나지 않는다.
--    정책 행이 없으면 revision 0·detection_policy NULL(값은 내장 기본 정책을 쓴다). 룰 세트·활성 집합은 r0가 있으므로 빈 결과 = 저장소 이상(fail-closed).
--    security_invoker: 호출자 권한으로 표를 읽으므로 뷰 권한이 표 권한을 넓히지 않는다. 표 이름은 생성 시 OID로 고정돼 호출자 search_path·임시 테이블의 영향이 없다.
CREATE VIEW ops_operational_state WITH (security_invoker = true) AS
SELECT r.revision AS rule_set_revision, r.content_hash AS rule_set_content_hash,
 a.revision AS active_model_set_revision, a.snapshot->'models' AS active_models,
 COALESCE(p.revision, 0) AS detection_policy_revision, p.policy AS detection_policy
FROM (SELECT revision, content_hash FROM ops_rule_set_revisions ORDER BY revision DESC LIMIT 1) r
CROSS JOIN (SELECT revision, snapshot FROM ops_active_model_set_revisions ORDER BY revision DESC LIMIT 1) a
LEFT JOIN (SELECT revision, policy FROM ops_detection_policy_revisions ORDER BY revision DESC LIMIT 1) p ON true;

COMMIT;
