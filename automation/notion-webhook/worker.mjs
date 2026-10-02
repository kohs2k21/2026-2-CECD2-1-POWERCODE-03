// No payload, caller-selected target, token, or upstream body is logged/forwarded.
const DISPATCH_URL = "https://api.github.com/repos/kohs2k21/2026-2-CECD2-1-POWERCODE-03/actions/workflows/notion-sync.yml/dispatches";

function reply(status, code, headers = {}) {
  return Response.json({ code }, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

async function matchesSecret(provided, expected) {
  if (!provided || provided.length > 256) return false;
  const encoder = new TextEncoder();
  const hashes = await Promise.all([provided, expected].map(value =>
    crypto.subtle.digest("SHA-256", encoder.encode(value))));
  const left = new Uint8Array(hashes[0]), right = new Uint8Array(hashes[1]);
  let mismatch = 0;
  for (let i = 0; i < left.length; i++) mismatch |= left[i] ^ right[i];
  return mismatch === 0;
}

export function createHandler({ fetcher = fetch, timeoutMs = 10000 } = {}) {
  return async (request, env) => {
    if (new URL(request.url).pathname !== "/notion-sync") return reply(404, "NOT_FOUND");
    if (request.method !== "POST") return reply(405, "POST_REQUIRED", { Allow: "POST" });
    if (typeof env.NOTION_RELAY_SECRET !== "string" || env.NOTION_RELAY_SECRET.length < 32
        || typeof env.GITHUB_DISPATCH_TOKEN !== "string" || !env.GITHUB_DISPATCH_TOKEN) {
      return reply(503, "RELAY_NOT_CONFIGURED");
    }
    if (!await matchesSecret(request.headers.get("X-Notion-Relay-Secret"), env.NOTION_RELAY_SECRET)) {
      return reply(401, "RELAY_AUTH_REQUIRED");
    }
    // Dispatch every authenticated request. Actions concurrency and the sync
    // worker reconcile duplicates; debounce must not silently lose new work.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetcher(DISPATCH_URL, {
        method: "POST", redirect: "error", signal: controller.signal,
        headers: {
          Authorization: "Bearer " + env.GITHUB_DISPATCH_TOKEN,
          Accept: "application/vnd.github+json", "Content-Type": "application/json",
          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "powercode-notion-relay",
        },
        body: JSON.stringify({ ref: "main" }),
      });
      if (response.status === 200 || response.status === 204) return reply(202, "WORKFLOW_DISPATCHED");
      if (response.status === 401 || response.status === 403) return reply(502, "GITHUB_ACCESS_REJECTED");
      if (response.status === 429) return reply(503, "GITHUB_RATE_LIMITED");
      return reply(502, "GITHUB_DISPATCH_REJECTED");
    } catch {
      return reply(controller.signal.aborted ? 504 : 502,
        controller.signal.aborted ? "GITHUB_DISPATCH_TIMEOUT" : "GITHUB_DISPATCH_UNCERTAIN");
    } finally {
      clearTimeout(timer);
    }
  };
}

export default { fetch: createHandler() };
