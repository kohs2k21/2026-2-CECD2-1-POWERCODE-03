import type { Pool } from 'pg';
import { prune, sendDue, type SendDeps } from './outbox.js';
import { runScan } from './scan.js';

/**
 * 메일 알림 주기 작업(API 프로세스 안, contracts/email-alerts.md "주기 작업" 절). 30초마다: 감지(runScan) → 발송(sendDue), 하루 한 번 정리(prune).
 * 회차는 겹치지 않는다(앞 회차가 끝나야 다음). API 프로세스가 여럿이면 감지는 잠금(81743, 0)으로 한 곳만, 발송은 SKIP LOCKED로 행마다 한 곳만.
 * 실패는 코드 한 줄만 남긴다(같은 코드가 이어지면 다시 남기지 않음). SQL 원문·값·비밀번호는 남기지 않는다.
 */
export const RUNNER_INTERVAL_MS = 30_000;
const PRUNE_EVERY_MS = 24 * 60 * 60_000;

/** 로그에 남겨도 되는 코드만: PostgreSQL SQLSTATE(5자리) 또는 소문자 코드. 그 밖은 unexpected. */
export const safeCode = (error: unknown) => {
  const code = (error as { code?: unknown } | null)?.code;
  return typeof code === 'string' && (/^[0-9A-Z]{5}$/.test(code) || /^[a-z][a-z0-9_]{0,63}$/.test(code)) ? code : 'unexpected';
};

export type Runner = { tick(): Promise<void>; stop(): void };
export function startEmailAlertRunner({ database, deps, intervalMs = RUNNER_INTERVAL_MS, log = (line: string) => console.error(line) }:
  { database: () => Pool; deps: SendDeps; intervalMs?: number; log?: (line: string) => void }): Runner {
  let busy = false;
  let lastPrune = 0;
  let lastError: string | null = null;
  const tick = async () => {
    if (busy) return;
    busy = true;
    try {
      const pool = database();
      await runScan(pool);
      await sendDue(pool, deps);
      if (Date.now() - lastPrune >= PRUNE_EVERY_MS) { await prune(pool); lastPrune = Date.now(); }
      if (lastError !== null) { log('[email-alerts] tick recovered'); lastError = null; }
    } catch (error) {
      const code = safeCode(error);
      if (code !== lastError) log(`[email-alerts] tick failed: ${code}`);
      lastError = code;
    } finally {
      busy = false;
    }
  };
  const timer = setInterval(() => { void tick(); }, intervalMs);
  timer.unref();
  const first = setTimeout(() => { void tick(); }, Math.min(5_000, intervalMs));
  first.unref();
  return { tick, stop: () => { clearInterval(timer); clearTimeout(first); } };
}
