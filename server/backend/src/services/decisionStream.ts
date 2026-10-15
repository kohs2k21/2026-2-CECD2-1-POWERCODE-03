import type { Response } from 'express';

type DecisionEvent = { eventId: string; [key: string]: unknown };
type Options = {
  after: string;
  authorize: () => Promise<boolean>;
  readEvents: (after: string) => Promise<DecisionEvent[]>;
  pollMs?: number;
  drainTimeoutMs?: number;
};

export async function streamDecisionEvents(res: Response, options: Options): Promise<void> {
  let after = options.after;
  let closed = false;
  let busy = false;
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

  const poll = async () => {
    if (!writable() || busy) return;
    busy = true;
    try {
      const authorized = await options.authorize();
      if (!writable()) return;
      if (!authorized) { finish(); return; }
      const events = await options.readEvents(after);
      if (!writable()) return;
      for (const event of events) {
        if (!await write(`id: ${event.eventId}\nevent: decision\ndata: ${JSON.stringify(event)}\n\n`)) return;
        after = event.eventId;
      }
      await write(': keepalive\n\n');
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
  timer = setInterval(() => { void poll(); }, options.pollMs ?? 1000);
  await poll();
}
