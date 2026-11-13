Created: 2026-10-12T02:06:03+09:00
Updated: 2026-11-14T12:00:00+09:00
Author: contracts_page_reviewer
Status: current

# ERD v0.3와 호환 migration

- Issue #32 기준: 최신 PostgreSQL·공통 규약·LLM 기획 반영. `schema.dbml`은 최종 목표 구조의 시각화, 실행 원본은 migration SQL
- 목표 구조(Issue #32 당시): public 28테이블 + `powercode_queue` 기존 물리 큐 2테이블. 기존 public 24테이블 보존, BODY·BODY 연결·불변 입력·LLM 결과 4테이블만 추가
- 현재 `schema.dbml`: 001~023 전체 + 024 [제안] + 025·026 반영(024·025·026 절), public 73테이블 + `powercode_queue` 5테이블. 004_feature_pipeline·005~009 추가분 24테이블 보강, 011 3테이블·012 1테이블·014 3테이블·015 3테이블·019 4테이블·022 4테이블·023 2테이블·024 1테이블·025 2테이블 추가, 026은 022 표에 열 1개 추가. 013(#82)은 함수 설정만 바꿔 DBML 변경 없음
- 기존 001/002 불변. 003/004는 서버 미적용; ERD 등록·SQL 검수와 실제 운영 적용 구분
- PostgreSQL 17.11 실제 public24+queue2 존재 확인 기록과 정합. 서버 schema/version 이력 및 완전 DDL 일치는 별도 사전 확인 필요

## 적용 순서와 기존 행

1. 실제 schema·enum·제약을 001/002 기준과 대조, 쓰기 영향·잠금 시간 검토, 백업과 복원 시험
2. `003_erd_v03_enums.sql` 실행·COMMIT. enum 추가와 사용을 같은 미커밋 트랜잭션으로 묶지 않기
3. 별도 트랜잭션의 `004_erd_v03_contracts.sql` 실행. 오류 시 전체 구조 변경 ROLLBACK; 003의 enum은 이미 남음
4. 신규 worker/API 계약 구현·혼재 시험 후 v03 생산자 활성화. SQL 적용만으로 기존 worker 전환 없음

- 각 파일은 `psql -v ON_ERROR_STOP=1 -f <파일>`처럼 오류에서 중단되는 runner로 별도 실행. 운영 실행 승인·유효 자격증명·대상 DB는 별도 확인
- 004는 한 번 실행하는 migration. 반복 실행·파일명만으로 적용 완료 추정 금지, 적용 hash·담당·시각 기록 필요
- `run_jobs.contract_version IS NULL`은 legacy. 과거 manifest·producer·queue·LLM version을 가짜 값으로 backfill하지 않음
- job의 contract_version은 등록 후 변경 금지. 기존 작업을 사후 v03로 변환하지 않고 신규 계약은 새 job으로 등록
- 기존 job 요청자·role·멱등키·unique 제약 보존. nullable 요청자/role은 v03 system detect만 허용, legacy와 사용자 작업은 필수
- 기존 판정의 `job_id/input_manifest_id/result_attempt_id/result_lease_token`은 모두 NULL 유지 가능. 새 참조를 쓰는 판정은 모두 명시
- 기존 파일 참조는 보존. `model_input_artifact_id`는 선택적 캐시 계보, 모든 모델 입력을 영구 파일로 저장하는 요구 아님
- 신규 enum 제거·기존 테이블 삭제·전체 DB 재생성·자동 down migration 없음

## 011 평가 세트·구성 비교·LLM 방법 추천 (#78, 계약 #74)

- 파일 `011_config_comparison_recommendation.sql`, 010 이후 한 번 실행. 001~010은 수정하지 않음, 자동 down migration 없음
- enum 커밋 분리(010 `import_csv` 방식): `job_kind`에 `evaluation_set`·`compare`·`recommend_methods`를 트랜잭션 밖 `ADD VALUE IF NOT EXISTS`로 먼저 추가. 같은 파일 안에서는 새 값을 `kind::text` 비교로만 사용하므로 한 문자열 실행(CI `prepare-runtime-db.py`)에서도 미커밋 enum 사용 오류 없음
- `psql -v ON_ERROR_STOP=1 -f`로 실행하면 BEGIN..COMMIT 중간 오류 시 구조 변경 전체 ROLLBACK, 이미 커밋된 enum 3값만 남음. 같은 파일 재실행 가능(enum은 IF NOT EXISTS). 재시험은 격리 DB를 새로 만들어 수행
- 운영 적용은 worker·API 쓰기 정지 또는 짧은 유지보수 창에서 실행 권고. `run_jobs`·`run_evaluations`·`run_llm_results` ACCESS EXCLUSIVE 잠금, `run_jobs` 제약 전체 재검증
- 파일 전체를 한 문자열로 실행(`prepare-runtime-db.py` 등)하면 BEGIN이 앞선 enum 추가를 같은 트랜잭션으로 흡수하므로, 실패 시 psql 파일별 실행과 달리 enum 추가분까지 함께 ROLLBACK
- `run_jobs_v03_contract` 재정의: `evaluation_set`·`compare`는 `training` 큐·user producer·admin, `recommend_methods`는 `llm_recommendation` 큐·admin·`llm_spec` 필수. 새 kind 3개는 legacy(`contract_version IS NULL`) 분기 사용 금지. 제약 재생성은 `run_jobs` 전체 검증·ACCESS EXCLUSIVE 잠금
- `erd_v03_validate_job()` 재정의: `llm_spec` 필수 문자열·"LLM completion requires result" kind 목록에 `recommend_methods`. 결과 존재 검사는 중첩 IF로 LLM kind에만 실행(`kind::text`는 상수 접힘이 없어 비LLM 완료에 `run_llm_results` 권한을 요구하게 되는 회귀 방지). evaluate 전용 완료 조건은 원래 없어 `compare` 분기 추가 없음
- `run_llm_results`: kind CHECK에 `recommend_methods`, `decision_id IS NULL` 분기에 포함
- `data_evaluation_sets`: INSERT는 `queued`·미정 열 NULL·`evaluation_set` 작업·ready non-prepared 같은 source snapshot만. `queued→ready|failed` 1회 전환만, 그 뒤 UPDATE 금지, DELETE는 상태 무관 금지. ready는 `generation`·`content_hash`·`ready_at`, failed는 `error_code` 필수. 산출물 경로 `evaluation-sets/<id>/<content_hash>.json`
- 작업 실패 동기화: `evaluation_set` 작업이 `failed`가 되면 같은 트랜잭션 AFTER trigger가 `queued` 세트를 `failed`(작업 `error_code`, 없으면 `evaluation_set_failed`)로 바꿈. Trainer 종료·시도 초과·dead 큐 정리 3경로 코드 수정 불필요. ready 세트는 바꾸지 않음
- `run_config_comparisons`: compare 작업 완료 트랜잭션에서 1회 INSERT(ready 세트·같은 source·baseline source 일치), 이후 UPDATE·DELETE 금지. `run_config_comparison_items`: active는 baseline 구성과 일치·비교당 1개, succeeded는 같은 비교·같은 구성의 평가 행 필수, failed는 `error_code` 필수·평가 없음. 불변
- `run_evaluations`: `comparison_id`·`evaluation_set_id` nullable 열 추가, `UNIQUE(job_id)` → `UNIQUE(job_id, config_version_id)`. 비교 밖 작업은 partial unique로 job당 1행 유지. 비교 행은 세트 snapshot·purpose·source와 비교 job·세트 일치 trigger. 같은 트랜잭션 INSERT 순서는 comparison → run_evaluations → items
- `ml_config_versions`는 004_feature_pipeline `pipeline_immutable`로 이미 UPDATE·DELETE 금지, 011은 신규 trigger 없음
- 011이 만들거나 재정의한 함수 4개(`erd_v03_validate_job`·`protect_evaluation_set`·`fail_evaluation_set_with_job`·`validate_comparison_lineage`)는 `SET search_path = public, pg_temp`로 고정, SECURITY INVOKER 유지. 세션 임시 테이블의 같은 이름 가리기로 검증·동기화를 우회하지 못함. 001~010 함수 중 표를 참조하는 10개에서 011·012가 재정의·고정한 `erd_v03_validate_job`·`publish_decision_event` 2개를 뺀 8개는 013(#82)에서 같은 값으로 고정
- 권한(`scripts/database-roles.sql`): API 세트 SELECT·INSERT, Trainer 세트 SELECT·UPDATE·비교 2테이블 SELECT·INSERT, LLM은 dead training 큐 정리 trigger용 세트 `SELECT(job_id,status)`·`UPDATE(status,error_code)`만, API 비교 SELECT
- 010 준비 CSV 세트 LLM 권한(#81): 같은 dead training 큐 정리가 `import_csv` 작업을 failed로 바꾸면 010 `fail_prepared_dataset_with_job` 트리거(호출자 권한)가 queued 세트를 failed로 바꾸므로 `powercode_llm`에 `data_prepared_datasets` `SELECT(created_job_id,status,validation_result)`·`UPDATE(status,validation_result)`만 부여(이전에는 권한 없음 → 42501로 정리 트랜잭션 실패·worker 종료). 내용 열·INSERT·DELETE 없음, ready·failed 세트는 010 보호 트리거로 불변. 남는 위험: LLM 역할이 queued 세트를 직접 failed로 바꾸고 `validation_result`를 덮어쓸 수 있음(011 평가 세트 LLM 권한과 같은 수준)

## 012 Detector 중간 판정 기록 (#78 B)

- 파일 `012_decision_interims.sql`, 011 이후 한 번 실행. 001~011 수정 없음, enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음. 011과 객체 겹침 없음
- `run_decision_interims`: 최종 판정 행 이전의 append-only 기록. kind `first_anomaly`·`evidence_added`는 Detector가 쓰며 현재 시도·유효 lease·token 일치와 `lease_token`·`evidence_version` 필수, `superseded`·`no_final`은 DB 트리거 전용. `seq`·`created_at`은 DB 부여, 시도당 첫 이상·닫힘 각 1건(partial unique), 판정 행 뒤·닫힌 시도 기록 거부, UPDATE·DELETE 금지. payload는 object만 강제(내용은 Detector evidence 계약)
- 대체됨(R2 (ii)): 새 시도의 첫 이상 또는 판정 행 INSERT와 같은 TX에서 같은 작업 다른 시도의 열린 묶음마다 기록. 이벤트는 새 시도 기록 이벤트 뒤(#78-Q4) — 트리거 이름 알파벳순 의존(`interim_event_insert` < `interim_supersede`, `decision_event_insert` < `decision_supersede_interims`), 이름 변경 시 erd-v03 S4·S5 순서 시험 재확인. #78-Q11 가드는 자체 잠금으로 순서 비의존
- 최종 없음: detect 작업이 `failed`로 바뀌는 같은 TX에서, 판정 행이 없고 열린 묶음이 있을 때만 기록(#78-Q3). `reason_code` = 작업 `error_code`(없으면 `job_failed`). 실패 3경로(Trainer 종료 3회째·claim 시도 초과·dead 큐 정리)가 모두 같은 `UPDATE run_jobs … 'failed'`로 끝나므로 코드 수정 불필요
- #78-Q11: 같은 작업에 `no_final`이 있으면 판정 행 INSERT 거부(`decision_no_final_guard`, BEFORE INSERT, 호출자 권한). 정상 코드는 failed를 끝 상태로 두므로(재처리는 새 작업) 정상 경로 영향 없음. `run_jobs`·`run_job_attempts` UPDATE 권한으로 failed 작업을 running으로 되돌린 뒤 판정을 넣는 경로를 닫음. 가드는 검사 전에 `run_jobs` 행을 스스로 `FOR UPDATE`로 잠가 트리거 이름순·004 검증 잠금에 의존하지 않음 — `no_final`은 같은 작업 행의 failed 전환 TX에서만 생기므로 잠금 뒤 새 스냅숏이 커밋된 `no_final`을 봄(잠금 없는 검사는 미커밋 failed 전환·대기 중 되돌리기와 겹치면 통과, erd-v03 Q11 race 회귀). `FOR UPDATE`에는 `run_jobs` UPDATE 권한이 필요하며 판정을 쓰는 Detector는 보유
- `run_decision_events` 확장: `event_kind`(`final`·중간 4종) NOT NULL·`interim_id` UNIQUE 추가, `decision_id` NOT NULL 해제(UNIQUE 유지), `final` ⇔ `decision_id` 있음 CHECK, `(interim_id, event_kind)` → `run_decision_interims(id, kind)` FK. 기존 행은 fast default로 `final`(행 재작성·UPDATE 트리거 없음, 추가 뒤 DEFAULT 제거). identity 순번·advisory lock(81736,0)·커서 1개 유지. 현행 API 조회(`run_process_decisions` 내부 조인)는 012 뒤에도 최종 이벤트만 전달 — 중간 이벤트 전송은 API·WebUI 계약 확정 후
- #78-Q10 B안: 대체됨·최종 없음은 표 소유자 소유 `SECURITY DEFINER` 트리거 함수 2개(`supersede_decision_interims`·`close_decision_interims_on_job_failure`)만 기록. 검증 함수가 `current_user` = 표 소유자를 요구, 두 함수는 `REVOKE EXECUTE … FROM PUBLIC`(런타임 역할이 자기 임시 표 트리거에 붙이지 못함). 012 함수 6개 모두 `SET search_path = pg_catalog, pg_temp`, 본문의 표·행 타입 `public.`·카탈로그 `pg_catalog.` 한정
- 권한(`scripts/database-roles.sql`): `run_decision_interims` SELECT 5개 런타임 역할, INSERT Detector. 작업을 failed로 바꾸는 다른 역할은 추가 권한 불필요. `REVOKE TEMPORARY ON DATABASE <현재 DB> FROM PUBLIC`(DO 블록, 런타임 코드 TEMP 미사용, DB 소유자·superuser는 유지). 역할 스크립트는 012 적용 뒤 재적용(012 이전 DB에서는 표가 없어 GRANT 실패)
- 보장 범위: 닫힘 기록(대체됨·최종 없음)이 그 시점 DB 상태와 일치함까지. 상태 전이가 정상 코드 경로였음은 보장하지 않음(`run_jobs` UPDATE는 api·detector·trainer·llm, `run_job_attempts` UPDATE는 detector·trainer·llm 보유). #78-Q11은 판정 행만 막음 — failed→running으로 되돌린 뒤 새 시도의 `first_anomaly` 기록은 범위 밖(정상 코드에서 failed는 끝 상태)
- 위협 모델: 런타임 서비스 로그인이 `database-roles.sql` 권한과 기본 PUBLIC 권한만 가진 구성이 범위. superuser·소유자 멤버십(`INHERIT FALSE, SET TRUE` 포함)·스키마/DB CREATE·관련 표 TRIGGER·`session_replication_role` SET·객체 또는 DB 소유(소속 그룹 포함) 같은 DBA 추가 권한은 범위 밖(트리거를 끄거나 소유자 문맥에 코드를 끼울 수 있음). 런타임이 소유자 계정으로 접속하면 소유자 검사 무효 — `compose.demo.yaml`은 서비스와 migrate가 같은 `DATABASE_URL`, `compose.yaml`은 서비스별 URL이 없으면 migrate와 같은 공통 `DATABASE_URL`로 대체하므로(코드 기준) 서비스별 로그인 분리(`DOCKER_DEPLOYMENT.md`) 후에만 B안이 유효
- 기존 결함(012 범위 밖, 013에서 수정): 004 `erd_v03_validate_result`는 search_path 고정이 없어 임시 표 이름 가리기로 lease 만료 시도의 판정 행이 수락될 수 있었음. 013(#82)이 고정해 erd-v03 Y7은 004 거부를 단언. 012는 그런 판정이 더 새 시도의 묶음을 대체하는 것도 거부(Y7A2 — 013 뒤에는 004가 먼저 거부, 어느 층이 거부했는지 PASS 줄에 기록)

### 012 적용 절차

1. Detector·API 작업자 중지. `run_decision_events` ALTER와 `run_process_decisions`·`run_jobs` 트리거 추가가 ACCESS EXCLUSIVE 잠금을 잡음
2. 소유자 확인: 아래 소유자 쿼리를 012를 적용할 역할로 실행 → 1행·`is_current_user = true`·`objects = 4`(기존 표 `run_jobs`·`run_job_attempts`·`run_process_decisions`·`run_decision_events`)
3. 012 적용(`psql -v ON_ERROR_STOP=1 -f 012_decision_interims.sql`), 이어서 DBA가 `scripts/database-roles.sql` 재적용. 역할 스크립트는 DB 소유자 또는 superuser로 실행(그 외 역할이면 `REVOKE TEMPORARY`가 WARNING만 내고 무시됨 — 5단계 `no_temp`로도 검출). 슈퍼유저로 적용하면 `SECURITY DEFINER` 함수가 슈퍼유저 권한으로 실행되므로 전용 소유자 역할 권장
4. 소유자 일치 재확인: 같은 쿼리 → 1행·`objects = 7`(표 5개 + `SECURITY DEFINER` 함수 2개). 이후 `ALTER … OWNER`·`REASSIGN OWNED`를 했다면 다시 실행(표와 두 함수를 함께 옮김). 2행 이상이면 failed 전환 `UPDATE`가 트리거 오류로 실패 → 작업 running 잔류, dead 큐 정리(`reconcile_dead_jobs`, Detector 루프의 try 밖) 경로는 Detector 루프 정지
5. 위협 모델 확인 쿼리(필요조건, 충분조건 아님): 소유자 제외 모든 로그인을 나열. 런타임 서비스 로그인 행은 모든 열 true여야 함. DBA·migration 로그인은 false가 정상. `owns_nothing`은 소속 그룹이 소유한 객체·DB까지 봄. 예약 역할(`pg_write_all_data`·`pg_maintain` 등)·`CREATEROLE`·다른 DB 객체·이후 부여될 권한은 열에 없음
6. 작업자 재시작

```sql
-- 012 owner check: run as the role that applies 012; expect one row with is_current_user = true
SELECT o AS owner, o = current_user AS is_current_user, count(*) AS objects
  FROM (SELECT pg_catalog.pg_get_userbyid(c.relowner) AS o FROM pg_catalog.pg_class c
         WHERE c.oid IN (to_regclass('public.run_jobs'), to_regclass('public.run_job_attempts'),
                         to_regclass('public.run_process_decisions'), to_regclass('public.run_decision_events'),
                         to_regclass('public.run_decision_interims'))
        UNION ALL
        SELECT pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p
         WHERE p.oid IN (to_regprocedure('public.supersede_decision_interims()'),
                         to_regprocedure('public.close_decision_interims_on_job_failure()'))) x
 GROUP BY o;
```

```sql
-- 012 threat-model check: every runtime service login row must be all true (necessary, not sufficient)
SELECT r.rolname,
       NOT r.rolsuper AS not_superuser,
       NOT pg_has_role(r.oid, c.relowner, 'MEMBER') AS not_member_of_owner,
       NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE has_schema_privilege(r.oid, n.oid, 'CREATE')) AS no_create_schema,
       NOT has_database_privilege(r.oid, current_database(), 'CREATE') AS no_db_create,
       NOT has_database_privilege(r.oid, current_database(), 'TEMP') AS no_temp,
       NOT has_parameter_privilege(r.oid, 'session_replication_role', 'SET') AS no_srr_set,
       NOT EXISTS (SELECT 1 FROM pg_shdepend d
                    WHERE d.deptype = 'o' AND pg_has_role(r.oid, d.refobjid, 'MEMBER')
                      AND d.dbid = (SELECT oid FROM pg_database WHERE datname = current_database()))
         AND NOT pg_has_role(r.oid, (SELECT datdba FROM pg_database WHERE datname = current_database()), 'MEMBER') AS owns_nothing,
       NOT EXISTS (SELECT 1 FROM unnest(ARRAY['public.run_decision_interims','public.run_decision_events','public.run_jobs',
                     'public.run_job_attempts','public.run_process_decisions']::regclass[]) t(rel)
                   WHERE has_table_privilege(r.oid, t.rel, 'TRIGGER')) AS no_trigger_priv
  FROM pg_roles r, pg_class c
 WHERE r.rolcanlogin AND c.oid = 'public.run_decision_interims'::regclass AND r.oid <> c.relowner
 ORDER BY r.rolname;
```

## 013 기존 트리거 함수 search_path 고정 (#82)

- 파일 `013_legacy_function_search_path.sql`, 012 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`). 001~012 수정 없음, enum·표·DBML 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK, 같은 파일 재실행 결과 동일. 자동 down migration 없음
- 대상 8개: 004 `erd_v03_validate_result`·`erd_v03_validate_body`·`erd_v03_validate_manifest`·`erd_v03_validate_artifact`·`erd_v03_protect_observation`, 004_feature_pipeline `protect_ready_snapshot_members`, 010 `protect_prepared_dataset`·`fail_prepared_dataset_with_job`. `ALTER FUNCTION … SET search_path = public, pg_temp`(011과 같은 값), 본문·SECURITY INVOKER·소유자 유지
- 이유: 대상 함수는 표·행 타입을 스키마 한정 없이 참조해 호출자 세션의 search_path로 해석했음. 같은 이름의 세션 임시 테이블로 lease 검사(판정 행)·ready 스냅샷 멤버 불변·준비 CSV 실패 동기화 등이 우회될 수 있었음(#78 검수 발견). `pg_temp`를 맨 뒤에 명시해 임시 테이블이 public보다 먼저 해석되지 않음. `REVOKE TEMP`(012 역할 스크립트)는 런타임 역할 경로만 막고 소유자·TEMP 보유 역할 경로가 남아 함수 고정이 별도로 필요
- 제외: 011 고정 4개(`public, pg_temp`)·012 고정 6개(`pg_catalog, pg_temp`)는 그대로. 표를 참조하지 않는 001~010 함수 7개(`erd_v03_immutable`·`erd_v03_guard_decision`·`prevent_rule_version_mutation`·`prevent_pipeline_immutable_mutation`·`guard_demo_state`·`guard_input_replay`·`guard_input_run`)는 임시 테이블 경로가 없어 미고정
- 적용: 함수 소유자(001~010 적용 역할)로 실행. pg_proc 갱신만 해 표 잠금 없음. 작업자 재시작 불필요 — 이미 열린 세션도 COMMIT 뒤 다음 문장부터 새 설정으로 실행(일반 문장·PREPARE한 문장·진행 중 트랜잭션의 다음 문장, PostgreSQL 17 격리 시험 확인). 권한 스크립트 변경 없음
- 시험(`erd-v03-search-path.mjs`, `erd-v03.mjs`가 호출): `erd82_upgrade`(001~012 + 기존 행 → 013, 행 보존·대상 8개 설정만 변경·본문 md5·소유자·SECURITY INVOKER 동일·적용 후 import_csv 실패 동기화), `erd82_fresh`(001~013, 재실행 불변), `erd82_rollback`(COMMIT 직전 오류 → 무변경 → 재적용), 세 DB 모두 함수 25개 전수 설정 대조와 DBML parity. `erd-v03-interim.mjs` Y7은 013 뒤 004 거부를 PASS로 단언, Y7A2는 013 적용 DB에서 004·012 어느 거부든 허용(거부 층 기록), 001~012만 적용한 `erd_interim_y7a2_012`에서 012 거부 메시지를 정확히 단언

## 014 이상 분석 처리 상태·조사·조치 기록·읽음 (#105)

- 파일 `014_analysis_workflow.sql`, 013 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행 뒤 DBA가 `scripts/database-roles.sql` 재적용. 001~013 표·함수·트리거·enum 변경 없음, 새 표 3개만 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 함수·트리거 없음. 자동 down migration 없음
- 표: `run_decision_workflow_events`(처리 상태 변경 이력, 상태 detected·open·resolved, 허용 전환 CHECK, 대상별 `seq` UNIQUE로 동시 변경 직렬화), `run_decision_feedback`(조사·조치 기록, 유형 3종·사유 1~2000자), `run_decision_reads`(사용자별 마지막 열람 시각, 대상·사용자당 1행)
- 대상 키: detect 작업이 있으면 `job_id`, 작업 없는 이전 형식 판정이면 `decision_id`(CHECK 정확히 하나). 어느 열을 쓸지는 API가 정함(analysis-ui.md)
- 권한: API만 이력·기록 SELECT·INSERT(append-only는 권한으로 보장), 읽음 표는 SELECT·INSERT·`UPDATE(read_at)`. Trainer·LLM·Detector·Collector 권한 없음(조사·조치 기록은 확정 라벨·학습 입력이 아님). 역할 스크립트 적용 뒤 API 역할로 실제 읽기·쓰기 경로 성공, API UPDATE·DELETE 거부(42501)·다른 역할 조회 거부를 `backend/tests/analysis-grants.integration.mjs`(CI 단계 "Analysis API runtime-role grants")가 확인
- 잠금: 새 표의 FK 생성이 `run_jobs`·`run_process_decisions`에 짧은 SHARE ROW EXCLUSIVE 잠금. 적용 중 Detector 판정 INSERT가 잠시 대기할 수 있음
- 기존 판정은 처리 상태 감지됨(행 없음)·모든 사용자에게 미확인으로 시작

## 015 운영 리비전 표·일관 읽기 (#110)

- 파일 `015_rule_set_revisions.sql`, 014 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행 뒤 DBA가 `scripts/database-roles.sql` 재적용. 001~014 표·함수·트리거·enum 변경 없음, 새 표 3개·뷰 1개·트리거 3개만 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 새 함수 없음. 기존 표 잠금 없음. 자동 down migration 없음
- 표(열 이름은 `contracts/rule-set.md`·`contracts/model-version.md` 저장소 절 그대로, 현재 값 = 표별 최대 `revision` 행): `ops_rule_set_revisions`(r0 시드), `ops_active_model_set_revisions`(r0 = 빈 집합 시드), `ops_detection_policy_revisions`(`revision ≥ 1`, 시드 없음, 행 없음 = 리비전 0 내장 기본 정책)
- r0 룰 세트: 하드룰 2개 켬·소프트룰 없음, `saved_by_actor_id = system`, `content_hash`는 rule-set.md 리비전 형식 절 정규 객체의 고정값 `80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89`
- 활성 집합 r0 행을 두는 이유: "현재 값 = 최대 revision 행" 규칙과 이력·복원(`restored_from_revision = 0`)을 룰 세트와 같은 경로로 쓰기 위함. 정책은 r0 행이 없으므로 `restored_from_revision = 0`을 넣을 수 없음(FK 거부) — 내장 기본값으로 돌아가는 저장은 일반 저장으로 기록
- append-only: `BEFORE UPDATE OR DELETE OR TRUNCATE FOR EACH STATEMENT` 트리거 1개씩, 함수는 004 `erd_v03_immutable()` 재사용(NEW·OLD를 읽지 않아 문장 수준·TRUNCATE에도 동작, 표를 참조하지 않아 013과 같이 search_path 경로 없음). 소유자의 문장도 거부되며 조건에 맞는 행이 0개여도 거부. `INSERT … ON CONFLICT DO UPDATE`·UPDATE·DELETE 동작이 있는 `MERGE`도 거부
- 신뢰 경계: 소유자는 `ALTER TABLE … DISABLE TRIGGER`, 슈퍼유저는 `session_replication_role = replica`로 트리거를 끌 수 있으며 이는 위협 모델 밖(서비스별 로그인을 분리한 구성 전제. `powercode_*` 런타임 역할은 소유자·슈퍼유저가 아님)
- 무결성: `revision` 범위, `content_hash` 소문자 hex 64, `snapshot`의 `hardRules`·`softRules`·`models` 배열, `changes` 배열, `summary`·`policy` 객체, `restored_from_revision` 자기 참조 FK와 `revision` 미만. 그 밖의 snapshot 내용 검증은 API 저장 검증 범위
- 공통 저장 잠금: API가 세 표의 저장 트랜잭션에서 `pg_advisory_xact_lock(81739, 0)`을 잡고 현재 리비전을 비교한 뒤 INSERT(마이그레이션은 잡지 않음). API는 UPDATE 권한이 없어 `SELECT … FOR UPDATE`는 쓸 수 없음
- 잠금 획득과 현재 리비전 읽기는 **별도 문장**으로 실행(READ COMMITTED 기준). 한 문장으로 합치면 잠금을 기다리기 전의 스냅샷을 읽어 직전 저장을 놓침
- 일관 읽기: 뷰 `ops_operational_state`, 항상 1행 — `rule_set_revision`·`rule_set_content_hash`·`active_model_set_revision`·`active_models`(snapshot의 `models` 배열)·`detection_policy_revision`·`detection_policy`. 한 문장이 한 스냅샷이므로 READ COMMITTED에서도 세 값이 서로 어긋나지 않음. 정책 행이 없으면 `detection_policy_revision = 0`·`detection_policy` NULL(읽는 쪽이 내장 기본 정책을 씀). 빈 결과는 r0 행 소실 = 저장소 이상이므로 Detector는 fail-closed. 룰 내용은 읽은 리비전으로 `ops_rule_set_revisions`를 다시 조회(append-only라 안전)
- 뷰는 `security_invoker = true`: 호출자 권한으로 표를 읽어 뷰 권한이 표 권한을 넓히지 않음(PostgreSQL 15 이상 필요, compose·CI는 17). 표는 뷰 생성 시 OID로 고정돼 호출자 search_path·임시 테이블 영향 없음
- 권한: `powercode_api` 3표 SELECT·INSERT, `powercode_detector`·`powercode_trainer` 3표 SELECT, 뷰는 셋 다 SELECT(Trainer는 #132에서 추가, 활성 집합·정책 리비전을 결과에 기록). Collector·LLM·demo 권한 없음
- 시험(`erd-v03-revisions.mjs`, `erd-v03.mjs`가 호출): `erd110_fresh`(계약 열 이름, r0 행·hash 재계산, 트리거 3종 거부, CHECK·FK 경계, 실제 LOGIN 역할별 권한, 뷰 결과·커밋 전 비가시·동시 저장 중 일관성, 임시 테이블·search_path 영향 없음), `erd110_upgrade`(001~014 + 기존 행 → 015, 행과 001~014 함수·enum·열·제약·트리거·인덱스 불변), `erd110_rollback`(COMMIT 직전 오류 → 무변경 → 재적용). 모두 DBML parity

## 016 v04 판정 경로·룰 세트 hash 버전·시스템 행 r1 (#119)

- 파일 `016_detect_v04_contract.sql`, 015 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표·함수 소유자로 실행. enum 변경·새 표 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK, `database-roles.sql` 변경 없음. 자동 down migration 없음(v04 행이 생기면 NOT NULL 복원 불가, r1은 불변 행)
- 왜: 판정 1건이 구성 버전 1개에 묶이지 않고(활성 모델 0..N) 룰만으로도 판정한다(`contracts/detection-decision.md` 10·12·13절, 15절 K16·K17). 리비전은 작업 입력·판정 열이 아니라 시도 시작마다 읽어 evidence v4에만 기록
- `run_jobs.contract_version = 'v04'`: detect만 허용, `input_config_version_id` NULL 필수, manifest는 v03 형식 그대로. 중복 방지 `run_jobs_v04_detect_idx`(kind, input_manifest_id), 멱등 키 `run_jobs_v04_producer_key_idx`, 큐 `run_jobs_v04_queue_idx`. v03 인덱스·규칙 변경 없음. 입력 불변·버전 고정·성공 전 판정 필수·lease 검사는 v03과 같음. 한 manifest에 v03 작업과 v04 작업이 함께 있는 것은 막지 않음(버전 전환 구간)
- `run_process_decisions.config_version_id` NULL 허용, 구성 NULL은 v04 작업의 판정만: 작업 없는 행은 CHECK `decisions_config_required`, 작업 있는 INSERT는 `erd_v03_validate_result`의 판정 구성 = 작업 구성 비교(v04 판정에 구성을 넣어도 거부), legacy 행의 구성을 지우거나 작업에 붙이는 UPDATE는 트리거 `decision_config_kept`가 거부(v03·v04 작업 모두, 다른 열 UPDATE는 허용). 4열 UNIQUE가 NULL을 막지 못하는 빈틈은 부분 UNIQUE `run_process_decisions_no_config_idx`
- explain: 판정 구성이 NULL이면 작업 `input_config_version_id`·`run_llm_results.config_version_id`도 NULL, 그 밖에는 지금처럼 일치 요구. explain 작업 자체는 v03
- `ops_pipeline_measurements.config_version_id`: detector 단계만 NULL 허용(CHECK `ops_pipeline_measurements_config_check`), feature 단계는 필수. 측정 행에 작업 참조가 없어 v04 작업까지 좁히지 못함. `run_decision_interims`는 구성 열이 없어 `validate_decision_interim`의 버전 조건만 고침. `data_feature_materializations`·`ops_input_controls` 변경 없음
- 재정의 함수 3개(`erd_v03_validate_job`·`erd_v03_validate_result`·`validate_decision_interim`): 원본 최신판 기준으로 표시한 줄만 변경, `search_path` 설정·SECURITY INVOKER·소유자 유지. SECURITY DEFINER 함수 2개는 버전 조건이 없어 변경 없음
- 룰 세트: `content_hash_version smallint`(016: 1 또는 2, 020 이후 1·2·3, 열 기본값은 계속 2). 기존 행 1(fast default), 열 기본값 2 → 열을 생략한 저장도 2. 시스템 행 r1 = 하드룰 4개 켬·내장 기본 severity·소프트룰 없음(`contracts/detection-decision.md` 4.3 값), `content_hash_version = 2`, `content_hash = de6a2ad8d0dcee189f36852a279263d3408f826632b95b4f14a97406b801bed0`(`contracts/rule-set.md` v2 정규 객체), `saved_by_actor_id = system`, `note` NULL, `changes`는 새 규칙 2개의 `hard_enabled`
- r1 삽입 조건: 최대 리비전이 0일 때만. 저장된 리비전이 있으면 `RAISE EXCEPTION`으로 016 전체가 되돌아감(메시지에 사유·조치). 이 검사가 파일 맨 앞쪽이라 큰 표를 잠그기 전에 멈춘다. API 공통 잠금 81739는 잡지 않음(표 ACCESS EXCLUSIVE를 쥔 채 기다리면 API 저장과 교착, 표 잠금만으로 직렬화됨)
- 뷰 `ops_operational_state`: 합의 열 6개의 이름·순서·형식은 그대로, 맨 뒤에 `rule_set_content_hash_version` 추가(`contracts/model-version.md` 일관 읽기 요구사항이 contentHashVersion을 함께 읽음). 뷰 권한·`security_invoker` 유지
- 잠금(COMMIT까지): `ops_rule_set_revisions`·`run_jobs`·`run_process_decisions`·`run_llm_results`·`ops_pipeline_measurements`와 뷰 `ops_operational_state`에 ACCESS EXCLUSIVE. 표 재작성 없음. CHECK 검증과 부분 인덱스 빌드로 heap을 읽음(`run_jobs` 4회, `run_process_decisions` 2회, `run_llm_results`·`ops_pipeline_measurements` 1회, TOAST 값은 읽지 않음). 012와 같이 작업자를 멈추고 적용(켠 채 적용하면 표 잠금 순서 차이로 교착 가능). 참고 실측: 세 표 각 100만 행(판정 표 1.9GB, 로컬 embedded PostgreSQL 17)에서 016 전체 3.7초. NOT VALID 미사용: 같은 트랜잭션의 CREATE INDEX가 어차피 heap을 읽고 CONCURRENTLY는 트랜잭션 밖에서만 가능하므로 원자성만 잃음
- 적용 순서: 016은 v03 쓰기 코드와 호환. v04 행은 Detector #112 배포 뒤에 생기므로 016 → 코드 배포 순서. worker·API가 v04를 읽는 변경은 각 Issue 범위
- 시험(`erd-v03-detect-v04.mjs`, `erd-v03.mjs`가 호출): `erd119_fresh`(r1 행·hash 재계산·뷰·기본값·불변, v04 작업·판정·중간 판정·explain 전체 흐름과 거부 조건, 실제 LOGIN 역할), `erd119_upgrade`(001~015 + v03·legacy 행 → 016, 행 보존, 변경 목록 외 카탈로그 불변, 함수 본문은 표시한 줄만 변경, 진행 중 v03 작업 완료), `erd119_saved`·`erd119_empty`(r1 조건 실패 → 무변경), `erd119_rollback`(COMMIT 직전 오류 → 무변경 → 재적용), `erd119_encoding`(비UTF8 세션 문장 단위 적용)

## 017 소프트룰 임계값 일괄 추천 작업 종류 (#124)

- 파일 `017_recommend_thresholds.sql`, 016 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표·함수 소유자로 실행. 새 표 없음 → `database-roles.sql` 변경 없음. 자동 down migration 없음(enum 값은 지울 수 없음)
- 왜: Trainer #121의 `recommend_thresholds` 작업을 DB가 받게 한다(`contracts/llm-recommendation-job.md` 마이그레이션·코드 영향 절, `contracts/trainer-service.md` 계약 누락 1·2번, `contracts/queues.md`). 이 파일만으로 작업이 생기지는 않음. API 접수·워커 처리는 각 Issue 범위
- enum 커밋 분리(010·011 방식): `job_kind`에 값을 트랜잭션 밖 `ADD VALUE IF NOT EXISTS`로 먼저 추가, 같은 파일 안에서는 `kind::text` 비교로만 사용. `psql -f` 실행은 BEGIN..COMMIT 중간 오류 시 구조 변경 전체 ROLLBACK, 커밋된 enum 값만 남고 같은 파일 재실행 가능. 한 문자열 실행(`prepare-runtime-db.py`)은 BEGIN이 enum 추가를 흡수해 실패 시 enum까지 되돌아감
- enum만 남은 구간: 이전 CHECK의 legacy 분기는 이 값을 막지 않는다. 쓰기 코드가 없어 행이 생기지 않고, 생기면 재실행의 CHECK 검증이 실패해 전체 ROLLBACK(반쪽 적용 없음). v03 작업은 이전 CHECK가 거부
- `run_jobs_v03_contract`(016본 기준): `llm_recommendation` 분기에 추가(큐 `llm_recommendation`·user producer·admin·`llm_spec` 필수), legacy 분기 사용 금지. v04·explain 분기 변경 없음
- `erd_v03_validate_job()`(016본 기준): `llm_spec` 필수 목록·LLM 완료 결과 필수 목록 2줄만 변경, `search_path` 설정·SECURITY INVOKER·소유자 유지. `erd_v03_validate_result`는 종류 목록이 없어 재정의하지 않음
- `run_llm_results`: `run_llm_results_kind_check`(011본 기준, 016이 재정의하지 않음)·`run_llm_results_check`(016본 기준)의 `decision_id IS NULL` 분기에 추가
- 구성·입력: 입력은 룰 세트 리비전이고 `input_spec`에 고정, 구성은 없다. `input_config_version_id`·`run_llm_results.config_version_id`는 규약상 NULL이고 DB가 작업 생성 때 강제하지는 않음(API가 넣지 않아야 함). `llm_recommendation` 분기와 `decision_id IS NULL` 분기에 구성 조건이 원래 없고 `erd_v03_validate_result`는 결과 구성 = 작업 구성만 비교하므로 조건을 풀거나 더하지 않음. 리비전을 검사할 열도 만들지 않음(입력 고정은 API, 값 대조는 Trainer)
- v03 작업이라 `input_snapshot_id`는 규약상 NULL이고 스냅샷은 `run_job_snapshots`에만 기록. 이 NULL도 작업 생성 때 DB가 강제하지 않고, 생성 뒤 변경만 막음. 실패 작업은 `run_llm_results` 없이 `error_code`·`result_summary`만 남길 수 있음(결과 필수는 `succeeded` 전환에만 적용). `llm_spec` 값(`soft-rule-threshold-v1` 등)은 DB 제약으로 고정하지 않음
- 잠금(COMMIT까지): `run_jobs`·`run_llm_results`에 ACCESS EXCLUSIVE. 표 재작성 없음. CHECK 검증으로 heap을 읽음(`run_jobs` 1회, `run_llm_results` 2회, TOAST 값은 읽지 않음). 012·016과 같이 Detector·Collector·Trainer·LLM·API 작업자를 멈추고 적용. 새 조건은 이전보다 넓어 기존 행이 실패할 수 없음
- 적용 순서: 017 → API·워커 배포. 기존 코드는 새 값을 쓰지 않아 017 적용 뒤에도 수정 없이 동작
- 시험(`erd-v03-recommend-thresholds.mjs`, `erd-v03.mjs`가 호출): `erd124_fresh`(작업 생성 조건, `llm_spec`, 결과·완료, 실패 요약, 스냅샷, 재시도, 기존 kind·v04·explain 유지, 실제 LOGIN 역할), `erd124_upgrade`(001~016 + 기존 행 → 017 문장 단위, 행 보존, 제약은 016 정의에서 새 값만 다름, 함수 본문은 2줄만 다름, 진행 중 작업 완료), `erd124_rollback`(enum 커밋 뒤 COMMIT 직전 오류, enum만 남은 구간, 재실행), `erd124_single`(한 문자열 실패·성공), `erd124_shadow`(임시 표 가리기)

## 018 판정 표 관측별 최신 조회 인덱스 (#129)

- 파일 `018_decision_observation_index.sql`, 017 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 표 소유자로 실행. 문장 1개(`BEGIN` 없음). 표·열·제약·트리거·함수·권한 변경 없음 → `database-roles.sql` 변경 없음. 자동 down migration 없음, 되돌리기는 `DROP INDEX run_process_decisions_observation_latest_idx`
- 왜: 운영 리포트 `SCOPED_OBSERVATIONS`의 LATERAL이 관측마다 최신 판정 1행을 찾는데 `process_observation_id`로 시작하는 인덱스가 없었다. 관측마다 Seq Scan(합성 데이터 `evidence` 53B·1.9kB) 또는 4열 UNIQUE(`execution_id`로 시작) 전체 탐색(712B)이 돌았고 읽은 버퍼가 관측 수의 제곱으로 늘었다(3,000건 19.7만 → 12,000건 314만 → 30,000건 1,847만). 적용 뒤 두 형태 모두 이 인덱스 탐색으로 바뀐다
- 쓰는 조회: `OPERATIONAL_SQL`·`COHORT_SUMMARY_SQL`·`DETECTOR_COST_SQL`(LATERAL `latest`), 구성 있는 경로·구성 NULL(v04) 경로 모두. `COVERAGE_SQL`·`BACKLOG_SQL`·`BACKLOG_AT_CUTOFF_SQL`은 `latest`를 읽지 않아 영향 없음. `detector/runner.py`의 기존 큐 경로(`process_observation_id`+`config_version_id` 존재 확인)도 이 인덱스를 쓴다. 판정 표를 `process_observation_id`로 찾는 다른 서버 조회는 없음(나머지는 `id`·`job_id`로 찾거나 관측 쪽으로 조인)
- 형태 `(process_observation_id, decided_at DESC, id DESC)`: `ORDER BY d.decided_at DESC,d.id DESC LIMIT 1`과 순서가 같아 관측당 판정이 적은 일반 분포에서는 두 경로 모두 정렬이 없다. 비교(판정 14,400행, `COHORT_SUMMARY_SQL` 중앙값): 이 형태 구성 39ms·NULL 31ms·840kB, 오름차순 41·36ms·840kB(역방향 탐색), `config_version_id` 둘째 열 33·45ms·1,048kB(NULL 경로에 정렬 추가), `process_observation_id` 단독 43·47ms·416kB(두 경로 정렬). 관측당 판정 50건이고 대상 구성이 가장 오래된 극단 분포에서만 둘째 열 형태가 구성 경로를 앞선다(5ms 대 32ms). 그 분포에서도 이 형태는 관측 1,000건에 32ms라 NULL 경로 정렬을 없애는 쪽을 택함
- 적용 방식: 일반 `CREATE INDEX`. 문장 1개라 실패하면 아무것도 남지 않고 같은 파일을 다시 실행하면 된다. 성공 뒤 재실행은 42P07로 멈추고 아무것도 바뀌지 않음(`IF NOT EXISTS`를 쓰지 않아 같은 이름의 다른 정의를 넘기지 않음)
- 잠금: 생성 동안 `run_process_decisions`에 SHARE. 읽기는 막지 않고 쓰기는 기다렸다 진행된다. 열린 쓰기 트랜잭션이 있으면 생성이 먼저 기다리고 그 뒤 쓰기도 줄을 선다. 작업자를 멈추고 적용하면 이 대기가 없고, 멈추지 않아도 판정 INSERT가 거부되지는 않음(서버 코드에 `lock_timeout` 없음)
- 쓰기 대기 측정(embedded PostgreSQL 17.10, 기본 설정, OS 캐시가 찬 상태, 20ms 간격 INSERT·SELECT 동시 실행): 판정 120만 행(heap 2.6GB) 일반 0.9초 동안 INSERT 대기 0.9초, `CONCURRENTLY` 1.1~1.2초 동안 INSERT 최대 7ms. 360만 행(heap 7.9GB) 일반 2.7초 동안 대기 2.6초, `CONCURRENTLY` 3.2~3.4초 동안 최대 7ms. 읽기는 모두 최대 2ms. 디스크에서 heap을 처음 읽는 서버 시간은 재지 않음
- `CONCURRENTLY`를 쓰지 않는 근거: 문장 1개면 `psql -f`·한 문자열 실행 모두 가능하지만(`BEGIN`이나 `SET`을 같은 문자열에 넣으면 25001) 실패·취소 때 INVALID 인덱스가 남고 `IF NOT EXISTS`가 그것을 건너뛰어 적용된 것처럼 보인다. 위 대기 시간에서 그 위험을 질 이득이 작다. 쓰기를 몇 초도 멈출 수 없는 표 크기가 되면 이 판단을 다시 본다
- 비용: 인덱스 크기 판정 120만 행 67MB, 360만 행 202MB(행당 약 59바이트). 판정 INSERT는 트리거를 뺀 단건 커밋 중앙값 0.25~0.40ms → 0.31~0.53ms, 한 트랜잭션 2만 행 행당 0.23~0.28ms → 0.24~0.38ms
- 남은 병목(이 파일 범위 밖): `OPERATIONAL_SQL`의 `pairs`가 날짜마다 `cohort`를 다시 읽어 판정 쪽이 빨라진 뒤 전체 시간을 차지한다. 또 `scoped`에 기간 하한이 없어 LATERAL이 원천의 전체 이력만큼 돈다. 관측 100만 건에서 LATERAL 약 15초, 보고 전체 130~347초
- 시험(`erd-v03-decision-index.mjs`, `erd-v03.mjs`가 호출): `erd129_fresh`(001~018 한 문자열, 인덱스 정의·유효, DBML, `prepare-runtime-db.py` 목록), `erd129_upgrade`(001~017 + 기존 행 → 018 문장 단위, 행 보존, 카탈로그는 인덱스 1개만 추가, 소스의 실제 보고 SQL이 두 경로에서 이 인덱스로만 판정을 읽음, 동점·구성 필터·기준 시각 결과, 인덱스 없는 스캔과 같은 결과, 재실행 42P07), `erd129_lock`(열린 쓰기 때문에 55P03으로 실패해도 아무것도 안 남고 이후 적용)

## 019 장애 묶음

- 실험 기능(층 C 거래 간 사건 묶음)
- 파일 `019_incidents.sql`, 018 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행 뒤 DBA가 `scripts/database-roles.sql` 재적용. 새 표 4개·트리거 4개만, 새 함수 없음, 단일 BEGIN..COMMIT. FK 생성이 `ops_sources`·`run_process_decisions`에 짧은 SHARE ROW EXCLUSIVE(014와 같음). 자동 down migration 없음
- 표(층 C, Detector 쓰기·API 읽기): `run_incident_runs`(Detector 주기 실행 1회 = 1행, 실행 트랜잭션 끝에 INSERT, 읽은 커서 범위·건수 3종), `run_incidents`(묶음, `criteria` 객체·`criteria_version`·`group_key`·`time_basis`·`opened_at` = 첫 구성원 발생 시각, `continues_from` = 1시간 구간으로 끊긴 앞 구간), `run_incident_members`(`decision_id` PK = 판정 1건은 최대 1개 묶음, `occurred_at`·`occurred_basis`), `run_incident_events`(병합 기록 `merged_into`만, identity id)
- 시각 기준: `time_basis`·`occurred_basis` 모두 `process_time_raw`·`decided_at`·`source_log_occurred_at` 중 하나(CHECK). 원문 시각 해석에 실패한 구성원은 `decided_at`으로 대체되므로 구성원 `occurred_basis`가 묶음 `time_basis`와 다를 수 있음. 현재 코드는 `run_process_decisions.source_log_occurred_at`·`decision_committed_at`을 쓰지 않음(값 NULL), `run_process_decisions`에 `decided_at`으로 시작하는 인덱스도 없음
- 멱등·무결성: `run_incidents` UNIQUE `(source_id, criteria_version, group_key, opened_at)`, 구성원은 `INSERT … ON CONFLICT (decision_id) DO NOTHING`, `merged_into`는 대상 필수·자기 대상 금지·묶음당 1건(partial UNIQUE), `continues_from`은 자기 참조 금지·앞 구간당 다음 구간 1개(partial UNIQUE, 사슬 일직선). 이어짐은 이벤트 종류로 두지 않음. 커서 `cursor_from ≥ 0`·`cursor_to ≥ cursor_from`, 건수 ≥ 0, `finished_at ≥ started_at`. 끝 시각·구성원 수·해제 여부는 저장하지 않고 읽을 때 계산
- 실행 FK: 묶음·구성원·이벤트의 `added_by_run` → `run_incident_runs(id)`는 DEFERRABLE INITIALLY DEFERRED. 실행 행을 같은 트랜잭션 마지막에 넣고, 끝내 없으면 COMMIT이 23503으로 실패해 전체가 되돌아감
- 최근 목록 인덱스: `run_incidents_recent_idx (source_id, opened_at DESC, id DESC)`. WebUI 장애 목록·상세가 원천별 `ORDER BY opened_at DESC, id DESC`(id = 페이지 넘김 안정 순서)로 읽기 위함, 4열 UNIQUE만으로는 이 순서를 못 탐
- 커서: `cursor_from`·`cursor_to` = 읽은 `run_decision_events.id` 범위. 이 id는 006·012 트리거가 `pg_advisory_xact_lock(81736, 0)` 아래에서 붙이는 GENERATED ALWAYS identity라 id 순서 = 커밋 순서. 마지막 커서 = `(source_id, criteria_version)`별 `cursor_to` 최대 행(인덱스 `run_incident_runs_cursor_idx`). 범위 끝 포함 여부는 Detector 계약에서 정함
- 직렬화: Detector가 묶음 계산 트랜잭션에서 `pg_try_advisory_xact_lock(81741, 0)`(못 잡으면 그 회차 건너뜀). "병합 대상이 이미 병합되지 않았어야 함"은 이 잠금 안에서 Detector가 확인(트리거 없음)
- 읽기 기준(DB 강제 아님): 실행별 최신 판정 = `PARTITION BY d.execution_id ORDER BY o.revision_no DESC NULLS LAST, d.decided_at DESC, d.event_sequence DESC`. 해제·구성원 수는 읽을 때 계산
- append-only: 4표 모두 `BEFORE UPDATE OR DELETE OR TRUNCATE FOR EACH STATEMENT` 트리거(004 `erd_v03_immutable()` 재사용, 015 방식. 006 행 단위보다 TRUNCATE·0행 문장까지 넓게 거부) + 권한 SELECT·INSERT만. 신뢰 경계는 015와 같음
- 권한: `powercode_detector` 4표 SELECT·INSERT + `run_incident_events_id_seq` USAGE(006 `run_decision_events`와 같은 형식), `powercode_api` 4표 SELECT. Trainer·LLM·Collector 권한 없음
- advisory lock 분류(코드 grep 확인): 81732 Trainer 카탈로그, 81733 파이프라인 관측, 81734 API `jobRoutes.ts` LLM 재사용 키, 81735 Collector 원천·창 감시와 API `preparedDatasetRoutes.ts` 준비 CSV, 81736 판정 이벤트, 81737 데모 실행, 81738 Collector input_store, 81739 룰 세트·활성 집합·정책 저장, 81740 Detector manifest, 81741 장애 묶음(019), 81742 관계 규칙 재판정 탐색(024 [제안]), 94732 API ruleRepository
- 시험(`erd-v03-incidents.mjs`, `erd-v03.mjs`가 호출): `erd019`(001~018 + 019 한 문자열, 제약·partial UNIQUE·지연 FK·트리거 카탈로그, 역할 5개 권한 정확 일치, 실제 LOGIN Detector 쓰기·읽기와 API 읽기·INSERT 거부, 실행 행 마지막 커밋 성공·실행 행 없음 COMMIT 23503, 중복 판정 ON CONFLICT 0행·일반 INSERT 23505, 병합 2회·자기 대상·대상 없음, 이어짐 중복·자기 참조, 시각 기준 값, 커서 역순, 비객체 기준, 런타임 역할 UPDATE·DELETE 42501, 소유자 UPDATE·DELETE·TRUNCATE P0001). 함께 바꾼 기존 시험: 015 시점에 역할 스크립트를 적용하는 `erd110_fresh`는 019 GRANT 블록을 빼고 적용, 017·018 문장 단위 DB(`erd124_upgrade`·`erd124_rollback`·`erd124_single`·`erd129_upgrade`)는 parity 전에 이후 파일까지 적용, `prepare-runtime-db.py` 목록에 019 추가

## 020 룰 세트 hash 버전 3 허용

- 파일 `020_rule_set_hash_v3.sql`, 019 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 표 소유자로 실행. 문장 1개(`ALTER TABLE` 안의 DROP·ADD가 함께 성공·실패, 018과 같이 BEGIN..COMMIT 없음), 새 표·권한 변경 없음, 자동 down migration 없음. 재실행해도 결과 같음
- 왜: hash v3은 관계 규칙과 소프트룰 범위를 정규 객체에 넣는다. 정규 객체·직렬화 규칙은 API·Detector 계약 범위이고 DB는 허용 버전 값만 둔다
- 변경: CHECK `ops_rule_set_revisions_content_hash_version_check`를 같은 이름으로 `(1,2)` → `(1,2,3)`. 열 DEFAULT 2 유지(API `ruleSetStore.ts`가 버전을 명시해 저장), 기존 행(r0 = 1, r1 = 2) 변경 없음
- 잠금: 문장 동안 `ops_rule_set_revisions`에 ACCESS EXCLUSIVE, CHECK 검증으로 리비전 행을 한 번 읽음(작은 표). 새 조건이 이전보다 넓어 검증 실패 없음
- 배포 순서: DB 020 → Detector v3 → API v3. 020만 적용한 상태에서 기존 코드는 2를 그대로 씀. 되돌림은 v3 행이 생기기 전에만 가능(행 불변)
- 시험(`erd-v03-rule-hash-v3.mjs`, `erd-v03.mjs`가 호출): `erd020`(020 전 3 거부, 020 한 문자열, 카탈로그는 이 CHECK 정의만 변경·이름 유지·검증됨, DEFAULT 2, r0·r1 행 불변, 3 허용·4·0 거부, 열 생략 저장 2, 재실행 같은 카탈로그, DBML parity). 함께 바꾼 기존 시험: 모든 파일을 적용하는 `erd119_fresh`의 범위 밖 값은 4로, 016의 3 거부는 016 직후인 `erd119_upgrade`에서 확인, `prepare-runtime-db.py` 목록에 020 추가

## 021 규칙 관계 추천 후보 작업 종류

- 파일 `021_recommend_relations.sql`, 020 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행. 새 표·시퀀스·함수 없음 → `database-roles.sql` 변경 없음(API는 `run_jobs` INSERT·UPDATE와 `training_jobs` INSERT, Trainer는 `run_jobs`·`run_job_attempts` INSERT·UPDATE와 `training_jobs` SELECT·UPDATE를 이미 가짐). 자동 down migration 없음(enum 값은 지울 수 없음)
- 왜: WebUI의 규칙 관계 추천(층 B) 후보 작업 요청을 Trainer가 training 큐로 처리하고 결과는 `run_jobs.result_summary`에만 쓴다. 새 표·`run_llm_results`·`llm_spec` 없음
- enum 커밋 분리(017과 같음): `job_kind`에 `recommend_relations`를 트랜잭션 밖 `ADD VALUE IF NOT EXISTS`로 먼저 추가, 파일 안에서는 `kind::text` 비교만. `psql -f` 실패 시 CHECK만 ROLLBACK·enum 값은 남고 재실행 가능, 한 문자열 실행은 실패 시 enum까지 되돌아감. enum만 남은 구간에는 017 CHECK의 legacy 분기가 이 값을 막지 않음(쓰기 코드 없음, 행이 생기면 재실행 CHECK 검증이 실패해 전체 ROLLBACK)
- `run_jobs_v03_contract`(017본 기준, 018~020은 `run_jobs`를 건드리지 않음): v03 training 분기 목록(`queue='training'`·user producer·admin·`llm_spec` NULL·manifest/decision NULL)과 legacy 금지 목록에 추가 → v03 training 전용. detect·`llm_recommendation`·explain·v04 분기 변경 없음
- 바꾸지 않는 것: `erd_v03_validate_job`(kind 목록은 LLM·detect·explain뿐 → 이 kind는 producer 일치·입력 불변·`succeeded` 때 현재 미만료 시도만 적용), `run_llm_results_kind_check`·`run_llm_results_check`(결과 표를 쓰지 않으므로 목록에 넣지 않음 → 이 kind 결과 행은 거부), 작업 실패 트리거 3개(010 `import_csv`·011 `evaluation_set`·012 `detect` 전용)와 011 `compare`·`evaluation_set` 계보 검사(자기 kind만 처리 → dead 큐 정리의 `failed` 전환·`succeeded` 전환을 막지 않음), `powercode_queue.training_jobs`(payload 객체 CHECK만, kind 조건 없음)
- 잠금(COMMIT까지): `run_jobs`에 ACCESS EXCLUSIVE, 표 재작성 없음, 새 CHECK 검증으로 heap 1회 읽음. 017과 같이 작업자를 멈추고 적용. 새 조건은 이전보다 넓고 legacy 금지 값은 021 전에 없던 값이라 기존 행이 실패할 수 없음 → NOT VALID 미사용
- 되돌리기: CHECK는 017본 정의로 다시 만들 수 있으나 `kind = recommend_relations` 행이 없을 때만. 재실행은 같은 카탈로그
- 배포 순서: DB 021 → Trainer(training claim 목록과 admin 확인 목록에 이 kind 추가) → API 접수 활성화. API가 먼저 접수하면 작업이 큐에 남은 채 처리되지 않음. 021만 적용한 상태에서 기존 코드는 새 값을 쓰지 않아 그대로 동작
- 시험(`erd-v03-recommend-relations.mjs`, `erd-v03.mjs`가 호출): `erd021`(021 전 22P02, 021 문장 단위 적용·ASCII 문장, 카탈로그는 enum 값 1개와 `run_jobs_v03_contract`의 두 목록만 017 정의와 다름·검증됨, 한 문자열 재실행 같은 카탈로그, DBML parity, 실제 LOGIN API 작업·큐 메시지 생성과 다른 큐·`llm_spec`·일반 사용자·system producer·producer 불일치·멱등키 없음·manifest·decision·legacy·v04·중복 거부, Trainer claim·완료·`result_summary`·큐 확인, `run_llm_results` 행 거부, 만료 lease 완료 거부, `pg_queue.reconcile_dead_jobs` SQL로 dead 큐 정리, 기존 kind 대조). 기존 kind 전체 흐름은 021까지 적용하는 기존 DB들(`erd_fresh`·`erd124_fresh` 등)이 그대로 확인. 함께 바꾼 기존 시험: `prepare-runtime-db.py` 목록에 021 추가(최종 상태 제약 문자열을 비교하는 기존 시험은 없어 옮긴 검사 없음)

## 022 사용자별 설정·알림 읽음 상태

- 파일 `022_user_settings_notifications.sql`, 021 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행 뒤 DBA가 `scripts/database-roles.sql` 재적용. 새 표 4개만(함수·트리거·시퀀스·enum 없음), 단일 BEGIN..COMMIT. FK가 없어 기존 표를 잠그지 않고 작업자를 멈출 필요 없음. 자동 down migration 없음(되돌리기 = 새 표 4개 DROP)
- 표(API만 읽고 씀): `ops_user_notification_settings`(알림 설정 3값, 사용자당 1행, 행 없음 = 기본값), `ops_user_widget_layouts`(화면별 위젯 배치 문서, 지금은 `operations_overview`만, 객체·16KB 이하), `ops_user_notification_cursors`(모두 읽음 순번), `ops_user_notification_reads`(항목별 읽음 순번, 키 `incident:`·`job:`·`decision:` + 소문자 UUID)
- 사용자 키 `actor_id` = 인증 계정 id 문자열(1~128자, 014 `run_decision_reads.actor_id`와 같음). 계정은 API 파일 저장소에 있어 FK 없음. 계정을 지워도 행은 남음(같은 id가 다시 생기지 않는 한 읽히지 않음, 정리는 운영 절차)
- 알림 항목은 저장하지 않는다: API가 `run_decision_events`·판정·중간 기록·장애 묶음에서 읽을 때 만든다(`contracts/notifications.md`). 읽음 순번 = `run_decision_events.id`(006·012 identity, id 순서 = 커밋 순서)
- 권한: `powercode_api` 설정·배치·순번 표 SELECT·INSERT·UPDATE, 항목 읽음 표 SELECT·INSERT·UPDATE·DELETE(모두 읽음 순번 이하·조회 범위 밖 행 정리). Detector·Collector·Trainer·LLM 권한 없음
- 시험(`erd-v03-user-settings.mjs`, `erd-v03.mjs`가 호출): `erd022`(001~022 한 문자열, PK·FK 없음·트리거 없음 카탈로그, DBML parity, 역할 5개 권한 정확 일치, 실제 LOGIN API upsert·GREATEST 순번·DELETE 정리와 TRUNCATE·설정 DELETE 42501, 다른 역할 읽기·쓰기 42501, CHECK 경계 12종). 2026-11-13 native PostgreSQL 17.10(embedded) 1180 checks 통과(임시 폴더 D 드라이브). API 경로는 `backend/tests/notifications.integration.mjs`(CI 단계 "User settings and notifications API under runtime role", `powercode_api` 역할 연결)
- 함께 바꾼 기존 시험: `prepare-runtime-db.py` 목록에 022·`qa_notifications_service` 추가. `erd110_fresh`(015 시점 역할 스크립트)는 019 블록부터 `#78-Q10` 앞까지를 빼므로 022 블록도 함께 빠짐(변경 없음)

## 023 프로토콜 알림 처리 기록

- 파일 `023_protocol_alert_actions.sql`, 022 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행. 역할 스크립트 재적용은 023·024·025를 모두 적용한 뒤 한 번(024 절 배포 순서). 새 표 2개만(함수·트리거·시퀀스·enum 없음, 001~022 정의·022 `item_key` CHECK 그대로), 단일 BEGIN..COMMIT. FK가 없어 기존 표를 잠그지 않고 작업자를 멈출 필요 없음. 자동 down migration 없음(되돌리기 = 새 표 2개 DROP)
- 표(API만 읽고 씀): `ops_protocol_alert_keeps`(알림 [이상 유지] 기록, 시스템 전역, 묶음 키 (인터페이스, pathKey)당 1행, 추가만 — 키 `protocol:` + sha256 16진 64자, `path_key` 원래 값·`''` = 경로 없는 미등록 인터페이스 알림, `kept_through_seq` = 누른 시점 알림 순번), `ops_user_protocol_alert_reads`(프로토콜 알림 항목별 읽음, 022 항목 읽음과 같은 규칙)
- 알림 항목은 저장하지 않는다: API가 최종 판정 `evidence.protocolReview`(Detector `protocol-rules.md` 5.3)에서 읽을 때 만든다(`contracts/protocol-settings-ui.md` 5절). Detector는 이 표들을 읽지 않는다(판정은 이상 그대로)
- 권한: `powercode_api` keeps SELECT·INSERT(수정·삭제 없음), reads SELECT·INSERT·UPDATE·DELETE. Detector·Collector·Trainer·LLM 권한 없음
- 시험(`erd-v03-protocol-alerts.mjs`, `erd-v03.mjs`가 호출): `erd023`(migration 전체 적용 뒤 역할 스크립트, PK·FK 없음·트리거 없음 카탈로그, DBML parity, 역할 5개 권한 정확 일치, 실제 LOGIN API 기록 INSERT·ON CONFLICT DO NOTHING·UPDATE/DELETE 42501·읽음 upsert·정리, 다른 역할 42501, CHECK 경계). API 경로는 `backend/tests/notifications.integration.mjs`
- 함께 바꾼 기존 시험: `prepare-runtime-db.py` 목록에 023 추가

## 024 관계 규칙 재판정 탐색 커서 [제안, DB 세션 확정 대기]

- 실험 기능(룰 세트 v3 7단계 재판정 탐색, `contracts/relation-rules.md` 6.5). Detector 세션이 제안한 파일이고 번호·표 형태·권한은 DB 세션이 확정한다. 번호 022는 사용자별 설정(위 절), 023은 WebUI 브랜치(프로토콜 알림 조치)가 쓰므로 024를 씀
- 파일 `024_relation_recheck_runs.sql`, 021 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`, 022·023과 서로 의존 없어 023보다 먼저 적용해도 됨), 기존 표 소유자로 실행. 새 표 1개·트리거 1개만, 새 함수 없음(004 `erd_v03_immutable()` 재사용), 단일 BEGIN..COMMIT. FK 생성이 `ops_sources`에 짧은 SHARE ROW EXCLUSIVE. 자동 down migration 없음(표가 비어 있으면 DROP TABLE)
- 표 `run_recheck_runs`(Detector 쓰기, 다른 역할 접근 없음): 탐색 1회(원본 1개) = 1행, 실행 트랜잭션 끝에 INSERT. 거래 관측 커서 `transaction_cursor_at`·`transaction_cursor_id`, 판정 이벤트 커서 `event_cursor`(≥ 0), 건수 3종 ≥ 0, `finished_at ≥ started_at`. 마지막 커서 = 원본별 `seq` 최대 행(인덱스 `run_recheck_runs_cursor_idx`), `seq`는 GENERATED ALWAYS identity이고 advisory lock(81742, 0) 아래에서만 INSERT라 순서 = 커밋 순서
- append-only: `BEFORE UPDATE OR DELETE OR TRUNCATE FOR EACH STATEMENT` 트리거(019와 같음) + 권한 SELECT·INSERT만. 1회 보장은 이 표가 아니라 `run_input_manifests UNIQUE(source_id, content_hash)`(004)라 표를 잃어도 재판정이 두 번 생기지 않음(커서가 처음부터 다시 읽을 뿐)
- 권한: `powercode_detector` SELECT·INSERT + `run_recheck_runs_seq_seq` USAGE
- **배포 순서: `scripts/database-roles.sql`은 023·024·025를 모두 전제한다**(각 GRANT 줄이 그 표를 가리킴). 하나라도 없이 역할 스크립트를 다시 적용하면 그 GRANT에서 실패하므로 DB 023·024·025 적용(새 표만 추가라 기존 코드 영향 없음) → 역할 스크립트 재적용 → Detector v3·`recheck` 서비스(Compose profile `relation-recheck`) → API v3 저장·WebUI 순. 024를 늦추려면 역할 스크립트의 024 블록을 빼고 적용
- advisory lock 81742: 재판정 탐색(024 표 쓰기 트랜잭션). 같은 트랜잭션에서 81740(manifest)만 이어서 잡고 81741과 함께 잡지 않음
- DBML: `schema.dbml`에 `run_recheck_runs`와 `ops_sources` FK를 반영(ERD 시험 parity가 모든 migration 파일을 적용한 뒤 비교하므로). ERD node 시험 파일(`erd-v03-*.mjs`)에 024 전용 검사는 아직 없음 — DB 세션 확정 때 추가

## 025 응답 코드 사전

- 파일 `025_response_codes.sql`, 023 적용 뒤 한 번 실행(024 Detector 재판정 커서와 의존이 없어 024와 순서 무관)(`psql -v ON_ERROR_STOP=1 -f`), 기존 표 소유자로 실행. 역할 스크립트 재적용은 023·024·025를 모두 적용한 뒤 한 번(024 절 배포 순서). 새 표 2개·색인 1개만(함수·트리거·enum 없음, 001~024 정의 그대로, 024 표와 의존 없음), 단일 BEGIN..COMMIT. FK는 새 표끼리만(기록 → 사전)이라 기존 표를 잠그지 않고 작업자를 멈출 필요 없음. 자동 down migration 없음(되돌리기 = 새 표 2개 DROP). **API(이 migration을 읽는 이상 분석 목록·상세 포함)보다 먼저 적용**한다 — 표가 없으면 이상 분석 조회가 503이 된다
- 역할 스크립트 재적용 전 확인: `scripts/database-roles.sql`에 023·024·025 표 GRANT가 모두 들어 있어 하나라도 없으면 실패한다. `SELECT to_regclass('public.ops_protocol_alert_keeps'), to_regclass('public.run_recheck_runs'), to_regclass('public.ops_response_codes')`가 모두 NULL이 아닌지 먼저 본다(024를 늦추려면 024 절대로 역할 스크립트의 024 블록을 빼고 적용)
- 표(API만 읽고 씀): `ops_response_codes`(코드당 1행, `code` 인쇄 가능한 ASCII 1~64자, 메시지 1~500자, `origin` import|edited, `revision` = 화면 편집 낙관적 잠금, 삭제 없음), `ops_response_code_changes`(메시지 변경 기록, 추가만, GENERATED ALWAYS identity, 기록 → 사전 FK). 코드별 위험도는 이 표가 아니라 룰 세트 `hard:transaction-failed`(contracts/response-codes-ui.md 1절). Detector는 읽지 않는다
- 권한: `powercode_api` 사전 SELECT·INSERT·UPDATE(DELETE 없음), 기록 SELECT·INSERT(수정·삭제 없음, identity라 시퀀스 권한 불필요). Detector·Collector·Trainer·LLM 권한 없음
- **기본값 넣기(서버, 운영자)**: 사전 값은 고객 자료라 저장소·이미지에 없다. migration 025 적용 뒤 원본 파일(.xlsx 또는 UTF-8 .csv)을 저장소 밖에서 API 컨테이너(compose 서비스 `api`, 역할 `API_DATABASE_URL`)로 복사해 API 역할로 실행한다. 출력은 건수·행 번호·사유 코드만(값 없음)
  ```sh
  docker compose cp <저장소 밖 경로>/<사전 파일>.xlsx api:/tmp/response-codes.xlsx
  docker compose exec api node dist/scripts/importResponseCodes.js /tmp/response-codes.xlsx --dry-run   # 검사·건수만(ROLLBACK)
  docker compose exec api node dist/scripts/importResponseCodes.js /tmp/response-codes.xlsx             # 반영
  docker compose exec -u root api rm /tmp/response-codes.xlsx
  ```
  `DATABASE_URL`은 컨테이너 환경 변수 그대로(`.env`를 읽지 않음). 잘못된 행이 있으면 아무것도 쓰지 않고 종료 2. 파일에 같은 코드가 여러 행이면 메시지·코드명·메시지 키를 `A 또는 B`로 합친다(유형·HTTP 상태가 다르면 잘못된 행, 출력 `merged codes`). 다시 실행하면 바뀐 값만 반영, 화면에서 고친 메시지는 `--force` 없이는 덮지 않는다, 파일에 없는 코드는 지우지 않는다(규칙 상세 contracts/response-codes-ui.md 6절)
- 시험(`erd-v03-response-codes.mjs`, `erd-v03.mjs`가 호출): `erd025`(디렉터리의 모든 migration 파일을 번호 순으로 한 문자열씩, PK·FK 1개·트리거 없음·색인 카탈로그, DBML parity, 역할 5개 권한 정확 일치, 실제 LOGIN API INSERT·수정 번호 UPDATE·기록 INSERT, DELETE·TRUNCATE·기록 UPDATE/DELETE 42501, GENERATED ALWAYS·FK, 다른 역할 42501, CHECK 경계, 합성 값만). 2026-11-13 embedded PostgreSQL 17.10 1259 checks 통과(임시 폴더 D 드라이브). API·도구 경로는 `backend/tests/response-codes.integration.mjs`(CI 단계 "Response code dictionary import and API under runtime role", `powercode_api` 연결)
- 함께 바꾼 기존 시험: `prepare-runtime-db.py` 목록에 025·`qa_response_codes_service` 추가. 시험 도구는 migration 디렉터리를 이름 순으로 읽어 적용한다

## 026 알림 조회 기간

- 파일 `026_notification_window_days.sql`, 022 적용 뒤 한 번 실행(023~025와 의존 없어 순서 무관)(`psql -v ON_ERROR_STOP=1 -f`), 022 표 소유자로 실행. 022 `ops_user_notification_settings`에 열 `window_days integer NOT NULL DEFAULT 7 CHECK (1~30)` 1개만 추가(새 표·함수·트리거·색인·enum 없음), 단일 BEGIN..COMMIT. 자동 down migration 없음(되돌리기 = `ALTER TABLE ops_user_notification_settings DROP COLUMN window_days`, 이전 API는 이 열을 읽지 않음)
- 잠금: 이 표에만 ACCESS EXCLUSIVE를 잠깐(상수 DEFAULT라 행 재작성 없음, CHECK 검사는 사용자 수만큼의 행). 다른 표는 잠그지 않고 작업자를 멈출 필요 없음
- **역할 스크립트 재적용 불필요**: 022 GRANT가 표 단위(`powercode_api` SELECT·INSERT·UPDATE)라 새 열에도 그대로 적용된다. 열 단위 GRANT를 쓰지 않으므로 `scripts/database-roles.sql` 변경 없음(다른 이유로 재적용해도 결과 같음)
- **배포 순서: migration 026 → API → WebUI**. 기존 행은 7을 받고, 이 열을 모르는 이전 API의 INSERT는 DEFAULT, upsert는 이 열을 건드리지 않아 저장값 유지(먼저 적용해도 안전). 새 API는 이 열을 읽으므로 열이 없으면 알림·설정 조회가 503. 새 WebUI는 응답의 `windowDays`를 요구하므로 API보다 먼저 올리지 않는다
- 의미: API가 알림 목록·미확인 수를 "지금 − `window_days`일" 이후 판정 이벤트로 좁힌다(기존 최근 5,000개 이벤트 상한은 요청 비용 상한으로 유지, `contracts/notifications.md` "알림 단위" 절). 기간 경계는 개수 범위(최대 5,000행) 안을 PK 역순으로 읽어 정하므로 `run_decision_events.created_at` 색인은 두지 않는다
- 시험(`erd-v03-notification-window.mjs`, `erd-v03.mjs`가 호출): `erd026_upgrade`(001~025 + 역할 스크립트 + 기존 설정 행 → 026: 기존 행 7, 역할 스크립트 재적용 없이 실제 LOGIN API가 새 열 upsert·조회, 이전 API 형태 upsert는 저장값 유지), CHECK 경계(0·31·NULL 거부, 1·30 허용), DBML parity. 2026-11-14 embedded PostgreSQL 17.10 1276 checks 통과(임시 폴더 D 드라이브). API 경로는 `backend/tests/notifications.integration.mjs`(CI 단계 "User settings and notifications API under runtime role", `powercode_api` 연결)
- 함께 바꾼 기존 시험: `prepare-runtime-db.py` 목록에 026 추가, `erd022` API 문장 형태를 새 upsert(`window_days` 포함)로 맞춤

## 027 프로토콜 경로 설명 작업 종류 [확정 11/14 사용자]

- 파일 `027_explain_protocol.sql`, 026 이후 한 번 실행(`psql -v ON_ERROR_STOP=1 -f`), 기존 표·함수 소유자로 실행. 계약 [protocol-explanation-job.md](../../contracts/protocol-explanation-job.md) DB 영향 절
- 왜: 프로토콜 설정 상세·프로토콜 알림 상세의 대화형 LLM 설명(`explain_protocol`). API가 `llm_explanation` 큐로 접수, LLM이 `run_llm_results`에 결과를 쓴다(explain과 같은 등급, 관리자만)
- enum 커밋 분리(017·021과 같음): `job_kind`에 `explain_protocol`을 트랜잭션 밖 `ADD VALUE IF NOT EXISTS`로 먼저 추가, 파일 안에서는 `kind::text` 비교만
- `run_jobs_v03_contract`(021본 기준): legacy 금지 목록에 추가, 새 분기 `queue='llm_explanation'`·user producer·`requester_role='admin'`·`llm_spec` 필수·manifest·구성 NULL(판정은 알림 대상만 — `input_decision_id` NULL 허용)
- `run_llm_results_kind_check`·`run_llm_results_check`(017본): kind 목록과 `explain_protocol` 분기(decision 조건 없음, 결과 판정 = 작업 판정은 `erd_v03_validate_result`가 강제). `erd_v03_validate_job`(017본): llm_spec 필수·LLM 완료 결과 필수 목록 2줄만
- 역할: `database-roles.sql`에 `GRANT SELECT ON ops_rule_set_revisions TO powercode_llm` 한 줄(사실을 만들 때 룰 세트 리비전 `protocolRules[]`를 읽음). 015 시험(`erd-v03-revisions.mjs`)의 권한 표를 같이 고침(llm은 룰 세트 이력 SELECT만)
- 적용 순서: 작업자를 멈추고 027 → 역할 스크립트 재적용 → LLM → API. 기존 코드는 새 값을 쓰지 않아 027 뒤에도 그대로 동작. 새 조건은 이전보다 넓기만 해 NOT VALID 없이 바로 검증
- 되돌리기: enum 값은 지울 수 없다. CHECK·함수는 021·017본으로 다시 만들 수 있으나 `explain_protocol` 작업·결과 행이 없을 때만
- 시험: `prepare-runtime-db.py`에 027 추가(dev 목록과 겹치지 않게 따로 둔 `later` 묶음, 병합 뒤 순서 …025·026·027 — CI가 한 문자열 실행으로 적용). 027 전용 `erd-v03-*.mjs` 시험은 아직 없다(미검증 — 실제 PostgreSQL 적용·재실행·역할 경계는 DB 세션 확인 대상)

## 큐와 소유권

- 001의 `detection_logs/training_jobs`는 기존 물리 큐. 기존 Python helper의 payload·물리 lease 경로 보존
- 기존 Trainer helper는 물리 학습 큐와 `run_jobs/run_job_attempts` 소유권을 결합하는 legacy 경로. 변경 없이 v03를 소비할 수 없음
- v03 logical queue: `detection`, `training`, `llm_recommendation`, `llm_explanation`. v03 작업의 소유권 원본은 `run_jobs`와 현재 `run_job_attempts` 하나
- 같은 v03 작업을 물리 큐에 복제하여 두 lease로 실행하지 않음. HTTP 직접 전달과 PG 복구는 동일 job ID·manifest·고정 config로 동일 원자 claim
- 짧은 claim 후 계산은 DB 트랜잭션 밖에서 실행. 완료는 현재 attempt·유효 lease·token을 확인하고 결과 INSERT·job/attempt 완료를 같은 트랜잭션으로 처리
- 신규 SQL은 결과 소유권·입력 계보 및 완료 결과 존재를 검사. claim/reclaim·heartbeat·backoff·예산·서비스 권한·정지 복구의 전체 실행기는 구현하지 않음
- at-least-once 재시도에서 중복 실행은 가능. unique 결과·멱등키로 중복 반영을 차단하며 exactly-once 실행을 주장하지 않음

## 입력과 BODY

- 원본 T/P/M/B의 수집 의미·revision·시각·미제공/null 보존. BODY는 별도 text 경계, API/큐/진단/모델/LLM에 자동 포함하지 않음
- BODY의 수집 성공과 사용 허용은 별개. 접근·보존 정책 버전·표현·마스킹 정책·3필드 상태·실제 표현 hash/byte 수 기록
- BODY 연결은 동일 source·message ID·가용 시점의 검증된 단일 M 후보에만 FK 부여. 늦은 M/B는 새 연결 검사 이력; ambiguous/unmatched 행의 M FK는 NULL
- 후보 수·동일 실행 판단·해시/byte의 실제 계산·권한·마스킹·수집 I/O 예산은 서비스 책임. DB가 `candidate_execution_count=1`의 진실을 외부 시스템에서 검증하지 않음
- manifest는 P와 당시 검증된 정확한 T/M observation 관계를 고정. `input_as_of`는 DB 저장 가용 시점 기준, 원천 timezone unknown을 보정하는 시각 아님
- T/M의 typed relation이 NULL/미확정이면 manifest에 임의 연결하지 않고 readiness에 기록. 늦게 확정된 관계는 새 관측/manifest 생성
- `message_observation_refs`: `[{"observation_id":"uuid","revision_no":1}]` 형태만 허용. M은 같은 source·정확한 P 관계·revision·as-of 검사
- manifest는 append-only. 참조된 관측 metadata와 T/P/M typed 행의 UPDATE/DELETE도 차단. M JSON 참조는 SQL FK 대신 검증·보호 trigger와 GIN 인덱스 사용
- manifest와 BODY 검증은 관측·typed 행에 FOR SHARE, 이후 신규 참조가 생긴 행은 보호 trigger 적용. 기본 READ COMMITTED의 검증/변경 경합 시험 범위; 다른 격리 수준과 전체 runtime 원자성까지 보장하는 선언 아님
- `content_hash`는 저장된 불변 입력의 정규화 hash. 직접 전달된 입력의 canonicalization/schema/hash 확인은 Detector 계약이며 DB가 본문 hash를 계산하지 않음
- snapshot은 원본 선택 목록/분할 고정. 기존 generic observation FK로 B도 명시 선택 가능, 기본 PROCESS·T/M 모델 입력과 BODY 접근/선택/가공 권한 분리
- train-only fit·동일 조건의 기존 snapshot/test 재사용·validation에서 임계값 선택·미라벨 정상 가정 금지·합성 평가 한계 유지

## LLM 결과와 파일

- 추천 종류 `recommend_thresholds`는 관리자, `explain` 요청은 user/admin. `recommend_rule/recommend_feature`는 폐지(API 신규 요청 422 `job_kind_abolished`, enum 값·옛 행·결과 읽기는 유지), `recommend_methods`는 보류(API 접수 없음). 이 role 값은 인증된 계정에서 서버가 확인, DB 감사 필드만으로 실제 인증 완료 아님
- `llm_spec`의 필수 문자열: `evidence_version`, `input_evidence_hash`, `model_version`, `prompt_version`, `output_schema_version`, `reuse_key`
- 설명 job은 저장 판정·그 판정 config를 참조. 추천과 설명의 logical queue·재시도·실행 예산 분리, 판정 동기 경로를 LLM 완료에 연결하지 않음
- `run_llm_results`는 성공·검증된 최종 출력만 저장. job kind/decision/config/version/reuse 값과 현재 attempt/token/lease 일치 검사, 실패는 job/attempt에 남김
- 재사용 키는 근거·허용 문맥/정책·LLM 모델·프롬프트·출력 스키마·근거 버전으로 서버에서 계산. 접근 권한 재검사 없이 결과를 공유하지 않음
- 출력 JSON object만으로 문법·필드·근거 연결 검증 완료가 되지 않음. 모델 출력 허용 문법·참조·사실성 및 BODY 권한은 서비스 검증 후 INSERT
- 대용량 모델/fit/manifest는 파일저장소. `ml_artifacts`는 완성 파일의 실제 hash/크기 검증 후 등록하는 불변 registry 계약, 미완성 staging 파일 등록 금지
- model_input의 변환 대상 snapshot과 fit의 학습 snapshot은 달라도 허용. 같은 source·FeatureSet 호환성을 검사하여 새 validation/test snapshot에도 기존 train fit 재사용 가능
- 학습 모델→학습 model_input 참조는 같은 snapshot·FeatureSet·fit 계보 검사. train split만 fit한 실제 통계와 runtime/파일 hash 확인은 서비스 책임
- 기존 snapshot·FeatureSet·config 자식행의 모든 불변성, artifact 파일 재작성 차단은 기존 서비스 책임. 이번 migration을 완전한 운영 gate로 해석하지 않음

## 승인·활성·보존·용량

- 기존 `run_applications`의 승인 시각·예상 active/revision·lease·gate·transition audit·실제 활성 시각과 `ops_system_state` 재사용
- 평가한 동일 피처/fit/Rule/ML/임계값 묶음만 승인·준비·실제 적용. CAS와 실제 워커 로드·장애 복구는 별도 runtime 구현
- 기존 `ops_storage_measurements` 재사용. BODY 저장소와 파일저장소 포함 실제 capacity/used/available 계측, 동일 filesystem 중복 합산 금지, stale/unavailable를 정상으로 대체하지 않음
- 권한(`scripts/database-roles.sql`, #138): `ops_storage_measurements`는 `powercode_api` SELECT만(`/api/operations/storage` 읽기). 다른 런타임 역할 권한 없음. 새 migration 없이 DBA가 역할 스크립트 재적용. 서버 연결을 API 역할로 띄운 HTTP 200·SELECT 전용을 `backend/tests/operations-grants.integration.mjs`(CI 단계 "Operations API runtime-role grants")가 확인
- 권한(`scripts/database-roles.sql`, 입력 변수 세트 등록): `ml_feature_versions`·`ml_feature_sets`·`ml_feature_set_members`에 `powercode_api` INSERT 추가(SELECT는 기존). 화면이 고른 필드로 `operator_defined` 세트를 `pg_advisory_xact_lock(81732,1)` 아래 SELECT·INSERT만으로 등록한다(trainer-service.md 입력 변수 세트 등록 규칙, contracts/models-ui.md). UPDATE·DELETE 없음, 행은 004 불변 trigger 유지. 새 migration 없이 DBA가 역할 스크립트 재적용. API 역할 등록·수정 거부는 `backend/tests/feature-sets.integration.mjs`(CI 단계 "Feature set registration API under runtime role")가 확인
- 신규 불변 테이블의 DELETE도 차단. 보존 기간 만료 purge·참조 해제·감사 삭제 절차는 이번 범위 미구현; 관리자 임의 trigger 해제로 삭제하지 않음
- snapshot·운영/감사 참조 무결성·백업/복원 확보 후 보존 정책 결정. 자동 cascade/purge 없음

## DBML과 SQL 대응

- DBML은 001~026(024 [제안] 포함) 적용 후 최종 구조(컬럼·nullability·unique·FK·enum 순서). 물리 큐 5개도 명시. enum 순서는 PostgreSQL 추가 순서(`job_kind`의 `import_csv`는 explain 뒤)
- nullable FK는 `>?`, unique FK는 부모 `-` 자식 방향, nullable unique FK는 부모 `?-` 자식 방향으로 표시. 실제 FK 방향은 SQL/파싱 대응 시험으로 확인
- attempt의 복합 FK 3개는 DBML의 exact-composite index 판정 한계로 `>`/`>?` 표시. 자식의 개별 PK/unique가 실제 1:1 제약을 보장한다는 주석 유지; 그림 경고를 없애려고 SQL 중복 인덱스를 추가하지 않음
- `@dbml/core`는 현재 문법의 `dbmlv2` parser 사용. deprecated `dbml` parser는 optional 관계 문법을 지원하지 않음
- `event_sequence`는 SQL의 `GENERATED BY DEFAULT AS IDENTITY`로 NOT NULL, DBML은 `not null/unique/increment` 표시. SQL 없는 별도 DEFAULT를 생성하지 않음
- CHECK·partial index·GIN 실행 방식·trigger·함수·뷰(015 `ops_operational_state`)는 SQL 원본 기준. DBML index note에 partial 조건을 표시하며 전체 행 unique로 오해하지 않음
- DBML 표시/파싱 성공은 SQL 적용·운영 migration 성공과 별개. 격리 PostgreSQL의 기존 행 보존·negative constraint·enum·컬럼·FK 대응 시험 후 검수

## 격리 시험 재현

- 임시 도구 폴더에만 `npm install --prefix <TEMP>/powercode-erd-review-tools embedded-postgres@17.10.0-beta.17 pg@8.23.1 @dbml/core@10.3.1`
- 저장소 루트에서 `node server/infra/tests/erd-v03.mjs <절대 tools 폴더>` 실행
- 임시 DB는 OS 임시 폴더에 생기고 시험이 끝나면 지워짐. 시스템 드라이브 공간이 부족하면 실행 전에 환경 변수 `TEMP`·`TMP`·`TMPDIR`을 여유 있는 디스크의 폴더로 지정(Node `os.tmpdir()`·Python `tempfile`·embedded-postgres가 모두 이 값을 따름)
- 읽기 전용으로 확보한 서버 schema metadata JSON이 있으면 세 번째 인자로 전달, 기존 001/002의 컬럼·enum·인덱스·제약과 대조. 시험에서 서버 재접속이나 데이터행 조회 없음
- 실제 native PostgreSQL 17.10 임시 cluster·localhost 임의 포트에서 001→002→003→004 실행, 기존 행 보존과 negative 계약·DBML parity 검증
- 운영 서버·고객 데이터·.env·인증파일 사용 없음. 임시 DB/의존성만 사용, 완료 시 시험 프로세스 종료
- 최종 검수: native PostgreSQL 17.10 fresh/legacy-upgrade 147 checks 통과. 원문 dbmlv2 파싱, 30테이블·양방향 nullable·18 enum·76 FK, 읽기 점검 서버 metadata와 기존 001/002 대조, 두 연결 claim·manifest typed-row 경합 시험 포함
- 이 결과는 격리 schema 시험이며 프로젝트 worker 실행·실제 서버 003/004 적용·운영 배포·백업 복원 완료를 뜻하지 않음
- Issue #78(011) 시험: fresh(001~011)·upgrade(001~010 + evaluate/import_csv/LLM 기존 행 + 011)·011 중간 오류 ROLLBACK·search_path 가리기 4개 DB. 새 kind 큐·요청자·legacy 분기, `recommend_methods` 결과·완료, 평가 세트 상태·불변·작업 실패 동기화(종료·시도 초과·dead 큐 정리), 비교 2테이블 FK·CHECK·불변, `run_evaluations` 다중 행·중복 거부, `ml_config_versions` 불변, `database-roles.sql`·`demo-role.sql` 권한(SET ROLE), 런타임 역할 새 연결의 임시 테이블 가리기 6종 거부 포함. 2026-10-21 native PostgreSQL 17.10(embedded) 375 checks 통과, DBML 58테이블 parity
- dev(011 이전) `erd-v03.mjs`는 DBML 누락 24테이블·`job_kind` 순서 때문에 parity에서 실패했음(#78에서 보강)
- Issue #78(012) 시험(`erd-v03-interim.mjs`, `erd-v03.mjs`가 호출): 기존 fresh·upgrade DB는 001~012로 DBML 59테이블 parity. 추가 5개 DB(`erd_interim_y7a2_012`는 #82에서 추가) — `erd_interim_fresh`(S1~S12·Q11·함수 설정·README 소유자 쿼리와 소유자 불일치 반례·두 연결 advisory lock 대기·이벤트 순번), `erd_interim_upgrade`(001~011 + 기존 판정·이벤트·진행 중 작업 → 012, U0~U5 후 시나리오 재실행), `erd_interim_rollback`(012 COMMIT 직전 오류 주입 → 미반영 → 재적용), `erd_interim_attack`(비슈퍼유저 소유자가 001~012 적용, `database-roles.sql` 적용 뒤 실제 LOGIN 역할 새 연결로 G4·CX-A~C·X1~X7·G1~G3·R5·Y1·Y2·Y5·Y6·Y7A2·YP/Q11·Q11 경합(Detector 로그인 3연결: 미커밋 failed 전환·대기 중 되돌리기·판정 INSERT)·B6b, 비소유자 REVOKE TEMP 무시(WARNING), README 위협 모델 쿼리 양·음 사례). 가리기 공격은 TEMP를 다시 허용한 상태에서 함수 방어(012, 013 뒤에는 고정된 004 포함)를 검증하고, REVOKE TEMP 거부는 G4로 따로 확인. Y7(004 기존 결함)은 013(#82) 뒤 004 거부를 PASS로 단언, Y7A2의 012 층 단독 거부는 001~012만 적용한 `erd_interim_y7a2_012`에서 확인
- 011 search_path 시험 DB도 `database-roles.sql`의 REVOKE TEMP 거부(42501)를 먼저 확인한 뒤 TEMP를 다시 허용해 011 고정만 검증. 2026-10-21 native PostgreSQL 17.10(embedded) 629 checks 통과
- Issue #82(013) 시험: 위 전체에 `erd82_upgrade`·`erd82_fresh`·`erd82_rollback` 3개 DB 추가(013 절). 검증 범위: 실제 우회 회귀 1경로(Y7, 판정 행 lease 검사 — 013 적용 전 우회·적용 후 거부) + 대상 8개 함수 search_path 설정 자동 확인. 다른 대상 함수의 우회 재현 시험은 범위 밖. 2026-11-02 native PostgreSQL 17.10(embedded) 685 checks 통과(dev #81 병합 후 아래 #81 시험 포함). 013의 `ALTER FUNCTION` 8줄을 지운 대조 실행은 Y7(만료 lease 판정 수락) 1건 + 세 DB 함수 설정 검사 4건 FAIL. `prepare-runtime-db.py`(001~013) 뒤 worker unittest 123건·integration input_roles·demo_permissions·demo_replay 통과
- Issue #81 시험(`erd-v03-llm-grant.mjs`, `erd-v03.mjs`가 호출): `pg_queue.reconcile_dead_jobs`의 training 큐 SQL 2문을 파일에서 읽어 `powercode_llm` 실제 LOGIN 새 연결·한 트랜잭션으로 실행. `erd_llm_grant_81_pre`(#81 GRANT 뺀 역할 스크립트) 42501 재현·대조, `erd_llm_grant_81` 정리 성공(작업 failed·queued 세트 failed·`errorCode` `queue_exhausted`·기존 `validation_result` 키 유지, ready·failed·무관 세트 불변), 권한 카탈로그 정확 일치, 다른 열 SELECT·내용 열 UPDATE·INSERT·DELETE·TRUNCATE 42501, 부여 열 하나씩 REVOKE 시 정리 42501. 2026-10-22 native PostgreSQL 17.10(embedded) 665 checks 통과
- Issue #110(015) 시험(`erd-v03-revisions.mjs`, `erd-v03.mjs`가 호출): `erd110_fresh`·`erd110_upgrade`·`erd110_rollback` 3개 DB 추가(015 절). 함께 고친 기존 시험 결함(014 이후 `erd-v03.mjs`가 끝까지 돌지 못했음): `erd_interim_y7a2_012`는 001~012만 적용하므로 역할 스크립트의 014·015 GRANT를 빼고 적용, `erd82_rollback`은 013 재적용 뒤 남은 마이그레이션까지 적용한 다음 parity 대조, `parity`는 기본 표만 대조(뷰 제외). `erd-v03.mjs`는 CI에 없는 격리 시험이라 변경 시 직접 실행. 2026-11-04 native PostgreSQL 17.10(embedded) 806 checks 통과
- Issue #119(016) 시험(`erd-v03-detect-v04.mjs`, `erd-v03.mjs`가 호출): `erd119_*` 6개 DB 추가(016 절). 함께 바꾼 기존 시험: `erd-v03-revisions.mjs`는 015까지만 적용한 시점의 r0를 판정하도록 고정(`erd110_fresh`는 015에서 멈춤, `erd110_upgrade`·`erd110_rollback`은 015 판정 뒤 끝까지 적용해 parity 대조, r0 값은 문서 예시 대신 시험에 고정 — 문서 예시는 이제 내장 목록 4개를 설명), `erd82_upgrade`는 함수 본문 비교를 013 직후에 수행(016이 3개를 의도적으로 재정의), 012 S3b 메시지 기대값 변경. `erd-v03.mjs`는 실행마다 임시 클러스터를 남겼으므로 성공·실패·예외·시그널·postgres 사망·기동 실패 모두 폴더를 지우도록 고침(`stop()`에 시간 제한, 끝까지 통과한 실행만 exit 0). 한계: 시작 후 약 4초의 클러스터 기동 구간에 들어온 시그널과 강제 종료는 폴더를 남길 수 있고, 이때도 종료 코드는 0이 아님. 2026-11-05 native PostgreSQL 17.10(embedded) 923 checks 통과
- Issue #124(017) 시험(`erd-v03-recommend-thresholds.mjs`, `erd-v03.mjs`가 호출): `erd124_*` 5개 DB 추가(017 절). 2026-11-05 native PostgreSQL 17.10(embedded) 1041 checks 통과. 함께 바꾼 기존 시험: `erd119_upgrade`·`erd119_rollback`은 016 판정 뒤 끝까지 적용해 parity 대조(`schema.dbml`이 최종 구조이므로), `erd-v03-detect-v04.mjs`의 공용 도구 5개(`catalog`·`code`·`delta`·`nameOf`·`statements`)를 export. `prepare-runtime-db.py` 목록에 017 추가
- Issue #129(018) 시험(`erd-v03-decision-index.mjs`, `erd-v03.mjs`가 호출): `erd129_*` 3개 DB 추가(018 절). 2026-11-05 native PostgreSQL 17.10(embedded) 1071 checks 통과. `psql` 바이너리가 없어 `psql -f`는 문장 단위 실행으로 대신함. 기존 시험은 바꾸지 않음
- 019 시험(`erd-v03-incidents.mjs`, `erd-v03.mjs`가 호출): `erd019` 1개 DB(019 절). 2026-11-12 native PostgreSQL 17.10(embedded) 1102 checks 통과(이전 파일 이름 기준 측정, 임시 폴더 D 드라이브)
- 021 시험(`erd-v03-recommend-relations.mjs`, `erd-v03.mjs`가 호출): `erd021` 1개 DB(021 절). 2026-11-12 native PostgreSQL 17.10(embedded) 1148 checks 통과(이전 파일 이름 기준 측정). 019~021 번호로 바꾼 뒤 2026-11-13 같은 1148 checks 통과(임시 폴더 D 드라이브)
