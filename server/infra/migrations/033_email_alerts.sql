-- 033: 메일 알림(관리자 설정·SMTP·받는 사람·알림 기록·발송 기록, contracts/email-alerts.md). API만 읽고 쓴다.
-- 새 표 5개만 만든다. 001~032 표·함수·트리거·enum·권한 정의는 바꾸지 않는다. 새 함수·트리거·시퀀스·enum 없음.
-- 전제: 032 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용(새 표 GRANT 1줄).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음(되돌리기 = 새 표 5개 DROP, 이전 API는 이 표를 읽지 않는다).
--   잠금: FK는 새 표끼리만 있어 기존 표(run_incidents·run_decision_events 등)를 잠그지 않는다. 작업자를 멈출 필요 없음.
--   다시 실행하면 이미 있는 표에서 실패해 전체 ROLLBACK(반쪽 적용 없음).
-- 판정·사건은 읽기만 한다: 알림 대상은 API 주기 작업이 run_decision_events(006·012)·판정·사건(019)을 읽어 정한다(email-alerts.md "감지" 절).
--   주기 작업 직렬화 = pg_try_advisory_xact_lock(81743, 0), 시험 발송 직렬화 = pg_advisory_xact_lock(81743, 1)(infra/database/README.md advisory lock 분류).
-- 비밀: SMTP 비밀번호는 평문으로 저장하지 않는다. API가 AES-256-GCM으로 암호화한 문자열(v1.<키 id>.<IV>.<tag>.<암호문>, base64url)만 둔다.
--   암호화 키는 DB에 두지 않는다(API 환경 변수 SETTINGS_SECRET_KEY 또는 API 데이터 폴더의 키 파일, email-alerts.md "비밀번호 저장" 절).
-- 표:
--   ops_email_alert_settings    시스템 전역 메일 알림 설정 1행(id=1): 켜기·보낼 알림 종류·위험도 하한·받는 사람·SMTP·대시보드 주소·마지막 연결 확인 결과.
--   ops_email_alert_cursor      주기 작업 처리 경계 1행(id=1): 이 판정 이벤트 순번까지 판단을 마쳤다. 켤 때 그때의 최대 순번으로 옮긴다(지난 판정은 보내지 않음).
--   ops_email_alerts            알림 대상 1건(사건 흐름 또는 묶이지 않은 판정) = 1행. 알린 위험 단계(notified_rank)까지 보냈다.
--   ops_email_alert_incidents   사건 → 알림 대상. 흐름 머리가 바뀌어도(병합·이어짐) 같은 흐름을 다시 새 사건으로 보내지 않게 한다.
--   ops_email_deliveries        발송 1건(알림 대상·위험 단계·받는 사람) = 1행. 시험 발송도 여기 남는다(alert_id NULL).
BEGIN;

-- 1. 설정(1행). 형식 검사(주소·호스트 이름·대시보드 주소)는 API가 한다. DB는 값 범위와 켜기 전제만 막는다.
--    smtp_port: 표준 SMTP 포트만(25 relay, 465 암묵 TLS, 587 submission, 2525 대체 submission). 다른 포트로 내부 서비스를 두드리지 못하게 한다.
--    smtp_security: tls(접속부터 TLS) | starttls(STARTTLS 필수). 평문 SMTP는 없다.
--    smtp_changed_at: SMTP 값(호스트·포트·보안·계정·비밀번호·보내는 주소)이 바뀐 시각. 연결 확인 결과(transport_*)는 이 시각의 설정에 대한 것만 남는다.
CREATE TABLE ops_email_alert_settings (
 id smallint PRIMARY KEY CHECK (id = 1),
 enabled boolean NOT NULL,
 notify_incidents boolean NOT NULL,
 notify_decisions boolean NOT NULL,
 min_risk_level text NOT NULL CHECK (min_risk_level IN ('info','warning','critical')),
-- recipients: jsonb_array_length는 배열이 아니면 CHECK 위반이 아니라 22023 오류를 낸다(AND는 계산 순서를 보장하지 않음) → CASE로 배열일 때만 길이를 잰다.
 recipients jsonb NOT NULL CHECK (CASE WHEN jsonb_typeof(recipients) = 'array' THEN jsonb_array_length(recipients) <= 10 ELSE false END),
 smtp_host text CHECK (smtp_host <> '' AND length(smtp_host) <= 253),
 smtp_port integer CHECK (smtp_port IN (25,465,587,2525)),
 smtp_security text CHECK (smtp_security IN ('tls','starttls')),
 smtp_username text CHECK (smtp_username <> '' AND length(smtp_username) <= 320),
 smtp_password_enc text CHECK (length(smtp_password_enc) <= 3000
   AND smtp_password_enc ~ '^v1\.[0-9a-f]{8}\.[A-Za-z0-9_-]{16}\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]+$'),
 from_address text CHECK (from_address <> '' AND length(from_address) <= 254),
 public_base_url text CHECK (length(public_base_url) <= 512 AND public_base_url ~ '^https?://[^/?#]+(/[^?#]*)?$'),
 smtp_changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 transport_checked_at timestamptz,
 transport_result text CHECK (transport_result IN ('ok','auth_failed','error')),
 transport_error_code text CHECK (transport_error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
 updated_by_actor_id text NOT NULL CHECK (updated_by_actor_id <> '' AND length(updated_by_actor_id) <= 128),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT ops_email_alert_settings_password_check CHECK (smtp_password_enc IS NULL OR smtp_username IS NOT NULL),
 CONSTRAINT ops_email_alert_settings_transport_check CHECK ((transport_result IS NULL) = (transport_checked_at IS NULL)
   AND (transport_error_code IS NULL OR transport_result IN ('auth_failed','error'))),
 CONSTRAINT ops_email_alert_settings_enable_requires CHECK (NOT enabled OR (smtp_host IS NOT NULL AND smtp_port IS NOT NULL
   AND smtp_security IS NOT NULL AND from_address IS NOT NULL AND public_base_url IS NOT NULL
   AND CASE WHEN jsonb_typeof(recipients) = 'array' THEN jsonb_array_length(recipients) >= 1 ELSE false END AND (notify_incidents OR notify_decisions)))
);

-- 2. 처리 경계(1행). processed_through_seq = run_decision_events.id(006·012 identity, id 순서 = 커밋 순서).
CREATE TABLE ops_email_alert_cursor (
 id smallint PRIMARY KEY CHECK (id = 1),
 processed_through_seq bigint NOT NULL CHECK (processed_through_seq >= 0),
 armed_at timestamptz NOT NULL,
 last_tick_at timestamptz
);

-- 3. 알림 대상. alert_key = 'incident:<흐름 id>' | 'job:<detect 작업 id>' | 'decision:<작업 없는 판정 id>'(contracts/notifications.md 항목 키와 같은 형식).
--    notified_rank = 보낸 가장 높은 위험 단계(1 참고, 2 주의, 3 위험). 더 높은 단계가 오면 위험도 상승으로 한 번 더 보낸다.
CREATE TABLE ops_email_alerts (
 id uuid PRIMARY KEY,
 alert_key text NOT NULL CHECK (alert_key ~ '^(incident|job|decision):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),
 kind text NOT NULL CHECK (kind IN ('incident','decision')),
 first_seq bigint NOT NULL CHECK (first_seq > 0),
 last_seq bigint NOT NULL,
 notified_rank smallint NOT NULL CHECK (notified_rank BETWEEN 1 AND 3),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT ops_email_alerts_key_unique UNIQUE (alert_key),
 CONSTRAINT ops_email_alerts_kind_key_match CHECK ((kind = 'incident') = (alert_key LIKE 'incident:%')),
 CONSTRAINT ops_email_alerts_seq_check CHECK (last_seq >= first_seq)
);

-- 4. 사건 → 알림 대상(run_incidents FK 없음: 019 표를 잠그지 않는다). 알림 대상을 지우면(30일 정리) 함께 지워진다.
CREATE TABLE ops_email_alert_incidents (
 incident_id uuid PRIMARY KEY,
 alert_id uuid NOT NULL REFERENCES ops_email_alerts(id) ON DELETE CASCADE,
 added_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ops_email_alert_incidents_alert_idx ON ops_email_alert_incidents(alert_id);

-- 5. 발송. UNIQUE(alert_id, risk_rank, recipient): 같은 알림 대상의 같은 위험 단계는 한 사람에게 한 번만 대기열에 들어간다(재시작·동시 실행에도).
--    alert_id NULL = 시험 발송 또는 정리로 지워진 알림 대상(UNIQUE 대상 아님).
--    status: queued 대기 → sending 보내는 중(lease_until까지) → sent 보냄 | retrying 다시 시도 대기 | failed 실패
--            | unknown 확인 불가(보내는 중 멈춤, 다시 보내지 않음) | suppressed 발송 제한으로 생략.
--    payload = 메일에 넣을 사실 스냅숏(대기열에 넣을 때 고정, 원문 로그·비밀 없음).
CREATE TABLE ops_email_deliveries (
 id uuid PRIMARY KEY,
 alert_id uuid REFERENCES ops_email_alerts(id) ON DELETE SET NULL,
 kind text NOT NULL CHECK (kind IN ('incident','decision','test')),
 reason text NOT NULL CHECK (reason IN ('opened','risk_raised','test')),
 risk_rank smallint CHECK (risk_rank BETWEEN 1 AND 3),
 trigger_seq bigint CHECK (trigger_seq > 0),
 recipient text NOT NULL CHECK (recipient <> '' AND length(recipient) <= 254),
 payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 8192),
 status text NOT NULL CHECK (status IN ('queued','sending','sent','retrying','failed','unknown','suppressed')),
 attempts smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 next_attempt_at timestamptz,
 lease_until timestamptz,
 error_code text CHECK (error_code ~ '^[a-z][a-z0-9_]{0,63}$'),
 smtp_code smallint CHECK (smtp_code BETWEEN 200 AND 599),
 requested_by_actor_id text CHECK (requested_by_actor_id <> '' AND length(requested_by_actor_id) <= 128),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 sent_at timestamptz,
 finished_at timestamptz,
 CONSTRAINT ops_email_deliveries_once_key UNIQUE (alert_id, risk_rank, recipient),
 CONSTRAINT ops_email_deliveries_test_check CHECK ((kind = 'test') = (reason = 'test') AND (kind = 'test' OR (risk_rank IS NOT NULL AND trigger_seq IS NOT NULL))
   AND (kind <> 'test' OR requested_by_actor_id IS NOT NULL)),
 CONSTRAINT ops_email_deliveries_state_check CHECK ((status <> 'sending' OR lease_until IS NOT NULL)
   AND (status NOT IN ('queued','retrying') OR next_attempt_at IS NOT NULL)
   AND (status <> 'sent' OR sent_at IS NOT NULL))
);
-- 보낼 차례(주기 작업), 멈춘 발송 찾기, 최근 발송 목록·시간당 발송 수.
CREATE INDEX ops_email_deliveries_due_idx ON ops_email_deliveries(next_attempt_at) WHERE status IN ('queued','retrying');
CREATE INDEX ops_email_deliveries_lease_idx ON ops_email_deliveries(lease_until) WHERE status = 'sending';
CREATE INDEX ops_email_deliveries_recent_idx ON ops_email_deliveries(created_at DESC, id DESC);

COMMIT;
