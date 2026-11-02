-- 013: 001~010 트리거 함수의 search_path 고정 (#82, #78 검수에서 발견한 기존 결함).
-- 대상 8개는 표·행 타입을 스키마 한정 없이 참조하므로 호출자 세션의 search_path로 이름을 해석했다.
--   런타임 역할이 같은 이름의 세션 임시 테이블을 만들면 대상 표가 가려져 검증·불변·실패 동기화가 우회됐다
--   (예: lease 만료 시도의 판정 행 수락, ready 스냅샷 멤버 INSERT, 준비 CSV 실패 동기화 흡수).
-- 011과 같은 설정값 public, pg_temp(pg_temp 맨 뒤 → 임시 테이블이 public보다 먼저 해석되지 않음, pg_catalog는 암묵적 맨 앞).
--   함수 본문·SECURITY INVOKER·소유자는 그대로 두고 ALTER FUNCTION … SET만 바꾼다.
-- 이미 고정된 함수는 건드리지 않음: 011 erd_v03_validate_job 등 4개(public, pg_temp), 012 publish_decision_event 등 6개(pg_catalog, pg_temp).
-- 표를 참조하지 않는 001~010 함수 7개(erd_v03_immutable·erd_v03_guard_decision·prevent_rule_version_mutation·
--   prevent_pipeline_immutable_mutation·guard_demo_state·guard_input_replay·guard_input_run)는 임시 테이블 경로가 없어 제외.
-- 전제: 001~012 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 함수 소유자(= 001~010 적용 역할)로 실행.
--   enum 변경 없음 → 단일 트랜잭션, 오류 시 전체 ROLLBACK. 같은 파일 재실행도 결과 동일. 자동 down migration 없음.
--   pg_proc 갱신만 하므로 표 잠금 없음. 작업자 재시작 불필요: 이미 열린 세션도 COMMIT 뒤 다음 문장부터 새 설정으로 실행
--   (일반 문장·PREPARE한 문장·진행 중 트랜잭션의 다음 문장 모두, PostgreSQL 17 격리 시험으로 확인).
BEGIN;
-- 004_erd_v03_contracts.sql
ALTER FUNCTION public.erd_v03_validate_body() SET search_path = public, pg_temp;
ALTER FUNCTION public.erd_v03_validate_manifest() SET search_path = public, pg_temp;
ALTER FUNCTION public.erd_v03_validate_result() SET search_path = public, pg_temp;
ALTER FUNCTION public.erd_v03_validate_artifact() SET search_path = public, pg_temp;
ALTER FUNCTION public.erd_v03_protect_observation() SET search_path = public, pg_temp;
-- 004_feature_pipeline.sql
ALTER FUNCTION public.protect_ready_snapshot_members() SET search_path = public, pg_temp;
-- 010_prepared_csv_import.sql
ALTER FUNCTION public.protect_prepared_dataset() SET search_path = public, pg_temp;
ALTER FUNCTION public.fail_prepared_dataset_with_job() SET search_path = public, pg_temp;
COMMIT;
