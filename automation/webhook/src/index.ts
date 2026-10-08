// This endpoint wakes the existing whole-board reconciler; request content is never forwarded.
export interface Work {
  receiptId: string;
  acceptedAt: string;
  runId?: number;
  pollCount?: number;
  recoveries?: number;
}
export interface Env {
  WEBHOOK_ENABLED?: string;
  NOTION_WEBHOOK_SECRET?: string;
  GITHUB_DISPATCH_TOKEN?: string;
  REQUESTS: Queue<Work>;
  DEAD_LETTER: Queue<Work>;
}
export const LIMITS = { bodyBytes: 65536, polls: 120, recoveries: 1, attempts: 5, ageMs: 7200000, timeoutMs: 10000 } as const;
const BASE = "https://api.github.com/repos/kohs2k21/2026-2-CECD2-1-POWERCODE-03/actions";
const DISPATCH = `${BASE}/workflows/notion-sync.yml/dispatches`;

function response(status: number, code: string, receiptId?: string): Response {
  return Response.json({ status: code, ...(receiptId ? { receiptId } : {}) }, { status });
}
function enabled(env: Env): boolean {
  return env.WEBHOOK_ENABLED === "true" && Boolean(env.NOTION_WEBHOOK_SECRET && env.GITHUB_DISPATCH_TOKEN);
}
async function sameSecret(actual: string, expected: string): Promise<boolean> {
  if (actual.length > 1024) return false;
  const digest = (value: string) => crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  const [left, right] = await Promise.all([digest(actual), digest(expected)]);
  return crypto.subtle.timingSafeEqual(left, right);
}
async function validBody(request: Request): Promise<number> {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) return 415;
  if (Number(request.headers.get("content-length")) > LIMITS.bodyBytes) return 413;
  if (!request.body) return 400;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > LIMITS.bodyBytes) { await reader.cancel(); return 413; }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const body: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes));
    return body !== null && typeof body === "object" && !Array.isArray(body) ? 200 : 400;
  } catch { return 400; }
}
export async function receive(request: Request, env: Env): Promise<Response> {
  if (new URL(request.url).pathname !== "/notion") return response(404, "not_found");
  if (request.method !== "POST") return response(405, "method_not_allowed");
  if (!enabled(env)) return response(503, "disabled");
  if (!await sameSecret(request.headers.get("X-Webhook-Secret") ?? "", env.NOTION_WEBHOOK_SECRET!)) return response(401, "unauthorized");
  const bodyStatus = await validBody(request);
  if (bodyStatus !== 200) return response(bodyStatus, "invalid_body");
  const work: Work = { receiptId: crypto.randomUUID(), acceptedAt: new Date().toISOString() };
  try { await env.REQUESTS.send(work); }
  catch { return response(503, "queue_unavailable"); }
  return response(202, "queued", work.receiptId);
}
function isWork(value: unknown): value is Work {
  if (!value || typeof value !== "object") return false;
  const w = value as Work;
  const counter = (n: number | undefined) => n === undefined || Number.isSafeInteger(n) && n >= 0;
  return typeof w.receiptId === "string" && /^[a-f0-9-]{36}$/.test(w.receiptId)
    && typeof w.acceptedAt === "string" && Number.isFinite(Date.parse(w.acceptedAt))
    && (w.runId === undefined || Number.isSafeInteger(w.runId) && w.runId > 0)
    && counter(w.pollCount) && counter(w.recoveries);
}
// Rebuild the envelope so unexpected keys cannot persist into subsequent messages.
function clean(w: Work): Work {
  return { receiptId: w.receiptId, acceptedAt: w.acceptedAt,
    ...(w.runId ? { runId: w.runId } : {}), ...(w.pollCount ? { pollCount: w.pollCount } : {}),
    ...(w.recoveries ? { recoveries: w.recoveries } : {}) };
}
async function deadLetter(message: Message<Work>, env: Env, code: string): Promise<void> {
  const work = isWork(message.body) ? clean(message.body) : { receiptId: message.id, acceptedAt: new Date().toISOString() };
  await env.DEAD_LETTER.send(work);
  console.warn(JSON.stringify({ event: "dead_letter", receiptId: work.receiptId, code }));
  message.ack();
}
async function retry(message: Message<Work>, env: Env, code: string, delay?: number): Promise<void> {
  if (message.attempts >= LIMITS.attempts) { await deadLetter(message, env, code); return; }
  const seconds = delay === undefined ? Math.min(300, 30 * 2 ** (message.attempts - 1)) : Math.max(30, Math.ceil(delay));
  const remaining = isWork(message.body) ? (LIMITS.ageMs - (Date.now() - Date.parse(message.body.acceptedAt))) / 1000 : 0;
  if (seconds >= remaining || seconds > 86400) { await deadLetter(message, env, "retry_after_budget_exhausted"); return; }
  console.warn(JSON.stringify({ event: "retry", code, attempt: message.attempts,
    receiptId: message.body.receiptId, delaySeconds: seconds }));
  message.retry({ delaySeconds: seconds });
}
async function enqueue(message: Message<Work>, env: Env, work: Work, delaySeconds: number): Promise<void> {
  await env.REQUESTS.send(clean(work), { delaySeconds });
  message.ack();
}
async function github(url: string, env: Env, method: "POST" | "GET"): Promise<Response> {
  return fetch(url, {
    method, redirect: "error", signal: AbortSignal.timeout(LIMITS.timeoutMs),
    headers: { "Authorization": `Bearer ${env.GITHUB_DISPATCH_TOKEN}`, "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "powercode-notion-webhook", "Content-Type": "application/json" },
    ...(method === "POST" ? { body: JSON.stringify({ ref: "main" }) } : {})
  });
}
function transient(result: Response): boolean {
  return result.status === 429 || result.status >= 500 || result.status === 403 &&
    (result.headers.has("retry-after") || result.headers.get("x-ratelimit-remaining") === "0");
}
function retryAfter(result: Response): number | undefined {
  const value = result.headers.get("retry-after");
  if (value) {
    const seconds = /^\d+$/.test(value) ? Number(value) : Math.ceil((Date.parse(value) - Date.now()) / 1000);
    if (Number.isFinite(seconds) && seconds >= 0) return seconds;
  }
  if (result.headers.get("x-ratelimit-remaining") === "0") {
    const reset = Number(result.headers.get("x-ratelimit-reset"));
    if (Number.isFinite(reset) && reset > 0) return Math.max(0, Math.ceil(reset - Date.now() / 1000));
  }
  return undefined;
}
export async function consume(message: Message<Work>, env: Env): Promise<void> {
  if (!isWork(message.body)) { await deadLetter(message, env, "invalid_envelope"); return; }
  const work = clean(message.body);
  const age = Date.now() - Date.parse(work.acceptedAt);
  if (age > LIMITS.ageMs || age < -60000 || (work.pollCount ?? 0) >= LIMITS.polls || (work.recoveries ?? 0) > LIMITS.recoveries) {
    await deadLetter(message, env, "budget_exhausted"); return;
  }
  if (!enabled(env)) { await retry(message, env, "disabled"); return; }
  let result: Response;
  try { result = await github(work.runId ? `${BASE}/runs/${work.runId}` : DISPATCH, env, work.runId ? "GET" : "POST"); }
  catch (error) {
    const code = error instanceof TypeError ? "github_network_error"
      : error instanceof DOMException && error.name === "TimeoutError" ? "github_timeout" : "github_unavailable";
    await retry(message, env, code); return;
  }
  if (transient(result)) { await retry(message, env, "github_transient", retryAfter(result)); return; }
  if (result.status !== 200) { await deadLetter(message, env, "github_rejected"); return; }
  let data: { workflow_run_id?: number; id?: number; status?: string; conclusion?: string | null };
  try {
    const parsed: unknown = await result.json();
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("invalid response");
    data = parsed;
  }
  catch { await deadLetter(message, env, "github_invalid_response"); return; }
  if (!work.runId) {
    if (!Number.isSafeInteger(data.workflow_run_id) || data.workflow_run_id! <= 0) {
      // Dispatch may already have been accepted; avoid blind redispatch after an unrecognized response.
      await deadLetter(message, env, "dispatch_run_id_missing"); return;
    }
    await enqueue(message, env, { ...work, runId: data.workflow_run_id }, 30);
    console.info(JSON.stringify({ event: "dispatched", receiptId: work.receiptId, runId: data.workflow_run_id }));
    return;
  }
  if (data.id !== work.runId) { await deadLetter(message, env, "run_id_mismatch"); return; }
  if (data.status === "completed") {
    if (data.conclusion === "success") {
      console.info(JSON.stringify({ event: "completed", receiptId: work.receiptId, runId: work.runId }));
      message.ack(); return;
    }
    if (["failure", "cancelled", "timed_out", "startup_failure"].includes(data.conclusion ?? "") && (work.recoveries ?? 0) < LIMITS.recoveries) {
      await enqueue(message, env, { receiptId: work.receiptId, acceptedAt: work.acceptedAt, recoveries: (work.recoveries ?? 0) + 1, pollCount: work.pollCount }, 60);
      return;
    }
    await deadLetter(message, env, "run_unsuccessful"); return;
  }
  if (!["queued", "in_progress", "waiting", "pending", "requested"].includes(data.status ?? "")) {
    await deadLetter(message, env, "run_status_unknown"); return;
  }
  await enqueue(message, env, { ...work, pollCount: (work.pollCount ?? 0) + 1 }, 60);
}
export default {
  fetch: receive,
  async queue(batch: MessageBatch<Work>, env: Env): Promise<void> {
    for (const message of batch.messages) {
      try { await consume(message, env); }
      catch {
        console.warn(JSON.stringify({ event: "queue_processing_error", attempt: message.attempts }));
        message.retry({ delaySeconds: 60 });
      }
    }
  }
} satisfies ExportedHandler<Env, Work>;
