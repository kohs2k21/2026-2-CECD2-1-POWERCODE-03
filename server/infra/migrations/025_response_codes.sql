-- 025: 응답 코드 사전(WebUI 룰 설정 › 응답 코드 목록·상세, 이상 분석 원인 줄의 메시지). API만 읽고 쓴다.
-- 새 표 2개·색인 1개만 만든다. 001~023 표·함수·트리거·enum·CHECK·권한은 바꾸지 않는다.
-- 전제: 001~023 적용 뒤 1회 실행(024와 의존 없음, 순서 무관)(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 역할 스크립트(scripts/database-roles.sql) 재적용은 023·024·025를 모두 적용한 뒤 한 번(infra/database/README.md 024 절 배포 순서).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음(되돌리기 = 새 표 2개 DROP, 다른 표 영향 없음).
--   잠금: 새 표끼리만 참조한다(변경 기록 → 사전 FK). 기존 표를 잠그지 않으므로 적용 중 작업자를 멈출 필요 없음.
-- 사전 값은 저장소에 넣지 않는다: 이 migration은 빈 표만 만들고, 기본값은 운영자가 가져오기 도구(backend dist/scripts/importResponseCodes.js,
--   infra/database/README.md "025 응답 코드 사전" 절)로 DB에만 넣는다. 고객 자료라 공개 저장소의 코드·시험·문서에 실제 값을 쓰지 않는다.
-- 코드별 위험도는 이 표에 두지 않는다: 룰 세트 hard:transaction-failed severity {byCode, default}(rule-set.md v3.5)가 정본이고
--   저장·버전·되돌리기도 룰 세트로 한다(contracts/response-codes-ui.md). Detector는 이 표를 읽지 않는다.
-- 표:
--   ops_response_codes         코드당 1행. code = 거래·PROCESS 행 응답 코드(byCode 키와 같은 형식: 인쇄 가능한 ASCII 1~64자).
--                              origin = 'import'(가져오기 값) | 'edited'(화면에서 메시지 편집). 가져오기는 기본으로 edited 행을 덮지 않는다.
--                              revision = 행 수정 번호(1부터, 화면 편집의 낙관적 잠금). 행은 지우지 않는다(DELETE 권한 없음).
--   ops_response_code_changes  메시지 변경 기록(추가만). 수정·삭제 권한이 없어 기록이 바뀌지 않는다 — 사전 행 jsonb 열에 두면
--                              API가 사전 행 UPDATE 권한으로 기록까지 고칠 수 있어 따로 둔다.
BEGIN;

-- 1. 사전. 길이 상한은 화면·API 검사와 같다(contracts/response-codes-ui.md 2절).
CREATE TABLE ops_response_codes (
 code text PRIMARY KEY CHECK (code ~ '^[!-~]{1,64}$'),
 enum_name text CHECK (enum_name IS NULL OR (enum_name <> '' AND char_length(enum_name) <= 128)),
 code_type text CHECK (code_type IS NULL OR (code_type <> '' AND char_length(code_type) <= 64)),
 http_status text CHECK (http_status IS NULL OR (http_status <> '' AND char_length(http_status) <= 64)),
 message_key text CHECK (message_key IS NULL OR (message_key <> '' AND char_length(message_key) <= 256)),
 message text NOT NULL CHECK (message <> '' AND char_length(message) <= 500),
 origin text NOT NULL CHECK (origin IN ('import','edited')),
 revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_by_actor_id text NOT NULL CHECK (updated_by_actor_id <> '' AND length(updated_by_actor_id) <= 128)
);

-- 2. 메시지 변경 기록. kind = 'import'(가져오기가 넣거나 바꿈) | 'edit'(화면 편집). message_before NULL = 처음 넣은 행.
CREATE TABLE ops_response_code_changes (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 code text NOT NULL REFERENCES ops_response_codes(code),
 kind text NOT NULL CHECK (kind IN ('import','edit')),
 message_before text CHECK (message_before IS NULL OR char_length(message_before) <= 500),
 message_after text NOT NULL CHECK (message_after <> '' AND char_length(message_after) <= 500),
 actor_id text NOT NULL CHECK (actor_id <> '' AND length(actor_id) <= 128),
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- 상세 화면은 코드별 최근 기록부터 읽는다.
CREATE INDEX ops_response_code_changes_code_idx ON ops_response_code_changes (code, id DESC);

COMMIT;
