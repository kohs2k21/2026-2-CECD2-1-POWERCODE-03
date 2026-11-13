-- 020: 룰 세트 content_hash_version 3 허용.
-- 왜: hash v3은 관계 규칙과 소프트룰 범위를 정규 객체에 넣는다. 정규 객체·직렬화 규칙은 API·Detector 계약에 있고 DB는 버전 값의 허용 범위만 안다.
--   016 CHECK (content_hash_version IN (1,2))가 v3 저장을 거부하므로 같은 이름의 CHECK를 (1,2,3)으로 바꾼다.
-- 바꾸지 않는 것: 열 DEFAULT 2(API ruleSetStore.ts가 버전을 명시해 저장), 기존 행(r0 = 1, r1 = 2, UPDATE 없음 → 불변 트리거와 무관), 다른 표·함수·트리거·enum·권한.
-- 전제: 001~019 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 표 소유자로 실행. 새 표 없음 → database-roles.sql 변경 없음. 자동 down migration 없음.
-- 문장 1개(018과 같음): ALTER TABLE 한 문장의 DROP·ADD는 함께 성공하거나 함께 실패하므로 BEGIN..COMMIT이 필요 없다. 리터럴이 숫자뿐이라 client_encoding 설정도 필요 없다.
--   재실행: 같은 이름을 지우고 같은 정의로 다시 만들어 결과가 같다(오류 없음).
-- 잠금: 문장이 끝날 때까지 ops_rule_set_revisions에 ACCESS EXCLUSIVE. 새 CHECK 검증이 표를 한 번 읽는다(리비전 행 수만큼, 작은 표). API 저장·Detector 뷰 읽기가 잠시 기다린다.
--   새 조건이 이전보다 넓어 기존 행이 검증에 실패할 수 없다.
-- 배포 순서: DB 020 → Detector v3 → API v3. DB가 먼저 3을 받아야 API가 v3 행을 쓸 수 있고, Detector가 v3 행을 읽을 줄 안 뒤에 API가 v3로 저장해야 판정이 멈추지 않는다.
--   020만 적용한 상태에서 기존 코드는 계속 2를 쓰므로 수정 없이 동작한다.
-- 되돌림: v3 행이 생기기 전에만 CHECK를 (1,2)로 되돌릴 수 있다. v3 행은 불변이라 생긴 뒤에는 되돌릴 수 없다.
ALTER TABLE ops_rule_set_revisions
 DROP CONSTRAINT ops_rule_set_revisions_content_hash_version_check,
 ADD CONSTRAINT ops_rule_set_revisions_content_hash_version_check CHECK (content_hash_version IN (1,2,3));
