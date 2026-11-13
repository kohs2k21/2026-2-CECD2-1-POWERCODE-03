-- 021: 규칙 관계 추천(층 B) 후보 작업 종류 recommend_relations.
-- 왜: WebUI가 관계 추천 후보 작업 요청 API를 만들고 Trainer가 training 큐로 처리한다. 결과는 run_jobs.result_summary에만 쓴다(새 표·run_llm_results·llm_spec 없음).
--   v03 training 분기(user producer·admin·llm_spec NULL·manifest/decision NULL)에 이 값을 넣고, legacy(contract_version NULL) 금지 목록에도 넣어 v03 전용으로 만든다(017 방식).
-- 전제: 001~020 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 자동 down migration 없음(enum 값은 지울 수 없다).
-- 기준: run_jobs_v03_contract는 017 재정의본(018~020은 run_jobs를 건드리지 않는다). 바꾼 곳은 legacy 금지 목록과 training 분기 목록 2곳뿐이다.
-- enum 커밋 분리(010·011·017 방식): 새 값은 트랜잭션 밖에서 먼저 커밋하고, 이 파일 안에서는 kind::text 비교로만 쓴다.
--   psql -f 실행: BEGIN..COMMIT 중간 오류면 CHECK 변경은 ROLLBACK되고 이미 커밋된 enum 값만 남는다. 이 파일을 다시 실행하면 된다(IF NOT EXISTS).
--   한 문자열 실행(tests/prepare-runtime-db.py): BEGIN이 앞선 enum 추가를 같은 트랜잭션으로 흡수하므로 실패 시 enum까지 함께 되돌아간다. 새 값을 enum으로 쓰면 이 경로에서 55P04가 나므로 kind::text만 쓴다.
--   enum만 남은 구간 주의: 017 CHECK의 legacy 분기는 이 값을 막지 않는다. 쓰기 코드가 없어 행이 생기지 않지만, 생기면 재실행의 CHECK 검증이 실패해 전체 ROLLBACK된다(반쪽 적용은 없음).
-- 재실행: enum은 IF NOT EXISTS, CHECK는 같은 이름을 지우고 같은 정의로 다시 만들어 결과가 같다.
-- 잠금(COMMIT까지 유지): run_jobs에 ACCESS EXCLUSIVE, 읽기·쓰기 모두 대기. 표 재작성 없음. 새 CHECK 검증으로 run_jobs heap을 1회 읽는다. 다른 표는 잠그지 않는다.
--   적용 절차: 017과 같이 작업자(Detector·Collector·Trainer·LLM·API)를 멈추고 적용한다. 순서는 021 → Trainer → API 배포이고, 기존 코드는 새 값을 쓰지 않아 021 뒤에도 수정 없이 동작한다.
-- NOT VALID를 쓰지 않는 근거: training 분기는 넓어지기만 하고, legacy 금지 목록은 021 전에는 없던 값만 추가하므로 기존 행이 실패할 수 없다. 017과 같이 트랜잭션 안에서 바로 검증한다.
-- 되돌리기: enum 값은 지울 수 없다. CHECK는 017본 정의로 다시 만들 수 있으나 kind = recommend_relations 행이 하나도 없을 때만 가능하다(v03 행이 017 training 분기를 통과하지 못함).
-- database-roles.sql 변경 없음: 새 표·시퀀스·함수가 없다. API는 run_jobs INSERT·UPDATE와 powercode_queue.training_jobs INSERT를,
--   Trainer는 run_jobs·run_job_attempts INSERT·UPDATE와 training_jobs SELECT·UPDATE를 이미 가진다.
-- 바꾸지 않는 것과 근거(001~020 전체에서 kind 목록을 가진 함수·제약 확인):
--   erd_v03_validate_job(017본): kind 목록은 LLM(llm_spec 필수·완료 시 run_llm_results 필수)과 detect·explain뿐이라, 이 kind는 공통 규칙(producer 일치, 입력 불변, succeeded 전환 때 현재 미만료 시도)만 받는다.
--   run_llm_results_kind_check·run_llm_results_check: 이 kind는 결과 표를 쓰지 않으므로 목록에 넣지 않는다(넣지 않아 결과 행이 거부된다). erd_v03_validate_result는 kind 목록이 없다.
--   작업 실패 트리거 fail_prepared_dataset_with_job(010, import_csv)·fail_evaluation_set_with_job(011, evaluation_set)·close_decision_interims_on_job_failure(012, detect),
--   protect_evaluation_set·validate_comparison_lineage(011, evaluation_set·compare 작업 요구): 각자 자기 kind만 처리하므로 이 kind의 failed 전환(dead 큐 정리 포함)과 succeeded 전환을 막지 않는다.
--   run_jobs의 다른 CHECK·인덱스(v03 producer key UNIQUE, detect 전용 partial UNIQUE, queue 인덱스)와 powercode_queue.training_jobs(payload 객체 CHECK만, kind 조건 없음)도 그대로 적용된다.
ALTER TYPE job_kind ADD VALUE IF NOT EXISTS 'recommend_relations';
BEGIN;

-- run_jobs. 017본에서 바뀐 곳: legacy 금지 목록과 training 분기 목록에 값 추가. detect·llm_recommendation·explain·v04 분기는 그대로다.
ALTER TABLE run_jobs DROP CONSTRAINT run_jobs_v03_contract;
ALTER TABLE run_jobs ADD CONSTRAINT run_jobs_v03_contract CHECK ((
 (contract_version IS NULL AND kind NOT IN ('detect','recommend_feature','explain')
  AND kind::text NOT IN ('import_csv','evaluation_set','compare','recommend_methods','recommend_thresholds','recommend_relations')
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
    AND input_decision_id IS NOT NULL AND input_manifest_id IS NULL AND llm_spec IS NOT NULL)))
 OR (contract_version IS NOT NULL AND contract_version = 'v04' AND kind = 'detect' AND queue = 'detection' AND producer_kind = 'system'
  AND producer_key IS NOT NULL AND idempotency_key IS NOT NULL
  AND requested_by_actor_id IS NULL AND requester_role IS NULL
  AND input_manifest_id IS NOT NULL AND input_config_version_id IS NULL AND input_decision_id IS NULL AND llm_spec IS NULL)
) IS TRUE);

COMMIT;
