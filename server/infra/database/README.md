Created: 2026-10-12T02:06:03+09:00
Updated: 2026-10-21T19:40:00+09:00
Author: contracts_page_reviewer
Status: current

# ERD v0.3와 호환 migration

- Issue #32 기준: 최신 PostgreSQL·공통 규약·LLM 기획 반영. `schema.dbml`은 최종 목표 구조의 시각화, 실행 원본은 migration SQL
- 목표 구조(Issue #32 당시): public 28테이블 + `powercode_queue` 기존 물리 큐 2테이블. 기존 public 24테이블 보존, BODY·BODY 연결·불변 입력·LLM 결과 4테이블만 추가
- 현재 `schema.dbml`(Issue #78): 001~011 전체 반영, public 53테이블 + `powercode_queue` 5테이블. 004_feature_pipeline·005~009 추가분 24테이블 보강, 011 3테이블 추가
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

- DBML은 001~011 적용 후 최종 구조(컬럼·nullability·unique·FK·enum 순서). 물리 큐 5개도 명시. enum 순서는 PostgreSQL 추가 순서(`job_kind`의 `import_csv`는 explain 뒤)
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
