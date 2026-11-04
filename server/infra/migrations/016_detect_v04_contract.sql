-- 016: 새 판정 경로 v04(작업·판정 구성 조건), 룰 세트 hash 버전 열과 시스템 행 r1 (#119, 계약 server/contracts/detection-decision.md 4.3·10·12·13절·15절 K16·K17, rule-set.md 리비전 형식·저장소 절).
-- 판정 1건은 구성 버전 1개에 묶이지 않고(활성 모델 0..N) 모델 없이 룰만으로도 판정한다. 그래서 v04 detect 작업은 구성 없이 만들고, 그 판정은 구성이 NULL이다.
--   리비전(룰 세트·활성 집합·정책)은 작업 입력·판정 열에 넣지 않는다(시도 시작마다 읽어 evidence v4에만 기록). manifest는 v03 형식 그대로(004 CHECK 변경 없음).
--   v03·legacy 작업과 행의 규칙은 그대로다. 이 파일을 적용해도 v03 쓰기 코드는 수정 없이 동작하고, v04 행은 Detector #112가 쓰기 전까지 생기지 않는다.
-- 전제: 001~015 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표·함수 소유자로 실행(012 전제와 같음, CREATE OR REPLACE는 소유자만 가능).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 새 표 없음 → database-roles.sql 변경 없음(열 단위 GRANT 대상 표가 아님). 자동 down migration 없음.
--   되돌림 주의: v04 행이 생긴 뒤에는 NOT NULL 복원이 불가능하고 r1은 불변 행이므로 001~015 상태로 내릴 수 없다.
--   실패 조건: 룰 세트에 r0 말고 저장된 리비전이 있으면 r1을 넣지 않고 RAISE EXCEPTION으로 전체를 되돌린다. 이 검사를 맨 앞에 두어 큰 표를 잠그고 읽기 전에 멈춘다.
-- 잠금(COMMIT까지 유지): run_jobs·run_process_decisions·run_llm_results·ops_pipeline_measurements·ops_rule_set_revisions와 뷰 ops_operational_state(CREATE OR REPLACE VIEW)에 ACCESS EXCLUSIVE, 읽기·쓰기 모두 대기.
--   표 재작성 없음: DROP NOT NULL은 카탈로그만, ADD COLUMN 상수 DEFAULT는 fast default(attmissingval)로 카탈로그만 바꾼다.
--   heap 전체 읽기: CHECK 검증과 인덱스 생성이 표마다 일어난다(run_jobs CHECK 1회 + 부분 인덱스 3개, run_process_decisions CHECK 1회 + 부분 인덱스 1개,
--   run_llm_results CHECK 1회, ops_pipeline_measurements CHECK 1회). 부분 인덱스도 빌드 때 heap을 전부 읽는다(결과 인덱스는 비어 있음). evidence·input_spec 같은 TOAST 값은 읽지 않는다.
--   적용 절차: 012와 같이 Detector·Collector·Trainer·LLM·API 작업자를 멈추고 적용한다. 작업자 트랜잭션은 표를 다른 순서로 잠그므로(판정 → 측정 → 작업 등) 켠 채 적용하면 교착으로 어느 한쪽이 취소될 수 있다.
-- NOT VALID를 쓰지 않는 근거: 잠금 시간을 줄이려면 검증을 별도 트랜잭션으로 빼야 하는데, 같은 트랜잭션의 CREATE INDEX가 어차피 run_jobs·run_process_decisions heap을 ACCESS EXCLUSIVE 아래에서 읽는다.
--   CREATE INDEX CONCURRENTLY는 트랜잭션 안에서 쓸 수 없고 v04 중복 방지 인덱스가 없는 구간을 만든다. 그러면 원자성만 잃고 이득이 없으므로 010·011과 같이 트랜잭션 안에서 바로 검증한다.
--   기존 행은 이전 CHECK·NOT NULL을 이미 만족하고 새 조건은 그보다 넓으므로(v03 explain 구성 조건 완화, v04 분기 추가) 검증이 실패할 수 없다.
-- 재정의 함수 3개: 원본 최신판(erd_v03_validate_job = 011, erd_v03_validate_result = 004 + 013 search_path, validate_decision_interim = 012)을 기준으로 아래 표시한 줄만 바꾼다.
--   CREATE OR REPLACE는 함수 설정을 비우므로 SET search_path를 그대로 다시 적는다. 소유자·SECURITY INVOKER 유지. 012 SECURITY DEFINER 함수 2개는 버전 조건이 없어 재정의하지 않는다.
--   012 본문의 주석은 012에 남긴다(코드 줄만 옮김).
BEGIN;
-- r1의 한글·기호 리터럴 보호(015와 같음): 세션 client_encoding이 UTF8이 아니어도 이후 문장을 UTF-8로 해석한다. COMMIT·ROLLBACK에서 원래 값으로 돌아간다.
SET LOCAL client_encoding = 'UTF8';

-- 1. 룰 세트 hash 버전(rule-set.md 리비전 형식·저장소 절). 기존 행 1, 이후 저장 2.
--    ADD COLUMN의 DEFAULT 1은 기존 행의 값(attmissingval)으로 고정되고, 이어서 바꾼 DEFAULT 2는 새 행에만 적용된다. 행을 UPDATE하지 않으므로 불변 트리거는 그대로 두고 건드릴 필요가 없다.
--    DEFAULT 2의 근거: 계약상 016 이후 저장은 전부 v2이고 API가 열을 빠뜨려도 v1로 잘못 표시되지 않게 한다(dev에 이 표를 쓰는 코드가 없어 v1 hash에 2가 붙을 일이 없고, 저장 구현은 열을 명시한다).
ALTER TABLE ops_rule_set_revisions ADD COLUMN content_hash_version smallint NOT NULL DEFAULT 1 CHECK (content_hash_version IN (1,2));
ALTER TABLE ops_rule_set_revisions ALTER COLUMN content_hash_version SET DEFAULT 2;

-- 2. 시스템 행 r1: 내장 하드룰 4개 전부 켬·내장 기본 severity·소프트룰 없음(rule-set.md `revision` 행, 항목 값은 detection-decision.md 4.3).
--    최대 리비전이 0일 때만 넣는다. 그 밖이면 저장된 룰 위에 시스템 행을 얹게 되므로 전체를 되돌린다(이 절 바로 위 ALTER가 표 ACCESS EXCLUSIVE를 쥐고 있어 API 저장과 겹치지 않는다).
--    pg_advisory_xact_lock(81739, 0)은 잡지 않는다. 표 잠금을 쥔 채 기다리면 그 잠금을 쥐고 표를 읽으려는 API 저장과 교착하고, 표 잠금만으로 직렬화가 끝난다.
DO $$
DECLARE latest bigint;
BEGIN
 SELECT max(revision) INTO latest FROM ops_rule_set_revisions;
 IF latest IS DISTINCT FROM 0 THEN
  RAISE EXCEPTION '016 aborted, nothing applied: system row r1 requires ops_rule_set_revisions max revision 0 but found %. Reason: a saved revision exists, and r1 would replace those rules. Action: keep the saved rows, decide with the rule-set owner how hard:success-with-error and hard:count-conservation get enabled, then revise the r1 step of 016 before applying it again.', COALESCE(latest::text, 'no rows');
 END IF;
END $$;
--    content_hash = rule-set.md v2 정규 객체(하드룰 {id, enabled, severity}, id 사전순)
--    {"hardRules":[{"enabled":true,"id":"hard:count-conservation","severity":"critical"},{"enabled":true,"id":"hard:negative-duration","severity":"critical"},
--    {"enabled":true,"id":"hard:process-status","severity":{"byCode":{"PROCESS_STATUS_F":"critical","PROCESS_STATUS_H":"info","PROCESS_STATUS_N":"info","PROCESS_STATUS_P":"warning","PROCESS_STATUS_Z":"warning"}}},
--    {"enabled":true,"id":"hard:success-with-error","severity":"critical"}],"softRules":[]}의 UTF-8 바이트 SHA-256(공백 없음). 재계산 대조는 tests/erd-v03-detect-v04.mjs.
--    changes: r0 스냅샷에 없는 규칙은 꺼진 것이므로 새 규칙 2개가 hard_enabled. hard:process-status는 r0가 v1 항목이라 값을 내장 기본값으로 읽어(rule-set.md v1 항목 severity 공통 규칙) severity 변경이 아니다.
--    note는 r0와 같이 NULL(이력 화면 문구를 새로 만들지 않는다).
INSERT INTO ops_rule_set_revisions (revision, content_hash, content_hash_version, snapshot, changes, summary, saved_by_actor_id) VALUES (1,
 'de6a2ad8d0dcee189f36852a279263d3408f826632b95b4f14a97406b801bed0', 2,
 '{"hardRules": [
   {"id": "hard:process-status", "methodVersion": "process-status-hr-v2", "displayName": "상태가 S가 아님", "conditionText": "STATUS ≠ S",
    "severity": {"byCode": {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning", "PROCESS_STATUS_Z": "warning", "PROCESS_STATUS_H": "info", "PROCESS_STATUS_N": "info"}},
    "enabled": true, "editable": ["enabled", "severity"]},
   {"id": "hard:negative-duration", "methodVersion": "negative-duration-v1", "displayName": "시작 시각이 종료 시각보다 늦음",
    "conditionText": "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)",
    "severity": "critical", "enabled": true, "editable": ["enabled"]},
   {"id": "hard:success-with-error", "methodVersion": "success-with-error-v1", "displayName": "성공 상태이나 오류 건수가 기록됨",
    "conditionText": "STATUS = S, ERROR_COUNT > 0", "severity": "critical", "enabled": true, "editable": ["enabled"]},
   {"id": "hard:count-conservation", "methodVersion": "count-conservation-v1", "displayName": "건수 합계 불일치",
    "conditionText": "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT", "severity": "critical", "enabled": true, "editable": ["enabled"]}
  ], "softRules": []}',
 '[{"type": "hard_enabled", "id": "hard:success-with-error", "displayName": "성공 상태이나 오류 건수가 기록됨"},
   {"type": "hard_enabled", "id": "hard:count-conservation", "displayName": "건수 합계 불일치"}]',
 '{"hardEnabled": 2, "hardDisabled": 0, "hardUpdated": 0, "softAdded": 0, "softUpdated": 0, "softRemoved": 0, "softEnabled": 0, "softDisabled": 0}',
 'system');

-- 3. 일관 읽기 뷰. model-version.md 일관 읽기 요구사항은 룰 세트 revision·contentHash·contentHashVersion을 한 응답으로 읽는다.
--    Detector 합의 열 6개의 이름·순서·형식은 그대로 두고 맨 뒤에 1개를 덧붙인다(CREATE OR REPLACE VIEW가 허용하는 유일한 변경).
--    CREATE OR REPLACE VIEW는 옵션을 다시 쓰므로 security_invoker를 함께 적는다. 뷰 권한은 유지된다.
CREATE OR REPLACE VIEW ops_operational_state WITH (security_invoker = true) AS
SELECT r.revision AS rule_set_revision, r.content_hash AS rule_set_content_hash,
 a.revision AS active_model_set_revision, a.snapshot->'models' AS active_models,
 COALESCE(p.revision, 0) AS detection_policy_revision, p.policy AS detection_policy,
 r.content_hash_version AS rule_set_content_hash_version
FROM (SELECT revision, content_hash, content_hash_version FROM ops_rule_set_revisions ORDER BY revision DESC LIMIT 1) r
CROSS JOIN (SELECT revision, snapshot FROM ops_active_model_set_revisions ORDER BY revision DESC LIMIT 1) a
LEFT JOIN (SELECT revision, policy FROM ops_detection_policy_revisions ORDER BY revision DESC LIMIT 1) p ON true;

-- 4. run_jobs. v04 = detect 전용, 구성 NULL 필수. v03 분기는 explain의 구성 필수 조건만 뺀다(일치는 아래 erd_v03_validate_job이 판정과 비교해 강제).
ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain')
  AND kind::text NOT IN ('import_csv','evaluation_set','compare','recommend_methods')
  AND requested_by_actor_id IS NOT NULL AND requester_role IS NOT NULL
  AND queue IS NULL AND producer_kind IS NULL AND producer_key IS NULL
  AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
 OR (contract_version IS NOT NULL AND contract_version = 'v03' AND queue IS NOT NULL
  AND producer_kind IS NOT NULL AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND ((kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
    AND requested_by_actor_id IS NULL AND requester_role IS NULL
    AND input_manifest_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('preview','train','evaluate') OR kind::text IN ('import_csv','evaluation_set','compare')) AND queue = 'training' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('recommend_rule','recommend_feature') OR kind::text = 'recommend_methods') AND queue = 'llm_recommendation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind = 'explain' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role IN ('user','admin')
    AND input_decision_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)))
 OR (contract_version IS NOT NULL AND contract_version = 'v04' AND kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
  AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND requested_by_actor_id IS NULL AND requester_role IS NULL
  AND input_manifest_id IS NOT NULL AND input_config_version_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
) IS TRUE);
-- 004의 v03 부분 인덱스 3개와 같은 역할. v03 인덱스는 건드리지 않는다. 구성 열이 없으므로 중복 방지 키는 (kind, input_manifest_id).
CREATE UNIQUE INDEX run_jobs_v04_producer_key_idx ON run_jobs(producer_kind,producer_key,kind,idempotency_key) WHERE contract_version = 'v04';
CREATE UNIQUE INDEX run_jobs_v04_detect_idx ON run_jobs(kind,input_manifest_id) WHERE contract_version = 'v04' AND kind = 'detect';
CREATE INDEX run_jobs_v04_queue_idx ON run_jobs(queue,status,next_run_at,created_at,id) WHERE contract_version = 'v04';

-- 5. run_process_decisions. 구성 NULL은 v04 작업의 판정만 가능하다. 세 겹으로 막는다.
--    (a) 작업 없는 legacy 행: CHECK (b) 작업 있는 INSERT: erd_v03_validate_result가 판정 구성 = 작업 구성을 비교(v03 작업은 구성 필수, v04 작업은 구성 NULL 필수)
--    (c) legacy 행의 UPDATE: 아래 트리거. 검증 트리거는 INSERT에만, 불변 트리거는 작업 있는 행에만 붙어 있어, 이것이 없으면 legacy 행을 구성만 지우거나
--        작업에 붙여(v04면 '구성 있는 v04 판정', v03이면 소유권 검사를 건너뛴 판정) 검증을 우회할 수 있다. legacy 행의 published_at 등 다른 열 UPDATE는 그대로 허용한다.
--        코드에 이 표를 UPDATE하는 곳은 없다(016 작성 시 worker·backend 확인). 작업 있는 행은 decision_v03_immutable이 막는다.
ALTER TABLE run_process_decisions ALTER COLUMN config_version_id DROP NOT NULL;
ALTER TABLE run_process_decisions ADD CONSTRAINT decisions_config_required CHECK (config_version_id IS NOT NULL OR job_id IS NOT NULL);
CREATE TRIGGER decision_config_kept BEFORE UPDATE ON run_process_decisions
 FOR EACH ROW WHEN (OLD.job_id IS NULL AND (NEW.job_id IS NOT NULL OR NEW.config_version_id IS NULL)) EXECUTE FUNCTION erd_v03_immutable();
-- UNIQUE(execution_id,process_observation_id,config_version_id,input_manifest_hash)는 NULL끼리 다르게 보아 구성 NULL 행을 막지 못한다.
-- job_id UNIQUE와 run_jobs_v04_detect_idx만으로도 작업이 manifest당 1개라 판정이 1행으로 묶이지만, 판정 표 자체가 같은 입력의 중복을 거부하도록 메운다.
CREATE UNIQUE INDEX run_process_decisions_no_config_idx ON run_process_decisions(execution_id,process_observation_id,input_manifest_hash) WHERE config_version_id IS NULL;

-- 6. run_llm_results. explain 결과의 구성 필수 조건만 뺀다. 결과 구성 = 작업 구성(erd_v03_validate_result), 작업 구성 = 판정 구성(erd_v03_validate_job)으로 일치는 그대로 강제된다.
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_check
 CHECK((kind::text = 'explain' AND decision_id IS NOT NULL)
  OR (kind::text IN ('recommend_rule','recommend_feature','recommend_methods') AND decision_id IS NULL));

-- 7. ops_pipeline_measurements. Detector가 판정 트랜잭션에서 stage='detector' 행을 쓰고 API 운영 상태가 이 행으로 Detector 지연·마지막 시각을 보여 주므로 v04에서도 계속 써야 한다.
--    구성 없는 판정의 측정이므로 detector 단계만 NULL을 허용한다. feature 단계는 NOT NULL 효과 유지. 측정 행에 작업 참조가 없어 'v04 작업의 측정'까지는 좁힐 수 없다.
ALTER TABLE ops_pipeline_measurements ALTER COLUMN config_version_id DROP NOT NULL;
ALTER TABLE ops_pipeline_measurements ADD CONSTRAINT ops_pipeline_measurements_config_check CHECK (config_version_id IS NOT NULL OR stage = 'detector');

-- 8. 작업 검증(011 기준). 바뀐 줄: 불변 검사를 버전이 있는 모든 작업으로, 조기 반환을 legacy(NULL)만으로, detect 구성·원천 대조를 v03으로 한정.
CREATE OR REPLACE FUNCTION erd_v03_validate_job() RETURNS trigger LANGUAGE plpgsql
 SET search_path = public, pg_temp AS $$
DECLARE d run_process_decisions; s text; a run_job_attempts;
BEGIN
 IF TG_OP = 'UPDATE' AND OLD.contract_version IS DISTINCT FROM NEW.contract_version THEN RAISE EXCEPTION 'job contract version is fixed; create a new job'; END IF;
 IF TG_OP = 'UPDATE' AND OLD.contract_version IS NOT NULL AND
  ROW(NEW.kind,NEW.contract_version,NEW.queue,NEW.producer_kind,NEW.producer_key,NEW.requested_by_actor_id,NEW.requester_role,NEW.idempotency_key,NEW.request_hash,NEW.input_config_version_id,NEW.input_snapshot_id,NEW.input_manifest_id,NEW.input_decision_id,NEW.input_spec,NEW.llm_spec)
  IS DISTINCT FROM ROW(OLD.kind,OLD.contract_version,OLD.queue,OLD.producer_kind,OLD.producer_key,OLD.requested_by_actor_id,OLD.requester_role,OLD.idempotency_key,OLD.request_hash,OLD.input_config_version_id,OLD.input_snapshot_id,OLD.input_manifest_id,OLD.input_decision_id,OLD.input_spec,OLD.llm_spec)
 THEN RAISE EXCEPTION '% job input is immutable', OLD.contract_version; END IF;
 IF NEW.contract_version IS NULL THEN RETURN NEW; END IF;
 IF NEW.producer_kind = 'user' AND NEW.producer_key IS DISTINCT FROM NEW.requested_by_actor_id THEN RAISE EXCEPTION 'user producer mismatch'; END IF;
 IF NEW.status = 'blocked' AND NEW.blocked_reason_code IS NULL THEN RAISE EXCEPTION 'blocked reason required'; END IF;
 IF NEW.kind = 'detect' AND NEW.contract_version = 'v03' AND
  (SELECT source_id FROM run_input_manifests WHERE id = NEW.input_manifest_id) IS DISTINCT FROM
  (SELECT source_id FROM ml_config_versions WHERE id = NEW.input_config_version_id) THEN RAISE EXCEPTION 'detect config/input source mismatch'; END IF;
 IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods') THEN
  IF jsonb_typeof(NEW.llm_spec) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'LLM spec object required'; END IF;
  FOREACH s IN ARRAY ARRAY['evidence_version','input_evidence_hash','model_version','prompt_version','output_schema_version','reuse_key'] LOOP
   IF jsonb_typeof(NEW.llm_spec->s) IS DISTINCT FROM 'string' OR COALESCE(NEW.llm_spec->>s,'') = '' THEN RAISE EXCEPTION 'LLM spec string required: %',s; END IF;
  END LOOP;
 END IF;
 IF NEW.kind = 'explain' THEN
  SELECT * INTO STRICT d FROM run_process_decisions WHERE id = NEW.input_decision_id;
  IF d.config_version_id IS DISTINCT FROM NEW.input_config_version_id THEN RAISE EXCEPTION 'explanation decision/config mismatch'; END IF;
 END IF;
 IF NEW.status = 'succeeded' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'succeeded') THEN
  SELECT * INTO STRICT a FROM run_job_attempts WHERE id = NEW.current_attempt_id AND job_id = NEW.id;
  IF a.status NOT IN ('running','succeeded') OR a.lease_until <= clock_timestamp() THEN RAISE EXCEPTION 'completion requires current unexpired attempt'; END IF;
  IF NEW.kind = 'detect' AND NOT EXISTS(SELECT 1 FROM run_process_decisions WHERE job_id = NEW.id AND result_attempt_id = a.id) THEN RAISE EXCEPTION 'detect completion requires decision'; END IF;
  -- Nested IF: kind::text is not constant-folded, so the run_llm_results check (and its SELECT
  -- privilege) must stay out of non-LLM completions such as the demo role's train jobs.
  IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods') THEN
   IF NOT EXISTS(SELECT 1 FROM run_llm_results WHERE job_id = NEW.id AND created_attempt_id = a.id) THEN RAISE EXCEPTION 'LLM completion requires result'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

-- 9. 판정·LLM 결과 검증(004 + 013 search_path 기준). 바뀐 줄: 소유권 검사의 버전 조건 하나.
--    구성 일치 비교는 그대로 둔다. v04 작업의 구성은 NULL이므로 IS DISTINCT FROM이 곧 '판정 구성 NULL 필수'이고, v03은 지금처럼 같은 값이어야 한다.
CREATE OR REPLACE FUNCTION erd_v03_validate_result() RETURNS trigger LANGUAGE plpgsql
 SET search_path = public, pg_temp AS $$
DECLARE j run_jobs; a run_job_attempts; attempt_id uuid; token uuid; spec jsonb; manifest run_input_manifests;
BEGIN
 IF TG_TABLE_NAME = 'run_process_decisions' AND NEW.job_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO STRICT j FROM run_jobs WHERE id = NEW.job_id FOR UPDATE;
 IF TG_TABLE_NAME = 'run_process_decisions' THEN attempt_id := NEW.result_attempt_id; token := NEW.result_lease_token;
 ELSE attempt_id := NEW.created_attempt_id; token := NEW.lease_token; END IF;
 SELECT * INTO STRICT a FROM run_job_attempts WHERE id = attempt_id AND job_id = j.id FOR UPDATE;
 IF j.contract_version IS NULL OR j.contract_version NOT IN ('v03','v04') OR j.status <> 'running' OR j.current_attempt_id IS DISTINCT FROM a.id
  OR a.status <> 'running' OR a.lease_token IS DISTINCT FROM token OR a.lease_until <= clock_timestamp()
 THEN RAISE EXCEPTION 'result requires current owner and unexpired lease'; END IF;
 IF TG_TABLE_NAME = 'run_process_decisions' THEN
  IF j.kind <> 'detect' OR NEW.input_manifest_id IS DISTINCT FROM j.input_manifest_id OR NEW.config_version_id IS DISTINCT FROM j.input_config_version_id
  THEN RAISE EXCEPTION 'decision job kind/input/config mismatch'; END IF;
  SELECT * INTO STRICT manifest FROM run_input_manifests WHERE id = j.input_manifest_id;
  IF NEW.transaction_observation_id IS DISTINCT FROM manifest.transaction_observation_id OR NEW.input_as_of IS DISTINCT FROM manifest.input_as_of
  THEN RAISE EXCEPTION 'decision transaction/as-of differs from fixed manifest'; END IF;
 ELSE
  spec := j.llm_spec;
  IF NEW.kind IS DISTINCT FROM j.kind OR NEW.decision_id IS DISTINCT FROM j.input_decision_id OR NEW.config_version_id IS DISTINCT FROM j.input_config_version_id
   OR NEW.evidence_version IS DISTINCT FROM spec->>'evidence_version' OR NEW.input_evidence_hash IS DISTINCT FROM spec->>'input_evidence_hash'
   OR NEW.model_version IS DISTINCT FROM spec->>'model_version' OR NEW.prompt_version IS DISTINCT FROM spec->>'prompt_version'
   OR NEW.output_schema_version IS DISTINCT FROM spec->>'output_schema_version' OR NEW.reuse_key IS DISTINCT FROM spec->>'reuse_key'
  THEN RAISE EXCEPTION 'LLM result/job lineage mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- 10. 중간 판정 기록 검증(012 기준). 바뀐 줄: 작업 버전 조건 하나. 구성을 읽지 않으므로 구성 NULL과 무관하다.
--    012 방식 유지: SET search_path = pg_catalog, pg_temp, 표·행 타입은 public., 카탈로그는 pg_catalog. 한정. 대체됨·최종 없음 트리거 함수(SECURITY DEFINER)는 변경 없음.
CREATE OR REPLACE FUNCTION validate_decision_interim() RETURNS trigger LANGUAGE plpgsql
 SET search_path = pg_catalog, pg_temp AS $$
DECLARE j public.run_jobs; a public.run_job_attempts; nb public.run_job_attempts;
BEGIN
 SELECT * INTO STRICT j FROM public.run_jobs WHERE id = NEW.job_id FOR UPDATE;
 IF j.contract_version IS NULL OR j.contract_version NOT IN ('v03','v04') OR j.kind <> 'detect' THEN RAISE EXCEPTION 'interim requires v03 or v04 detect job'; END IF;
 SELECT * INTO STRICT a FROM public.run_job_attempts WHERE id = NEW.attempt_id AND job_id = j.id FOR UPDATE;
 IF EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id AND kind IN ('superseded','no_final'))
 THEN RAISE EXCEPTION 'interim chain of attempt is closed'; END IF;
 IF NEW.kind IN ('first_anomaly','evidence_added') THEN
  IF j.status <> 'running' OR j.current_attempt_id IS DISTINCT FROM a.id OR a.status <> 'running'
     OR a.lease_token IS DISTINCT FROM NEW.lease_token OR a.lease_until <= clock_timestamp()
  THEN RAISE EXCEPTION 'interim requires current owner and unexpired lease'; END IF;
  IF EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = j.id)
  THEN RAISE EXCEPTION 'interim after final decision'; END IF;
  IF NEW.kind = 'first_anomaly' AND EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id)
  THEN RAISE EXCEPTION 'first_anomaly must open the attempt chain'; END IF;
  IF NEW.kind = 'evidence_added' AND NOT EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id AND kind = 'first_anomaly')
  THEN RAISE EXCEPTION 'evidence_added requires first_anomaly of the same attempt'; END IF;
 ELSIF NEW.kind IN ('superseded','no_final') THEN
  IF pg_trigger_depth() < 2 THEN RAISE EXCEPTION 'interim % is written only by database triggers', NEW.kind; END IF;
  IF current_user IS DISTINCT FROM (SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c WHERE c.oid = 'public.run_decision_interims'::pg_catalog.regclass)
  THEN RAISE EXCEPTION 'interim % is written only by owner-defined triggers', NEW.kind; END IF;
  NEW.id := gen_random_uuid();
  NEW.payload := '{}'::jsonb;
  IF NOT EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id)
  THEN RAISE EXCEPTION 'closing record requires an open interim chain'; END IF;
  IF NEW.kind = 'superseded' THEN
   SELECT * INTO STRICT nb FROM public.run_job_attempts WHERE id = NEW.superseded_by_attempt_id AND job_id = j.id;
   IF nb.attempt_no <= a.attempt_no OR j.current_attempt_id IS DISTINCT FROM nb.id OR NOT (
      EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = nb.id AND kind = 'first_anomaly')
      OR EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = j.id AND result_attempt_id = nb.id))
   THEN RAISE EXCEPTION 'superseded requires first_anomaly or decision of a newer current attempt'; END IF;
  ELSE
   IF j.status <> 'failed' OR EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = j.id)
   THEN RAISE EXCEPTION 'no_final requires failed detect job without decision'; END IF;
   NEW.reason_code := COALESCE(NULLIF(j.error_code, ''), 'job_failed');
  END IF;
 END IF;
 NEW.seq := COALESCE((SELECT max(seq) FROM public.run_decision_interims WHERE attempt_id = a.id), 0) + 1;
 NEW.created_at := clock_timestamp();
 RETURN NEW;
END $$;

COMMIT;
