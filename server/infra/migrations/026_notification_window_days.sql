-- 026: 사용자별 알림 조회 기간(일)(WebUI 설정 › 알림 설정 › 알림 조회 기간, 사용자 11/14 "알림 기간 설정도 만들어"). API만 읽고 쓴다.
-- 022 ops_user_notification_settings에 열 1개만 더한다. 001~025 표·함수·트리거·enum·권한은 바꾸지 않는다. 새 표·함수·트리거·시퀀스·색인 없음.
-- 전제: 022 적용 뒤 1회 실행(023~025와 의존 없음, 순서 무관)(psql -v ON_ERROR_STOP=1 -f), 022 표 소유자로 실행.
--   역할 스크립트(scripts/database-roles.sql) 재적용 불필요: 022 GRANT가 표 단위(SELECT,INSERT,UPDATE)라 새 열에도 그대로 적용된다(infra/database/README.md "026" 절).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음(되돌리기 = 이 열 DROP COLUMN, 다른 표 영향 없음).
--   잠금: 이 표에 ACCESS EXCLUSIVE를 잠깐 잡는다(상수 DEFAULT라 행 재작성 없음, CHECK 검사는 사용자 수만큼의 행만 읽음). 다른 표는 잠그지 않고 작업자를 멈출 필요 없음.
--   기존 API 호환: 기존 행은 DEFAULT 7을 받는다. 이 열을 모르는 이전 API의 INSERT는 DEFAULT를, upsert는 이 열을 건드리지 않아 저장값이 유지된다
--   → migration을 API보다 먼저 적용한다(새 API는 이 열을 읽으므로 열이 없으면 알림·설정 조회가 503).
-- 의미: 알림 목록·미확인 수는 "지금 − window_days일" 이후 판정 이벤트만 대상으로 한다(기존 최근 5,000개 이벤트 상한은 그대로, contracts/notifications.md "알림 단위" 절).
--   범위는 1~30 정수, 기본 7. API 검사(contracts/notifications.md "API" 절)와 같은 범위를 CHECK로도 둔다.
BEGIN;

ALTER TABLE ops_user_notification_settings
 ADD COLUMN window_days integer NOT NULL DEFAULT 7 CHECK (window_days BETWEEN 1 AND 30);

COMMIT;
