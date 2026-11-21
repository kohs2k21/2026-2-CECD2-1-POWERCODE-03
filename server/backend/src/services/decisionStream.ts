import type { Response } from 'express';

type DecisionEvent = { eventId: string; [key: string]: unknown };
type Options = {
  after: string;
  /** 계정·email·role DB 재확인. authorizeMs 간격으로만 부른다. */
  authorize: () => Promise<boolean>;
  /** DB 없이 하는 토큰 만료 검사. 페이지 조회 전과 이벤트 송신 전마다 부른다. false면 stream을 닫는다. */
  tokenValid?: () => boolean;
  /** `after` 다음 이벤트를 커서 순서로 최대 `pageSize`건 읽는다. */
  readEvents: (after: string) => Promise<DecisionEvent[]>;
  /** SSE `event:` 이름. 없으면 decision(기존 소비자 형식). */
  eventName?: (event: DecisionEvent) => string;
  /** 조회 주기. */
  pollMs?: number;
  /** readEvents 한 번의 상한(조회 LIMIT과 같은 값). 꽉 차면 같은 주기 안에서 커서를 이어 바로 다시 읽는다. */
  pageSize?: number;
  /** 한 주기 안의 최대 조회 횟수. 한 연결이 이벤트 루프·DB를 독점하지 않게 한다. 남은 이벤트는 다음 주기에 이어 읽는다. */
  maxPagesPerTick?: number;
  /** 사용자·권한 재확인 간격. 조회 주기와 따로 둔다(0이면 조회마다 확인). */
  authorizeMs?: number;
  /** keepalive 주석 간격. */
  keepaliveMs?: number;
  drainTimeoutMs?: number;
  /** 간격 계산용 시계(기본 단조 시계 performance.now). 시험에서 주입한다. */
  now?: () => number;
};

export const DECISION_STREAM_POLL_MS = 250;
export const DECISION_STREAM_PAGE_SIZE = 100;
export const DECISION_STREAM_MAX_PAGES_PER_TICK = 20;
// 0.25초 주기 4번째(약 1초)에 확인되도록 타이머 지연 여유를 둔다. 이전(1초 주기마다 확인)보다 늦어지지 않는다.
export const DECISION_STREAM_AUTHORIZE_MS = 900;
export const DECISION_STREAM_KEEPALIVE_MS = 900;

export async function streamDecisionEvents(res: Response, options: Options): Promise<void> {
  // 벽시계가 거꾸로 가도 재확인·keepalive 간격이 멈추지 않게 단조 시계를 쓴다.
  const now = options.now ?? (() => performance.now());
  const pageSize = options.pageSize ?? DECISION_STREAM_PAGE_SIZE;
  const maxPages = Math.max(1, options.maxPagesPerTick ?? DECISION_STREAM_MAX_PAGES_PER_TICK);
  const authorizeMs = options.authorizeMs ?? DECISION_STREAM_AUTHORIZE_MS;
  const keepaliveMs = options.keepaliveMs ?? DECISION_STREAM_KEEPALIVE_MS;
  let after = options.after;
  let closed = false;
  let busy = false;
  let authorizedAt = -Infinity;
  let keepaliveAt = -Infinity;
  let timer: ReturnType<typeof setInterval> | undefined;
  const cancelled = new Set<() => void>();
  const writable = () => !closed && !res.destroyed && !res.writableEnded;
  const stop = () => {
    closed = true;
    clearInterval(timer);
    for (const cancel of cancelled) cancel();
  };
  const finish = () => {
    stop();
    if (!res.destroyed && !res.writableEnded) res.end();
  };
  res.on('close', stop);
  res.on('finish', stop);
  // Response errors are asynchronous and cannot be caught by poll's try/catch.
  res.on('error', () => { stop(); if (!res.destroyed) res.destroy(); });

  const write = async (frame: string): Promise<boolean> => {
    if (!writable()) return false;
    if (res.write(frame)) return writable();
    // false means buffered, not failed: wait for capacity instead of ending SSE.
    return new Promise<boolean>((resolve) => {
      let timeout: ReturnType<typeof setTimeout>;
      const complete = (ok: boolean) => {
        clearTimeout(timeout);
        res.off('drain', drained);
        cancelled.delete(cancel);
        resolve(ok);
      };
      const drained = () => complete(writable());
      const cancel = () => complete(false);
      cancelled.add(cancel);
      res.once('drain', drained);
      timeout = setTimeout(() => { finish(); }, options.drainTimeoutMs ?? 10_000);
      if (!writable()) cancel();
    });
  };

  // 토큰 만료: 페이지 조회·이벤트 송신마다 DB 없이 검사한다.
  const tokenValid = (): boolean => {
    if (options.tokenValid?.() ?? true) return true;
    finish();
    return false;
  };

  // 조회 전마다 부르되 DB 재확인은 authorizeMs가 지났을 때만 한다(확인 시작 시각 기준).
  const authorized = async (): Promise<boolean> => {
    if (!tokenValid()) return false;
    if (now() - authorizedAt < authorizeMs) return true;
    const checkedAt = now();
    const ok = await options.authorize();
    if (!writable()) return false;
    if (!ok) { finish(); return false; }
    authorizedAt = checkedAt;
    return true;
  };

  const poll = async () => {
    if (!writable() || busy) return;
    busy = true;
    try {
      for (let page = 0; page < maxPages; page++) {
        if (!await authorized()) return;
        const events = await options.readEvents(after);
        if (!writable()) return;
        for (const event of events) {
          if (!tokenValid()) return;
          if (!await write(`id: ${event.eventId}\nevent: ${options.eventName?.(event) ?? 'decision'}\ndata: ${JSON.stringify(event)}\n\n`)) return;
          after = event.eventId;
        }
        if (events.length < pageSize) break;
      }
      if (now() - keepaliveAt >= keepaliveMs && tokenValid()) {
        keepaliveAt = now();
        await write(': keepalive\n\n');
      }
    } catch {
      finish();
    } finally {
      busy = false;
    }
  };

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();
  if (!writable()) return;
  timer = setInterval(() => { void poll(); }, options.pollMs ?? DECISION_STREAM_POLL_MS);
  await poll();
}
