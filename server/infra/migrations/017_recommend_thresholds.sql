-- 017: 소프트룰 임계값 일괄 추천 작업 종류 recommend_thresholds (#124, 계약 server/contracts/llm-recommendation-job.md 마이그레이션·코드 영향 절, trainer-service.md 계약 누락 1·2번, queues.md).
-- 새 작업 종류만 받도록 한다. recommend_rule·recommend_feature·recommend_methods·explain·detect(v03·v04)의 규칙은 그대로다. API 접수·워커 처리는 각 Issue 범위이고 이 파일만으로 새 작업이 생기지 않는다.
-- 전제: 001~016 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표·함수 소유자로 실행(CREATE OR REPLACE는 소유자만 가능). 새 표 없음 → database-roles.sql 변경 없음. 자동 down migration 없음(enum 값은 지울 수 없다).
-- enum 커밋 분리(010·011 방식): 새 값은 트랜잭션 밖에서 먼저 커밋하고, 이 파일 안에서는 kind::text 비교로만 쓴다.
--   psql -f 실행: BEGIN..COMMIT 중간 오류면 구조 변경은 전체 ROLLBACK되고 이미 커밋된 enum 값만 남는다. 이 파일을 다시 실행하면 된다(IF NOT EXISTS).
--   한 문자열 실행(tests/prepare-runtime-db.py): BEGIN이 앞선 enum 추가를 같은 트랜잭션으로 흡수하므로 실패 시 enum까지 함께 되돌아간다. 새 값을 enum으로 쓰면 이 경로에서 55P04가 나므로 kind::text만 쓴다.
--   enum만 남은 구간 주의: 이전 CHECK의 legacy 분기는 이 값을 막지 않는다. 쓰기 코드가 없어 행이 생기지 않지만, 생기면 재실행의 CHECK 검증이 실패해 전체 ROLLBACK된다(반쪽 적용은 없음).
-- 기준: run_jobs_v03_contract·run_llm_results_check·erd_v03_validate_job은 016 재정의본, run_llm_results_kind_check는 011본(016이 재정의하지 않음). 표시한 값만 추가한다.
--   erd_v03_validate_result는 재정의하지 않는다. 종류 목록이 없고 kind·decision·config·llm_spec 일치만 비교하므로 새 종류에 그대로 적용된다.
-- 구성·입력 판단: 이 작업의 입력은 룰 세트 리비전이고(input_spec) 구성은 없다. llm_recommendation 분기는 input_config_version_id 조건이 원래 없고, run_llm_results_check의 NULL 분기도 구성 조건이 없으며,
--   erd_v03_validate_result는 결과 구성 = 작업 구성(둘 다 NULL)만 비교한다. 그래서 구성 조건을 풀거나 더하지 않는다. 리비전을 검사할 열도 만들지 않는다(입력 고정은 API, 값 대조는 Trainer 몫).
-- 잠금(COMMIT까지 유지): run_jobs·run_llm_results에 ACCESS EXCLUSIVE, 읽기·쓰기 모두 대기. 표 재작성 없음. CHECK 검증으로 heap을 읽는다(run_jobs 1회, run_llm_results 2회, TOAST 값은 읽지 않음).
--   적용 절차: 012·016과 같이 Detector·Collector·Trainer·LLM·API 작업자를 멈추고 적용한다. 순서는 017 → API·워커 배포이고, 기존 코드는 새 값을 쓰지 않아 017 적용 뒤에도 수정 없이 동작한다.
-- NOT VALID를 쓰지 않는 근거: 새 조건은 이전 조건보다 넓어 기존 행이 실패할 수 없고(legacy 금지 목록은 아직 없는 값만 추가), 010·011·016과 같이 트랜잭션 안에서 바로 검증한다.
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'recommend_thresholds';
BEGIN;

-- 1. run_jobs. 016본에서 바뀐 곳: legacy 금지 목록과 llm_recommendation 분기에 값 추가. v04 분기와 explain 분기는 그대로다.
ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain')
  AND kind::text NOT IN ('import_csv','evaluation_set','compare','recommend_methods','recommend_thresholds')
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
   OR ((kind IN ('recommend_rule','recommend_feature') OR kind::text IN ('recommend_methods','recommend_thresholds')) AND queue = 'llm_recommendation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role = 'admin' AND input_manifest_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NOT NULL)
   OR (kind = 'explain' AND queue = 'llm_explanation' AND producer_kind = 'user'
    AND requested_by_actor_id IS NOT NULL AND requester_role IN ('user','admin')
    AND input_decision_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)))
 OR (contract_version IS NOT NULL AND contract_version = 'v04' AND kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
  AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND requested_by_actor_id IS NULL AND requester_role IS NULL
  AND input_manifest_id IS NOT NULL AND input_config_version_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
) IS TRUE);

-- 2. run_llm_results. 결과는 판정 없이 저장한다(decision_id NULL 분기). explain 분기는 016본 그대로 구성 조건이 없다.
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_kind_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_kind_check
 CHECK(kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods','recommend_thresholds'));
ALTER TABLE run_llm_results DROP CONSTRAINT run_llm_results_check;
ALTER TABLE run_llm_results ADD CONSTRAINT run_llm_results_check
 CHECK((kind::text = 'explain' AND decision_id IS NOT NULL)
  OR (kind::text IN ('recommend_rule','recommend_feature','recommend_methods','recommend_thresholds') AND decision_id IS NULL));

-- 3. 작업 검증(016 기준). 바뀐 줄: llm_spec 필수 목록과 LLM 완료 결과 필수 목록, 2줄. CREATE OR REPLACE는 함수 설정을 비우므로 SET search_path를 016과 같이 다시 적는다. 소유자·SECURITY INVOKER 유지.
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
 IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods','recommend_thresholds') THEN
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
  IF NEW.kind::text IN ('recommend_rule','recommend_feature','explain','recommend_methods','recommend_thresholds') THEN
   IF NOT EXISTS(SELECT 1 FROM run_llm_results WHERE job_id = NEW.id AND created_attempt_id = a.id) THEN RAISE EXCEPTION 'LLM completion requires result'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

COMMIT;
