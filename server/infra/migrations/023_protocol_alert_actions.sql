-- 023: 프로토콜 알림 처리 기록(WebUI 알림 [이상 유지]·프로토콜 알림 항목별 읽음). API만 읽고 쓴다.
-- 새 표 2개만 만든다. 001~022 표·함수·트리거·enum·CHECK·권한은 바꾸지 않는다(022 항목 읽음 표의 item_key CHECK도 그대로 — 프로토콜 항목은 새 표에 둔다).
-- 전제: 001~022 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용.
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음(되돌리기 = 새 표 2개 DROP, 다른 표 영향 없음).
--   잠금: 다른 표를 참조하지 않으므로(FK 없음) 기존 표를 잠그지 않는다. 적용 중 작업자를 멈출 필요 없음.
-- 알림 항목 자체는 저장하지 않는다: API가 읽을 때 최종 판정 evidence.protocolReview(Detector protocol-rules.md 5.3)에서 만든다
--   (contracts/protocol-settings-ui.md 5절). 묶음 키 = (interfaceId, pathKey), 항목 키 = 'protocol:' || sha256(jsonb_build_array(interfaceId, pathKey)::text) 16진.
--   경로를 만들 수 없는 미등록 인터페이스 알림(routes = [])은 pathKey = ''(인터페이스 단위). pathKey가 길 수 있어(채널 64개) PK는 항목 키(고정 길이)로 둔다.
-- 표:
--   ops_protocol_alert_keeps        [이상 유지] 기록(시스템 전역, 묶음 키당 1행, 추가만). 판정은 이상 그대로이고 Detector는 이 표를 읽지 않는다.
--                                   kept_through_seq = 누른 시점의 알림 순번 → 그 뒤 같은 (인터페이스, 경로)가 다시 나와도 다시 알리지 않는다(목록에는 남김).
--   ops_user_protocol_alert_reads   프로토콜 알림 항목별 읽음(022 ops_user_notification_reads와 같은 규칙, 키 형식만 다름).
BEGIN;

-- 1. [이상 유지]. 수정·삭제 없음(API 권한 SELECT·INSERT만). 같은 키를 다시 누르면 API가 ON CONFLICT DO NOTHING.
CREATE TABLE ops_protocol_alert_keeps (
 alert_key text PRIMARY KEY CHECK (alert_key ~ '^protocol:[0-9a-f]{64}$'),
 interface_id text NOT NULL CHECK (interface_id <> '' AND char_length(interface_id) <= 512),
 -- 원래 pathKey(protocol-rules.md 5.3 이스케이프 뒤 ':'로 이은 값, 쪼개 읽지 않음). '' = 경로 없는 미등록 인터페이스 알림.
 path_key text NOT NULL CHECK (octet_length(path_key) <= 262144),
 kept_through_seq bigint NOT NULL CHECK (kept_through_seq >= 0),
 -- 누를 때 본 최신 판정(표시용, FK 없음 — 판정 행은 append-only라 사라지지 않는다).
 decision_id uuid,
 kept_by_actor_id text NOT NULL CHECK (kept_by_actor_id <> '' AND length(kept_by_actor_id) <= 128),
 kept_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- 2. 프로토콜 알림 항목별 읽음. API가 모두 읽음 순번 이하·조회 범위 밖 행을 지운다(DELETE 권한, 022와 같음).
CREATE TABLE ops_user_protocol_alert_reads (
 actor_id text NOT NULL CHECK (actor_id <> '' AND length(actor_id) <= 128),
 item_key text NOT NULL CHECK (item_key ~ '^protocol:[0-9a-f]{64}$'),
 read_seq bigint NOT NULL CHECK (read_seq >= 0),
 read_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (actor_id, item_key)
);

COMMIT;
