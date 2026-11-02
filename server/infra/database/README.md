Created: 2026-10-12T02:06:03+09:00
Updated: 2026-10-22T08:03:00+09:00
Author: contracts_page_reviewer
Status: current

# ERD v0.3와 호환 migration

- Issue #32 기준: 최신 PostgreSQL·공통 규약·LLM 기획 반영. `schema.dbml`은 최종 목표 구조의 시각화, 실행 원본은 migration SQL
- 목표 구조(Issue #32 당시): public 28테이블 + `powercode_queue` 기존 물리 큐 2테이블. 기존 public 24테이블 보존, BODY·BODY 연결·불변 입력·LLM 결과 4테이블만 추가
- 현재 `schema.dbml`(Issue #78): 001~012 전체 반영, public 54테이블 + `powercode_queue` 5테이블. 004_feature_pipeline·005~009 추가분 24테이블 보강, 011 3테이블·012 1테이블 추가
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
- 011이 만들거나 재정의한 함수 4개(`erd_v03_validate_job`·`protect_evaluation_set`·`fail_evaluation_set_with_job`·`validate_comparison_lineage`)는 `SET search_path = public, pg_temp`로 고정, SECURITY INVOKER 유지. 세션 임시 테이블의 같은 이름 가리기로 검증·동기화를 우회하지 못함. 001~010 함수는 미적용(후속 조치 대상)
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
- 기존 결함(012 범위 밖): 004 `erd_v03_validate_result`는 search_path 고정이 없어 임시 표 이름 가리기로 lease 만료 시도의 판정 행이 수락될 수 있음(erd-v03 Y7 정보 출력). `REVOKE TEMP`는 런타임이 CREATE 가능한 스키마가 하나도 없을 때 이 경로를 닫음. 012는 그런 판정이 더 새 시도의 묶음을 대체하는 것만 거부(Y7A2). 004 수정은 별도 Issue 대상

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

- 추천 종류 `recommend_rule/recommend_feature`는 관리자, `explain` 요청은 user/admin. 이 role 값은 인증된 계정에서 서버가 확인, DB 감사 필드만으로 실제 인증 완료 아님
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
- 신규 불변 테이블의 DELETE도 차단. 보존 기간 만료 purge·참조 해제·감사 삭제 절차는 이번 범위 미구현; 관리자 임의 trigger 해제로 삭제하지 않음
- snapshot·운영/감사 참조 무결성·백업/복원 확보 후 보존 정책 결정. 자동 cascade/purge 없음

## DBML과 SQL 대응

- DBML은 001~012 적용 후 최종 구조(컬럼·nullability·unique·FK·enum 순서). 물리 큐 5개도 명시. enum 순서는 PostgreSQL 추가 순서(`job_kind`의 `import_csv`는 explain 뒤)
- nullable FK는 `>?`, unique FK는 부모 `-` 자식 방향, nullable unique FK는 부모 `?-` 자식 방향으로 표시. 실제 FK 방향은 SQL/파싱 대응 시험으로 확인
- attempt의 복합 FK 3개는 DBML의 exact-composite index 판정 한계로 `>`/`>?` 표시. 자식의 개별 PK/unique가 실제 1:1 제약을 보장한다는 주석 유지; 그림 경고를 없애려고 SQL 중복 인덱스를 추가하지 않음
- `@dbml/core`는 현재 문법의 `dbmlv2` parser 사용. deprecated `dbml` parser는 optional 관계 문법을 지원하지 않음
- `event_sequence`는 SQL의 `GENERATED BY DEFAULT AS IDENTITY`로 NOT NULL, DBML은 `not null/unique/increment` 표시. SQL 없는 별도 DEFAULT를 생성하지 않음
- CHECK·partial index·GIN 실행 방식·trigger·함수는 SQL 원본 기준. DBML index note에 partial 조건을 표시하며 전체 행 unique로 오해하지 않음
- DBML 표시/파싱 성공은 SQL 적용·운영 migration 성공과 별개. 격리 PostgreSQL의 기존 행 보존·negative constraint·enum·컬럼·FK 대응 시험 후 검수

## 격리 시험 재현

- 임시 도구 폴더에만 `npm install --prefix <TEMP>/powercode-erd-review-tools embedded-postgres@17.10.0-beta.17 pg@8.23.1 @dbml/core@10.3.1`
- 저장소 루트에서 `node server/infra/tests/erd-v03.mjs <절대 tools 폴더>` 실행
- 읽기 전용으로 확보한 서버 schema metadata JSON이 있으면 세 번째 인자로 전달, 기존 001/002의 컬럼·enum·인덱스·제약과 대조. 시험에서 서버 재접속이나 데이터행 조회 없음
- 실제 native PostgreSQL 17.10 임시 cluster·localhost 임의 포트에서 001→002→003→004 실행, 기존 행 보존과 negative 계약·DBML parity 검증
- 운영 서버·고객 데이터·.env·인증파일 사용 없음. 임시 DB/의존성만 사용, 완료 시 시험 프로세스 종료
- 최종 검수: native PostgreSQL 17.10 fresh/legacy-upgrade 147 checks 통과. 원문 dbmlv2 파싱, 30테이블·양방향 nullable·18 enum·76 FK, 읽기 점검 서버 metadata와 기존 001/002 대조, 두 연결 claim·manifest typed-row 경합 시험 포함
- 이 결과는 격리 schema 시험이며 프로젝트 worker 실행·실제 서버 003/004 적용·운영 배포·백업 복원 완료를 뜻하지 않음
- Issue #78(011) 시험: fresh(001~011)·upgrade(001~010 + evaluate/import_csv/LLM 기존 행 + 011)·011 중간 오류 ROLLBACK·search_path 가리기 4개 DB. 새 kind 큐·요청자·legacy 분기, `recommend_methods` 결과·완료, 평가 세트 상태·불변·작업 실패 동기화(종료·시도 초과·dead 큐 정리), 비교 2테이블 FK·CHECK·불변, `run_evaluations` 다중 행·중복 거부, `ml_config_versions` 불변, `database-roles.sql`·`demo-role.sql` 권한(SET ROLE), 런타임 역할 새 연결의 임시 테이블 가리기 6종 거부 포함. 2026-10-21 native PostgreSQL 17.10(embedded) 375 checks 통과, DBML 58테이블 parity
- dev(011 이전) `erd-v03.mjs`는 DBML 누락 24테이블·`job_kind` 순서 때문에 parity에서 실패했음(#78에서 보강)
- Issue #78(012) 시험(`erd-v03-interim.mjs`, `erd-v03.mjs`가 호출): 기존 fresh·upgrade DB는 001~012로 DBML 59테이블 parity. 추가 4개 DB — `erd_interim_fresh`(S1~S12·Q11·함수 설정·README 소유자 쿼리와 소유자 불일치 반례·두 연결 advisory lock 대기·이벤트 순번), `erd_interim_upgrade`(001~011 + 기존 판정·이벤트·진행 중 작업 → 012, U0~U5 후 시나리오 재실행), `erd_interim_rollback`(012 COMMIT 직전 오류 주입 → 미반영 → 재적용), `erd_interim_attack`(비슈퍼유저 소유자가 001~012 적용, `database-roles.sql` 적용 뒤 실제 LOGIN 역할 새 연결로 G4·CX-A~C·X1~X7·G1~G3·R5·Y1·Y2·Y5·Y6·Y7A2·YP/Q11·Q11 경합(Detector 로그인 3연결: 미커밋 failed 전환·대기 중 되돌리기·판정 INSERT)·B6b, 비소유자 REVOKE TEMP 무시(WARNING), README 위협 모델 쿼리 양·음 사례). 가리기 공격은 TEMP를 다시 허용한 상태에서 012 함수 방어만 검증하고, REVOKE TEMP 거부는 G4로 따로 확인. Y7(004 기존 결함)은 판정·실패가 아닌 INFO 출력
- 011 search_path 시험 DB도 `database-roles.sql`의 REVOKE TEMP 거부(42501)를 먼저 확인한 뒤 TEMP를 다시 허용해 011 고정만 검증. 2026-10-21 native PostgreSQL 17.10(embedded) 629 checks 통과
- Issue #81 시험(`erd-v03-llm-grant.mjs`, `erd-v03.mjs`가 호출): `pg_queue.reconcile_dead_jobs`의 training 큐 SQL 2문을 파일에서 읽어 `powercode_llm` 실제 LOGIN 새 연결·한 트랜잭션으로 실행. `erd_llm_grant_81_pre`(#81 GRANT 뺀 역할 스크립트) 42501 재현·대조, `erd_llm_grant_81` 정리 성공(작업 failed·queued 세트 failed·`errorCode` `queue_exhausted`·기존 `validation_result` 키 유지, ready·failed·무관 세트 불변), 권한 카탈로그 정확 일치, 다른 열 SELECT·내용 열 UPDATE·INSERT·DELETE·TRUNCATE 42501, 부여 열 하나씩 REVOKE 시 정리 42501. 2026-10-22 native PostgreSQL 17.10(embedded) 665 checks 통과
