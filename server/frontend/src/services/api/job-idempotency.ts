const storageKey = "pipeline-job-requests-v1";
let requests: Record<string, string> = {};
let loaded = false;

function loadRequests(): void {
  if (loaded) return;
  // Once hydrated, memory must win over stale storage after a failed write or removal.
  loaded = true;
  try {
    const stored: unknown = JSON.parse(window.sessionStorage.getItem(storageKey) ?? "{}");
    if (stored && typeof stored === "object" && !Array.isArray(stored)) {
      requests = Object.fromEntries(Object.entries(stored).filter(([, value]) => typeof value === "string"));
    }
  } catch { /* Keep in-memory keys when storage is unavailable. */ }
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, canonical(item)]));
  }
  return value;
}

export function clearJobRequestKeys(): void {
  requests = {};
  loaded = true;
  try { window.sessionStorage.removeItem(storageKey); } catch { /* Storage may be disabled. */ }
}

export function forgetJobRequestKey(actorId: string, input: Record<string, unknown>): void {
  loadRequests();
  delete requests[JSON.stringify([actorId, canonical(input)])];
  try { window.sessionStorage.setItem(storageKey, JSON.stringify(requests)); } catch { /* Storage may be disabled. */ }
}

export function jobRequestKey(actorId: string, input: Record<string, unknown>, newExecution = false): string {
  loadRequests();
  const fingerprint = JSON.stringify([actorId, canonical(input)]);
  if (newExecution || !requests[fingerprint]) requests[fingerprint] = crypto.randomUUID();
  const key = requests[fingerprint];
  try { window.sessionStorage.setItem(storageKey, JSON.stringify(requests)); } catch { /* Keep the key for retries. */ }
  return key;
}
