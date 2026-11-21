import crypto from 'node:crypto';
import type { Pool, PoolClient } from 'pg';
import type { KeySource } from './secretBox.js';
import { TEST_LOCK, loadSettingsRow, recordTransportCheck, smtpConfigOf, transportState, usable, type SettingsRow } from './settingsStore.js';
import { renderMessage, type DeliveryPayload } from './template.js';
import { SmtpError, classifySmtpError, type MailTransport, type TransportFactory } from './transport.js';

/**
 * 발송(contracts/email-alerts.md "발송" 절). 대기열(ops_email_deliveries)에서 보낼 차례인 행을 FOR UPDATE SKIP LOCKED로 가져와
 * sending·lease_until을 커밋한 뒤 보낸다(API 프로세스가 여럿이어도 한 행은 한 곳만).
 * - 성공 sent. 일시 실패는 1·5·15·60분 뒤 다시(최대 MAX_ATTEMPTS번), 그 뒤·영구 실패는 failed. 코드만 남긴다(서버 응답 원문 없음).
 * - 보내는 중 멈춘 행(lease 지남 — 프로세스 종료 등)은 unknown(확인 불가)으로 바꾸고 다시 보내지 않는다(사용자 11/21: 중복 없음, 누락 가능).
 * - 연결 단계 실패(접속·TLS·인증)면 그 묶음의 남은 행도 같은 결과로 두고 멈춘다(같은 실패를 행마다 시간 제한까지 기다리지 않게).
 * - 연결 확인 결과(설정 transport_*)를 발송 결과로 갱신한다: 성공 ok, 인증 실패 auth_failed, 연결·TLS 오류 error. 받는 사람 거절은 남기지 않는다.
 * - 설정이 꺼져 있으면 보내지 않는다(끌 때 대기 행은 실패로 바뀐다, settingsStore).
 */
export const MAX_ATTEMPTS = 5;
export const RETRY_DELAYS_MS = [60_000, 5 * 60_000, 15 * 60_000, 60 * 60_000] as const;
export const CLAIM_LIMIT = 10;
/** 한 묶음(최대 10건 × 소켓 20초)이 끝나기 전에 lease가 지나지 않게. */
export const LEASE_SECONDS = 300;
export const TEST_INTERVAL_SECONDS = 30;
export const TEST_TIMEOUT_MS = 20_000;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

export type Outcome = { status: 'sent'; smtpCode: number | null } | { status: 'retrying' | 'failed'; error: SmtpError; delayMs: number | null };
/** 실패 → 다음 상태. attempts = 이번 시도까지 센 수. */
export function failureOutcome(error: SmtpError, attempts: number): Outcome {
  if (error.transient && attempts < MAX_ATTEMPTS) return { status: 'retrying', error, delayMs: RETRY_DELAYS_MS[Math.min(attempts, RETRY_DELAYS_MS.length) - 1] };
  return { status: 'failed', error, delayMs: null };
}

async function finalize(db: Pick<Pool, 'query'>, id: string, outcome: Outcome) {
  const error = outcome.status === 'sent' ? null : outcome.error;
  await db.query(`UPDATE ops_email_deliveries SET status=$2,error_code=$3,smtp_code=$4,lease_until=NULL,
      next_attempt_at=CASE WHEN $2='retrying' THEN clock_timestamp()+($5::integer*interval '1 millisecond') END,
      sent_at=CASE WHEN $2='sent' THEN clock_timestamp() END,
      finished_at=CASE WHEN $2 IN ('sent','failed') THEN clock_timestamp() END
    WHERE id=$1 AND status='sending'`,
  [id, outcome.status, error?.code ?? null, outcome.status === 'sent' ? outcome.smtpCode : error?.smtpCode ?? null, outcome.status === 'sent' ? null : outcome.delayMs]);
}

/** 보내는 중 멈춘 행 → unknown. 반환 = 바꾼 수. */
export async function recoverStale(db: Pick<Pool, 'query'>) {
  const { rowCount } = await db.query(`UPDATE ops_email_deliveries SET status='unknown',error_code='send_interrupted',lease_until=NULL,finished_at=clock_timestamp()
    WHERE status='sending' AND lease_until<clock_timestamp()`);
  return rowCount ?? 0;
}

const transportOutcome = async (db: Pick<Pool, 'query'>, settings: SettingsRow, error: SmtpError | null) => {
  if (error === null) await recordTransportCheck(db, settings.smtp_changed_at, 'ok', null);
  else if (error.transport) await recordTransportCheck(db, settings.smtp_changed_at, error.transport, error.code);
};

export type SendDeps = { factory: TransportFactory; keys: KeySource };
export type SendSummary = { recovered: number; sent: number; retrying: number; failed: number };

/** 주기 작업의 발송 단계. */
export async function sendDue(pool: Pool, { factory, keys }: SendDeps, limit = CLAIM_LIMIT): Promise<SendSummary> {
  const summary: SendSummary = { recovered: await recoverStale(pool), sent: 0, retrying: 0, failed: 0 };
  const settings = await loadSettingsRow(pool);
  if (!settings?.enabled) return summary;
  const { rows } = await pool.query(`WITH due AS (SELECT id FROM ops_email_deliveries WHERE status IN ('queued','retrying') AND next_attempt_at<=clock_timestamp()
      ORDER BY next_attempt_at,id LIMIT $1 FOR UPDATE SKIP LOCKED)
    UPDATE ops_email_deliveries d SET status='sending',attempts=d.attempts+1,lease_until=clock_timestamp()+($2::integer*interval '1 second')
    FROM due WHERE d.id=due.id RETURNING d.id,d.recipient,d.payload,d.attempts`, [limit, LEASE_SECONDS]);
  if (!rows.length) return summary;
  const count = (outcome: Outcome) => { summary[outcome.status === 'sent' ? 'sent' : outcome.status] += 1; };
  const failAll = async (from: number, error: SmtpError) => {
    for (const row of rows.slice(from)) { const outcome = failureOutcome(error, row.attempts); await finalize(pool, row.id, outcome); count(outcome); }
  };
  let transport: MailTransport | null = null;
  try {
    let config;
    try { config = smtpConfigOf(settings, keys); } catch (error) { await failAll(0, classifySmtpError(error)); return summary; }
    try { transport = await factory(config); } catch (error) {
      const failure = classifySmtpError(error);
      await failAll(0, failure);
      await transportOutcome(pool, settings, failure);
      return summary;
    }
    let lastTransport: SmtpError | null | undefined;
    for (const [index, row] of rows.entries()) {
      const message = renderMessage(row.payload as DeliveryPayload, { to: row.recipient, baseUrl: settings.public_base_url, deliveryId: row.id, fromAddress: config.fromAddress });
      try {
        const sent = await transport.send(message);
        const outcome: Outcome = { status: 'sent', smtpCode: sent.smtpCode };
        await finalize(pool, row.id, outcome); count(outcome);
        lastTransport = null;
      } catch (error) {
        const failure = classifySmtpError(error);
        const outcome = failureOutcome(failure, row.attempts);
        await finalize(pool, row.id, outcome); count(outcome);
        if (failure.transport) { lastTransport = failure; await failAll(index + 1, failure); break; }
      }
    }
    if (lastTransport !== undefined) await transportOutcome(pool, settings, lastTransport);
    return summary;
  } finally {
    transport?.close();
  }
}

export class TestSendError extends Error {
  constructor(readonly code: string, readonly status: number) { super(code); }
}
const withTimeout = <T>(promise: Promise<T>, ms: number) => {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([promise, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new SmtpError('smtp_timeout', true, 'error')), ms); })])
    .finally(() => clearTimeout(timer));
};

/**
 * POST /api/email-alerts/test(관리자): 저장된 받는 사람 1명에게만 시험 메일 1통을 바로 보낸다(다른 주소로 보내는 통로가 되지 않게).
 * 전체 30초에 1번(잠금 81743, 1 아래 확인). 결과는 발송 기록(kind test)에 남고, 성공·실패를 그대로 돌려준다(성공 대체 없음).
 */
export async function sendTest(pool: Pool, actorId: string, to: string, { factory, keys }: SendDeps) {
  const settings = await loadSettingsRow(pool);
  const recipient = settings?.recipients.find((address) => address.toLowerCase() === to.toLowerCase());
  if (!settings || !recipient) throw new TestSendError('email_recipient_not_listed', 404);
  if (!usable(transportState(settings, keys))) throw new TestSendError('email_transport_not_configured', 409);
  const id = crypto.randomUUID();
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock($1,$2)', [...TEST_LOCK]);
    const { rowCount } = await client.query(`SELECT 1 FROM ops_email_deliveries WHERE kind='test' AND created_at>clock_timestamp()-($1::integer*interval '1 second') LIMIT 1`,
      [TEST_INTERVAL_SECONDS]);
    if (rowCount) throw new TestSendError('email_test_rate_limited', 429);
    await client.query(`INSERT INTO ops_email_deliveries(id,kind,reason,recipient,payload,status,attempts,lease_until,requested_by_actor_id)
      VALUES($1,'test','test',$2,'{"type":"test"}','sending',1,clock_timestamp()+($3::integer*interval '1 second'),$4)`, [id, recipient, LEASE_SECONDS, actorId]);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
  let outcome: Outcome;
  let transport: MailTransport | null = null;
  try {
    const config = smtpConfigOf(settings, keys);
    transport = await factory(config);
    const message = renderMessage({ type: 'test' }, { to: recipient, baseUrl: settings.public_base_url, deliveryId: id, fromAddress: config.fromAddress });
    const sent = await withTimeout(transport.send(message), TEST_TIMEOUT_MS);
    outcome = { status: 'sent', smtpCode: sent.smtpCode };
    await transportOutcome(pool, settings, null);
  } catch (error) {
    const failure = classifySmtpError(error);
    outcome = { status: 'failed', error: failure, delayMs: null };
    await transportOutcome(pool, settings, failure);
  } finally {
    transport?.close();
  }
  await finalize(pool, id, outcome);
  const { rows: [row] } = await pool.query(`SELECT status,error_code,smtp_code,${ISO('finished_at')} AS finished_at FROM ops_email_deliveries WHERE id=$1`, [id]);
  return { deliveryId: id, status: row.status as 'sent' | 'failed' | 'unknown', errorCode: row.error_code as string | null, smtpCode: row.smtp_code as number | null,
    finishedAt: row.finished_at as string | null };
}

/**
 * 저장 직후 연결 확인(PUT 응답 verify). SMTP 값이 바뀌었고 보낼 수 있는 설정일 때만, 짧은 시간 제한으로 접속·TLS·로그인까지 해 본다(메일은 보내지 않음).
 * 결과는 연결 확인 기록에 남긴다. 실패해도 저장한 설정은 그대로 둔다(일시 장애일 수 있고, 관리자가 고칠 값을 다시 넣지 않아도 되게).
 */
export async function verifyTransport(pool: Pool, { factory, keys }: SendDeps, timeoutMs = TEST_TIMEOUT_MS) {
  const settings = await loadSettingsRow(pool);
  const state = transportState(settings, keys);
  if (!settings || !usable(state)) return { result: 'skipped' as const, code: state.code };
  let transport: MailTransport | null = null;
  try {
    transport = await factory(smtpConfigOf(settings, keys));
    await withTimeout(transport.verify(), timeoutMs);
    await transportOutcome(pool, settings, null);
    return { result: 'ok' as const, code: null };
  } catch (error) {
    const failure = classifySmtpError(error);
    await transportOutcome(pool, settings, failure.transport ? failure : new SmtpError(failure.code, failure.transient, 'error'));
    return { result: failure.transport === 'auth_failed' ? 'auth_failed' as const : 'error' as const, code: failure.code };
  } finally {
    transport?.close();
  }
}

/** 하루 한 번 정리: 90일 지난 발송 행, 30일 갱신 없는 알림 대상(매핑 함께, 발송 행은 alert_id NULL로 남음). */
export async function prune(pool: Pick<Pool, 'query'>) {
  const deliveries = await pool.query(`DELETE FROM ops_email_deliveries WHERE created_at<clock_timestamp()-interval '90 days' AND status NOT IN ('queued','retrying','sending')`);
  const alerts = await pool.query(`DELETE FROM ops_email_alerts WHERE updated_at<clock_timestamp()-interval '30 days'`);
  return { deliveries: deliveries.rowCount ?? 0, alerts: alerts.rowCount ?? 0 };
}
