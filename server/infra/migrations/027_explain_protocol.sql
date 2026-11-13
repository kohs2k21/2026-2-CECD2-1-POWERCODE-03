-- 027: 프로토콜 경로 설명 작업 종류 explain_protocol (계약 server/contracts/protocol-explanation-job.md DB 영향 절). [초안 — 공통 계약, 사용자 승인 대상]
-- 왜: 프로토콜 설정 상세(관측 경로)·프로토콜 알림 상세에서 관리자가 요청하는 대화형 LLM 설명. API가 llm_explanation 큐로 접수하고 LLM이 결과를 run_llm_results에 쓴다(explain과 같은 등급).
--   run_jobs v03에 explain_protocol 분기(llm_explanation 큐·user producer·admin·llm_spec 필수·구성/manifest NULL, 판정은 알림 대상만)를 더하고, legacy(contract_version NULL) 금지 목록에도 넣어 v03 전용으로 만든다(017·021 방식).
--   run_llm_results kind CHECK와 decision 분기, erd_v03_validate_job의 llm_spec 필수·LLM 완료 결과 필수 목록에 값을 더한다(017 방식).
-- 전제: 001~026 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표·함수 소유자로 실행(CREATE OR REPLACE는 소유자만 가능). 자동 down migration 없음(enum 값은 지울 수 없다).
-- 기준: run_jobs_v03_contract는 021 재정의본, run_llm_results_kind_check·run_llm_results_check·erd_v03_validate_job은 017 재정의본(022~026은 이 넷을 건드리지 않는다). 표시한 값만 추가한다.
--   erd_v03_validate_result(004 + 013 search_path)는 재정의하지 않는다. 종류 목록이 없고 결과 kind·decision·config·llm_spec = 작업 값만 비교하므로 새 종류에 그대로 적용된다
--   (결과 decision_id = 작업 input_decision_id: 알림 대상은 판정 id, 관측 경로 대상은 둘 다 NULL — worker trainer/runner.py _llm_result_decision_id).
-- enum 커밋 분리(010·011·017·021 방식): 새 값은 트랜잭션 밖에서 먼저 커밋하고, 이 파일 안에서는 kind::text 비교로만 쓴다.
--   psql -f 실행: BEGIN..COMMIT 중간 오류면 구조 변경은 전체 ROLLBACK되고 이미 커밋된 enum 값만 남는다. 이 파일을 다시 실행하면 된다(IF NOT EXISTS).
--   한 문자열 실행(tests/prepare-runtime-db.py): BEGIN이 앞선 enum 추가를 같은 트랜잭션으로 흡수하므로 실패 시 enum까지 함께 되돌아간다. 새 값을 enum으로 쓰면 이 경로에서 55P04가 나므로 kind::text만 쓴다.
--   enum만 남은 구간 주의: 021 CHECK의 legacy 분기는 이 값을 막지 않는다. 쓰기 코드가 없어 행이 생기지 않지만, 생기면 재실행의 CHECK 검증이 실패해 전체 ROLLBACK된다(반쪽 적용은 없음).
-- 재실행: enum은 IF NOT EXISTS, CHECK는 같은 이름을 지우고 같은 정의로 다시 만들고, 함수는 CREATE OR REPLACE라 결과가 같다.
-- 잠금(COMMIT까지 유지): run_jobs·run_llm_results에 ACCESS EXCLUSIVE, 읽기·쓰기 모두 대기. 표 재작성 없음. CHECK 검증으로 heap을 읽는다(run_jobs 1회, run_llm_results 2회).
--   적용 절차: 017·021과 같이 작업자(Detector·Collector·Trainer·LLM·API)를 멈추고 적용한다. 순서는 027 → 역할 스크립트 재적용(LLM 룰 세트 이력 SELECT) → LLM → API 배포.
--   기존 코드는 새 값을 쓰지 않아 027 뒤에도 수정 없이 동작한다.
-- NOT VALID를 쓰지 않는 근거: 새 조건은 이전 조건보다 넓기만 하고(legacy 금지 목록은 027 전에는 없던 값만 추가), 기존 행이 실패할 수 없다. 017·021과 같이 트랜잭션 안에서 바로 검증한다.
-- 되돌리기: enum 값은 지울 수 없다. CHECK·함수는 021·017본 정의로 다시 만들 수 있으나 kind = explain_protocol 작업·결과 행이 하나도 없을 때만 가능하다.
-- database-roles.sql 변경(이 PR): LLM이 사실을 만들 때 룰 세트 리비전을 읽으므로 GRANT SELECT ON ops_rule_set_revisions TO powercode_llm 한 줄.
--   그 밖의 읽기(run_jobs·run_process_decisions·data_observations)와 쓰기(run_jobs·run_job_attempts UPDATE, run_llm_results INSERT, llm_explanation_jobs SELECT·UPDATE)는 이미 있다.
--   API는 run_jobs INSERT·UPDATE와 powercode_queue.llm_explanation_jobs INSERT를 이미 가진다.
-- 바꾸지 않는 것과 근거(001~026 전체에서 kind 목록을 가진 함수·제약 확인):
--   erd_v03_validate_job의 explain 전용 판정 구성 대조(IF NEW.kind = 'explain')는 이 kind에 적용하지 않는다. 프로토콜 판정은 v04(구성 NULL)라 구성 대조가 뜻이 없고, 작업 구성은 CHECK가 NULL로 고정한다.
--   작업 실패 트리거(010 import_csv·011 evaluation_set·012 detect)와 011 evaluation_set·compare 보호 함수는 각자 자기 kind만 처리한다. powercode_queue.llm_explanation_jobs(006, training_jobs LIKE — payload 객체 CHECK만, kind 조건 없음)도 그대로다.
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'explain_protocol';
BEGIN;

-- 1. run_jobs. 021본에서 바뀐 곳: legacy 금지 목록에 값 추가, explain_protocol 분기 추가. 나머지 분기는 그대로다.
--    explain_protocol 분기: 관리자만(프로토콜 설정·프로토콜 알림이 관리자 화면). 알림 대상은 input_decision_id = 판정 id, 관측 경로 대상은 NULL(둘 다 허용).
ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain')
  AND kind::text NOT IN ('import_csv','evaluation_set','compare','recommend_methods','recommend_thresholds','recommend_relations','explain_protocol')
  AND requested_by_actor_id IS NOT NULL AND requester_role IS NOT NULL
  AND queue IS NULL AND producer_kind IS NULL AND producer_key IS NULL
  AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
 OR (contract_version IS NOT NULL AND contract_version = 'v03' AND queue IS NOT NULL
  AND producer_kind IS NOT NULL AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND ((kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
    AND requested_by_actor_id IS NULL AND requester_role IS NULL
    AND input_manifest_id IS NOT NULL AND input_config_version_id IS NOT NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('preview','train','evaluate') OR kind::text IN ('import_csv','evaluation_set','compare','recommend_relations')) AND queue = 'training' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
   OR ((kind IN ('recommend_rule','recommend_feature') OR kind::text IN ('recommend_methods','recommend_thresholds')) AND queue = 'llm_recommendation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind = 'explain' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role IN ('user','admin')
    AND input_decision_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind::text = 'explain_protocol' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin'
    AND input_manifest_id IS NULL AND input_config_version_id IS NULL AND llm_spec IS NOT NULL)))
 OR (contract_version IS NOT NULL AND contract_version = 'v04' AND kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
  AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND requested_by_actor_id IS NULL AND requester_role IS NULL
  AND input_manifest_id IS NOT NULL AND input_config_version_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
) IS TRUE);

-- 2. run_llm_results. 017본에서 바뀐 곳: kind 목록에 값 추가, explain_protocol 분기(판정은 알림 대상만이라 decision_id 조건 없음 — 결과 판정 = 작업 판정은 erd_v03_validate_result가 강제).
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_kind_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_kind_check
 CHECK(kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods','recommend_thresholds','explain_protocol'));
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_check
 CHECK((kind::text = 'explain' AND decision_id IS NOT NULL)
  OR (kind::text IN ('recommend_rule','recommend_feature','recommend_methods','recommend_thresholds') AND decision_id IS NULL)
  OR kind::text = 'explain_protocol');

-- 3. 작업 검증(017 기준). 바뀐 줄: llm_spec 필수 목록과 LLM 완료 결과 필수 목록, 2줄. CREATE OR REPLACE는 함수 설정을 비우므로 SET search_path를 017과 같이 다시 적는다. 소유자·SECURITY INVOKER 유지.
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
 IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods','recommend_thresholds','explain_protocol') THEN
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
  IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods','recommend_thresholds','explain_protocol') THEN
   IF NOT EXISTS(SELECT 1 FROM run_llm_results WHERE job_id = NEW.id AND created_attempt_id = a.id) THEN RAISE EXCEPTION 'LLM completion requires result'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

COMMIT;
