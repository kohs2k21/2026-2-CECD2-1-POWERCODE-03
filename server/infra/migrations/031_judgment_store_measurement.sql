-- 031: 판정 저장 소요 측정 단계(contracts/detection-decision.md 19절, operations-ui.md latency judgmentStore).
-- 왜: Detector가 판정 쓰기 트랜잭션 진입 → COMMIT 완료를 재서 ops_pipeline_measurements에 stage='judgment_store' 1행을 남긴다(첫 판정만, 재판정 제외).
--   판정 행에는 둘 수 없다(커밋 시각은 그 트랜잭션 안에서 알 수 없고 판정 행은 불변 — 004 decision_v03_immutable). 그래서 측정 표의 새 단계로 둔다.
-- 바꾸는 것: 004 ops_pipeline_measurements의 CHECK 2개만 같은 이름으로 다시 만든다.
--   ① ops_pipeline_measurements_stage_check(004 열 CHECK의 자동 이름): ('feature','detector') → ('feature','detector','judgment_store')
--   ② ops_pipeline_measurements_config_check(016): config_version_id NULL 허용 단계를 detector → detector·judgment_store. feature는 계속 구성 필수
--   001~030 표·열·함수·트리거·enum·인덱스·권한은 바꾸지 않는다. 새 표 없음 → scripts/database-roles.sql 변경 없음(detector INSERT는 004 GRANT 그대로).
-- 전제: 030까지 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 표 소유자로 실행. 자동 down migration 없음.
-- 문장 1개(020과 같음): ALTER TABLE 한 문장의 DROP·ADD 4개는 함께 성공하거나 함께 실패하므로 BEGIN..COMMIT이 필요 없다(반쪽 적용 없음).
--   재실행: 같은 이름을 지우고 같은 정의로 다시 만들어 결과가 같다(오류 없음). 리터럴이 ASCII뿐이라 client_encoding 설정도 필요 없다.
-- 잠금: 문장이 끝날 때까지 ops_pipeline_measurements에 ACCESS EXCLUSIVE. 새 CHECK 검증이 표 heap을 읽는다(행 수에 비례, 보존 정책이 없어 판정 수만큼 쌓인 표).
--   그동안 Detector 판정 트랜잭션(detector 측정 행 INSERT)·Feature 워커·API 운영 조회가 기다린다. 표가 크면 Detector를 멈추고 적용한다.
--   새 조건이 이전보다 넓어 기존 행이 검증에 실패할 수 없다.
-- 배포 순서: 031 → Detector(judgment_store 행을 쓰는 코드). 031 전에 새 Detector가 돌면 측정 INSERT만 23514로 실패하고,
--   Detector는 로그만 남기고 판정·작업을 그대로 성공시킨다(표본만 없음). 이전 Detector는 031 뒤에도 그대로 동작한다(detector·feature 행 조건 불변).
-- 되돌림: judgment_store 행이 생기기 전에만 두 CHECK를 004·016 정의로 되돌릴 수 있다(생긴 뒤에는 그 행을 지워야 한다 — 측정 표본이라 지워도 판정에 영향 없음).
ALTER TABLE ops_pipeline_measurements
 DROP CONSTRAINT ops_pipeline_measurements_stage_check,
 ADD CONSTRAINT ops_pipeline_measurements_stage_check CHECK (stage IN ('feature','detector','judgment_store')),
 DROP CONSTRAINT ops_pipeline_measurements_config_check,
 ADD CONSTRAINT ops_pipeline_measurements_config_check CHECK (config_version_id IS NOT NULL OR stage IN ('detector','judgment_store'));
