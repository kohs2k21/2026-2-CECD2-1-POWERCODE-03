import type { Pool, PoolClient } from 'pg';
import { EmailSettingsError, RECIPIENT_MAX, SMTP_PORTS, SMTP_SECURITY, type RiskLevel, type SettingsUpdate, type SmtpSecurity } from './config.js';
import { SecretBoxError, open, seal, sealedKeyId, type KeySource } from './secretBox.js';
import { SmtpError, type SmtpConfig } from './transport.js';

/**
 * 메일 알림 설정 저장·읽기(033 ops_email_alert_settings 1행, contracts/email-alerts.md "API" 절). 관리자만(라우터가 검사).
 * - 비밀번호는 쓰기 전용: 응답에는 passwordSet만. 저장은 secretBox 암호문만(키가 없으면 저장 실패 503 — 평문 저장·성공 대체 없음).
 * - 전송 상태(transport.status)는 저장된 설정에서 만든다: not_configured(호스트 없음) / invalid(값 빠짐·비밀번호를 풀 수 없음) /
 *   configured, 그 위에 마지막 연결 확인·발송 결과(auth_failed·error). 확인 결과는 그 결과를 낸 SMTP 설정(smtp_changed_at)일 때만 남는다.
 * - 켜기(꺼짐 → 켜짐): 처리 경계를 그때의 최대 판정 이벤트 순번으로 옮긴다(지난 판정은 보내지 않음). 끄기: 대기·다시 시도 대기 발송을 실패(email_alerts_disabled)로.
 *   받는 사람에서 빠진 주소의 대기 발송도 실패(email_recipient_removed). 저장은 주기 작업과 같은 잠금(81743, 0) 아래(한 회차와 섞이지 않게).
 */
type Queryable = Pick<Pool, 'query'>;
export const SCAN_LOCK = [81743, 0] as const;
export const TEST_LOCK = [81743, 1] as const;
/** 새 알림(사건·판정, 위험 단계별) 시간당 상한(사용자 11/21). 넘으면 발송 제한으로 생략(suppressed). */
export const NEW_ALERTS_PER_HOUR = 10;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

export type SettingsRow = {
  enabled: boolean; notify_incidents: boolean; notify_decisions: boolean; min_risk_level: RiskLevel; recipients: string[];
  smtp_host: string | null; smtp_port: number | null; smtp_security: SmtpSecurity | null; smtp_username: string | null; smtp_password_enc: string | null;
  from_address: string | null; public_base_url: string | null; smtp_changed_at: string; transport_checked_at: string | null;
  transport_result: 'ok' | 'auth_failed' | 'error' | null; transport_error_code: string | null; updated_by_actor_id: string; updated_at: string;
};
const COLUMNS = `enabled,notify_incidents,notify_decisions,min_risk_level,recipients,smtp_host,smtp_port,smtp_security,smtp_username,smtp_password_enc,
  from_address,public_base_url,${ISO('smtp_changed_at')} AS smtp_changed_at,${ISO('transport_checked_at')} AS transport_checked_at,transport_result,
  transport_error_code,updated_by_actor_id,${ISO('updated_at')} AS updated_at`;

export async function loadSettingsRow(db: Queryable, forUpdate = false): Promise<SettingsRow | null> {
  const { rows: [row] } = await db.query(`SELECT ${COLUMNS} FROM ops_email_alert_settings WHERE id=1${forUpdate ? ' FOR UPDATE' : ''}`);
  return (row as SettingsRow | undefined) ?? null;
}

export type TransportStatus = 'not_configured' | 'invalid' | 'configured' | 'auth_failed' | 'error';
export type TransportState = { status: TransportStatus; code: string | null; checkedAt: string | null };
type SmtpFields = Pick<SettingsRow, 'smtp_host' | 'smtp_port' | 'smtp_security' | 'smtp_username' | 'smtp_password_enc' | 'from_address'>;

/** 저장된 비밀번호를 지금 키로 풀 수 있는가(키 id만 비교, 풀지는 않음). 문제면 코드, 없거나 괜찮으면 null. */
function passwordProblem(fields: SmtpFields, keys: KeySource): string | null {
  if (fields.smtp_username && !fields.smtp_password_enc) return 'smtp_password_missing';
  if (!fields.smtp_password_enc) return null;
  try {
    const key = keys.load(false);
    if (!key) return 'email_secret_key_unavailable';
    return sealedKeyId(fields.smtp_password_enc) === key.id ? null : 'smtp_password_unreadable';
  } catch (error) {
    return error instanceof SecretBoxError ? error.code : 'email_secret_key_unavailable';
  }
}

/** 설정 값 → 전송 상태(확인 결과 덮기 전). */
function configState(fields: SmtpFields, keys: KeySource): TransportState {
  if (!fields.smtp_host) return { status: 'not_configured', code: null, checkedAt: null };
  if (fields.smtp_port === null || fields.smtp_security === null || !fields.from_address) return { status: 'invalid', code: 'smtp_settings_incomplete', checkedAt: null };
  const problem = passwordProblem(fields, keys);
  return problem ? { status: 'invalid', code: problem, checkedAt: null } : { status: 'configured', code: null, checkedAt: null };
}

export function transportState(row: SettingsRow | null, keys: KeySource): TransportState {
  if (!row) return { status: 'not_configured', code: null, checkedAt: null };
  const state = configState(row, keys);
  if (state.status !== 'configured' || !row.transport_result) return state;
  if (row.transport_result === 'ok') return { status: 'configured', code: null, checkedAt: row.transport_checked_at };
  return { status: row.transport_result, code: row.transport_error_code, checkedAt: row.transport_checked_at };
}
/** 보낼 수 있는 설정인가(확인 실패는 막지 않음 — 일시 장애일 수 있고, 보내면 실패가 그대로 기록된다). */
export const usable = (state: TransportState) => state.status === 'configured' || state.status === 'auth_failed' || state.status === 'error';

/** 보낼 때 SMTP 설정(비밀번호를 푼다). 설정·키 문제는 SmtpError(영구, 연결 확인 결과에는 남기지 않음). */
export function smtpConfigOf(row: SettingsRow | null, keys: KeySource): SmtpConfig {
  const state = row ? configState(row, keys) : { status: 'not_configured' as const, code: null };
  if (!row || state.status !== 'configured') throw new SmtpError(state.code ?? 'smtp_not_configured', false, null);
  let password: string | null = null;
  if (row.smtp_password_enc) {
    try {
      const key = keys.load(false);
      if (!key) throw new SecretBoxError('email_secret_key_unavailable');
      password = open(row.smtp_password_enc, key);
    } catch (error) {
      throw new SmtpError(error instanceof SecretBoxError ? error.code : 'smtp_password_unreadable', false, null);
    }
  }
  return { host: row.smtp_host!, port: row.smtp_port!, security: row.smtp_security!, username: row.smtp_username, password, fromAddress: row.from_address! };
}

/** 연결 확인·발송 결과를 남긴다(그 결과를 낸 SMTP 설정일 때만 — 그 사이 설정이 바뀌었으면 버림). */
export async function recordTransportCheck(db: Queryable, smtpChangedAt: string, result: 'ok' | 'auth_failed' | 'error', code: string | null) {
  await db.query(`UPDATE ops_email_alert_settings SET transport_checked_at=clock_timestamp(),transport_result=$1,transport_error_code=$2
    WHERE id=1 AND smtp_changed_at=$3::timestamptz`, [result, result === 'ok' ? null : code, smtpChangedAt]);
}

/** GET /api/email-alerts/settings 응답. */
export async function readEmailSettings(db: Queryable, keys: KeySource) {
  const row = await loadSettingsRow(db);
  const { rows: [extra] } = await db.query(`SELECT (SELECT coalesce(max(id),0)::text FROM run_decision_events) AS latest,
      (SELECT jsonb_build_object('seq',processed_through_seq::text,'armedAt',${ISO('armed_at')},'lastTickAt',${ISO('last_tick_at')}) FROM ops_email_alert_cursor WHERE id=1) AS cursor,
      (SELECT count(DISTINCT (alert_id,risk_rank))::integer FROM ops_email_deliveries
        WHERE kind<>'test' AND status<>'suppressed' AND created_at>clock_timestamp()-interval '1 hour') AS last_hour`);
  const cursor = extra.cursor as { seq: string; armedAt: string; lastTickAt: string | null } | null;
  return {
    enabled: row?.enabled ?? false,
    rules: {
      kinds: row ? [...(row.notify_incidents ? ['incident'] : []), ...(row.notify_decisions ? ['decision'] : [])] : ['incident'],
      minRiskLevel: row?.min_risk_level ?? 'critical',
    },
    recipients: row?.recipients ?? [],
    smtp: {
      host: row?.smtp_host ?? null, port: row?.smtp_port ?? null, security: row?.smtp_security ?? null, username: row?.smtp_username ?? null,
      passwordSet: Boolean(row?.smtp_password_enc), fromAddress: row?.from_address ?? null,
    },
    publicBaseUrl: row?.public_base_url ?? null,
    transport: transportState(row, keys),
    processing: { processedThroughSeq: cursor?.seq ?? null, latestSeq: extra.latest as string, armedAt: cursor?.armedAt ?? null, lastTickAt: cursor?.lastTickAt ?? null },
    rateLimit: { newAlertsPerHour: NEW_ALERTS_PER_HOUR, newAlertsLastHour: extra.last_hour as number },
    limits: { recipients: RECIPIENT_MAX, ports: [...SMTP_PORTS], security: [...SMTP_SECURITY] },
    isDefault: row === null,
    updatedAt: row?.updated_at ?? null,
    updatedBy: row?.updated_by_actor_id ?? null,
  };
}

/**
 * PUT /api/email-alerts/settings 저장(한 트랜잭션). 돌려주는 smtpChanged = 연결 확인이 필요한가(SMTP 값·비밀번호가 바뀜).
 * 오류: 켜려는데 보낼 수 없는 설정 409 email_transport_not_configured, 대시보드 주소 없음 400 public_base_url_required,
 * 비밀번호를 넣었는데 키가 없음·틀림 503 email_secret_key_unavailable | email_secret_key_invalid. 오류면 아무것도 바꾸지 않는다.
 */
export async function updateEmailSettings(pool: Pool, actorId: string, update: SettingsUpdate, keys: KeySource): Promise<{ smtpChanged: boolean }> {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock($1,$2)', [...SCAN_LOCK]);
    const old = await loadSettingsRow(client, true);
    const { smtp } = update;
    let passwordEnc: string | null;
    if (smtp.username === null || smtp.password.action === 'clear') passwordEnc = null;
    else if (smtp.password.action === 'set') {
      let key;
      try { key = keys.load(true); } catch (error) { throw new EmailSettingsError(error instanceof SecretBoxError ? error.code : 'email_secret_key_unavailable', 503); }
      if (!key) throw new EmailSettingsError('email_secret_key_unavailable', 503);
      passwordEnc = seal(smtp.password.value, key);
    } else passwordEnc = old?.smtp_username ? old.smtp_password_enc : null;
    const fields: SmtpFields = { smtp_host: smtp.host, smtp_port: smtp.port, smtp_security: smtp.security, smtp_username: smtp.username,
      smtp_password_enc: passwordEnc, from_address: smtp.fromAddress };
    const smtpChanged = !old || old.smtp_host !== fields.smtp_host || old.smtp_port !== fields.smtp_port || old.smtp_security !== fields.smtp_security
      || old.smtp_username !== fields.smtp_username || old.smtp_password_enc !== fields.smtp_password_enc || old.from_address !== fields.from_address;
    if (update.enabled) {
      if (!usable(configState(fields, keys))) throw new EmailSettingsError('email_transport_not_configured', 409);
      if (!update.publicBaseUrl) throw new EmailSettingsError('public_base_url_required');
    }
    await client.query(`INSERT INTO ops_email_alert_settings(id,enabled,notify_incidents,notify_decisions,min_risk_level,recipients,smtp_host,smtp_port,
        smtp_security,smtp_username,smtp_password_enc,from_address,public_base_url,updated_by_actor_id)
      VALUES(1,$1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)
      ON CONFLICT (id) DO UPDATE SET enabled=EXCLUDED.enabled,notify_incidents=EXCLUDED.notify_incidents,notify_decisions=EXCLUDED.notify_decisions,
        min_risk_level=EXCLUDED.min_risk_level,recipients=EXCLUDED.recipients,smtp_host=EXCLUDED.smtp_host,smtp_port=EXCLUDED.smtp_port,
        smtp_security=EXCLUDED.smtp_security,smtp_username=EXCLUDED.smtp_username,smtp_password_enc=EXCLUDED.smtp_password_enc,
        from_address=EXCLUDED.from_address,public_base_url=EXCLUDED.public_base_url,updated_by_actor_id=EXCLUDED.updated_by_actor_id,updated_at=clock_timestamp(),
        smtp_changed_at=CASE WHEN $14::boolean THEN clock_timestamp() ELSE ops_email_alert_settings.smtp_changed_at END,
        transport_checked_at=CASE WHEN $14::boolean THEN NULL ELSE ops_email_alert_settings.transport_checked_at END,
        transport_result=CASE WHEN $14::boolean THEN NULL ELSE ops_email_alert_settings.transport_result END,
        transport_error_code=CASE WHEN $14::boolean THEN NULL ELSE ops_email_alert_settings.transport_error_code END`,
    [update.enabled, update.kinds.incident, update.kinds.decision, update.minRiskLevel, JSON.stringify(update.recipients), smtp.host, smtp.port, smtp.security,
      smtp.username, passwordEnc, smtp.fromAddress, update.publicBaseUrl, actorId, smtpChanged]);
    if (update.enabled && !old?.enabled) {
      await client.query(`INSERT INTO ops_email_alert_cursor(id,processed_through_seq,armed_at)
        VALUES(1,(SELECT coalesce(max(id),0) FROM run_decision_events),clock_timestamp())
        ON CONFLICT (id) DO UPDATE SET processed_through_seq=EXCLUDED.processed_through_seq,armed_at=EXCLUDED.armed_at`);
    }
    if (!update.enabled && old?.enabled) {
      await client.query(`UPDATE ops_email_deliveries SET status='failed',error_code='email_alerts_disabled',finished_at=clock_timestamp()
        WHERE status IN ('queued','retrying')`);
    }
    await client.query(`UPDATE ops_email_deliveries SET status='failed',error_code='email_recipient_removed',finished_at=clock_timestamp()
      WHERE status IN ('queued','retrying') AND NOT (lower(recipient) = ANY($1::text[]))`, [update.recipients.map((address) => address.toLowerCase())]);
    await client.query('COMMIT');
    return { smtpChanged };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}
