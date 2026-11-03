import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";

globalThis.React = React;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
await import("react-hot-toast");
const storage = new Map();
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  addEventListener() {},
  removeEventListener() {},
  setTimeout,
  clearTimeout,
};
const { QueryClientProvider } = await import("@tanstack/react-query");
const {
  RUNNING_POLL_MS,
  accountQueryKey,
  createAppQueryClient,
  pollWhileRunning,
  queryDefaults,
  sessionBound,
  useAccountQuery,
} = await import("../src/lib/query.ts");
const { AuthSessionProvider, useAuthSession } = await import("../src/services/auth/AuthSessionProvider.tsx");
const { HttpError } = await import("../src/services/api/client.ts");

// 1) 순수 규칙
assert.deepEqual(accountQueryKey("ops", "u1", "a"), ["ops", "u1", "a"]);
assert.deepEqual(accountQueryKey("ops", null), ["ops", "anonymous"]);
assert.deepEqual(queryDefaults, { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false, refetchInterval: false });
const defaults = createAppQueryClient().getDefaultOptions().queries;
for (const [key, value] of Object.entries(queryDefaults)) assert.equal(defaults[key], value, `app default ${key}`);
assert.equal(RUNNING_POLL_MS, 5000);
const poll = pollWhileRunning((data) => data?.status === "running");
assert.equal(poll({ state: { data: { status: "running" } } }), 5000);
assert.equal(poll({ state: { data: { status: "succeeded" } } }), false);
assert.equal(poll({ state: { data: undefined } }), false);

storage.set("token", "first");
assert.equal(await sessionBound(async () => "ok"), "ok");
await assert.rejects(
  sessionBound(async () => {
    storage.set("token", "second");
    return "late";
  }),
  (error) => error.name === "AbortError",
  "a response that arrives after the token changed is discarded",
);
const aborted = new AbortController();
aborted.abort();
await assert.rejects(sessionBound(async () => "x", aborted.signal), (error) => error.name === "AbortError");
storage.delete("token");

// 2) 훅: 계정별 key · AbortSignal · 404 재시도 없음 · 관리자 전용 · 세션 변경 폐기 · 진행 중만 5초
const tick = () => act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
const wait = async (check, label) => {
  for (let i = 0; i < 60 && !check(); i++) await tick();
  assert.ok(check(), label);
};
const client = createAppQueryClient();
client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, gcTime: Infinity } });
let session;
const results = {};
const reads = { plain: [], missing: 0, admin: 0, running: 0 };
let releaseLate;
const Probe = () => {
  session = useAuthSession();
  results.plain = useAccountQuery({
    name: "plain",
    read: (signal) => {
      reads.plain.push(signal);
      if (session?.user?.id === "late-user") return new Promise((resolve) => (releaseLate = resolve));
      return Promise.resolve(`data:${storage.get("token")}`);
    },
  });
  results.missing = useAccountQuery({
    name: "missing",
    read: async () => {
      reads.missing += 1;
      throw new HttpError("not found", 404);
    },
  });
  results.admin = useAccountQuery({
    name: "admin-only",
    adminOnly: true,
    read: async () => {
      reads.admin += 1;
      return "secret";
    },
  });
  results.running = useAccountQuery({
    name: "job",
    read: async () => {
      reads.running += 1;
      return { status: "running" };
    },
    isRunning: (data) => data?.status === "running",
  });
  return null;
};
let renderer;
await act(async () => {
  renderer = create(
    React.createElement(QueryClientProvider, { client }, React.createElement(AuthSessionProvider, null, React.createElement(Probe))),
  );
});
assert.equal(reads.plain.length, 0, "no read before login");
const user = { id: "user-1", email: "u@example.test", userType: "user", createdAt: "2026-10-01T00:00:00Z" };
await act(async () => session.login({ token: "token-user", user }));
await wait(() => results.plain.data === "data:token-user", "plain read resolves for the account");
assert.deepEqual(results.plain.queryKey, ["plain", "user-1"], "query key includes the account id");
assert.ok(reads.plain[0] instanceof AbortSignal, "queryFn receives an AbortSignal");
await wait(() => results.missing.isError, "404 surfaces as an error");
await tick();
await tick();
assert.equal(reads.missing, 1, "404 is not retried");
assert.equal(results.missing.error.status, 404);
assert.equal(reads.admin, 0, "admin-only query stays disabled for a regular user");
assert.equal(results.admin.fetchStatus, "idle");
await wait(() => results.running.data?.status === "running", "running job loaded");
const jobObserver = client.getQueryCache().find({ queryKey: ["job", "user-1"] }).observers[0];
assert.equal(jobObserver.options.refetchInterval({ state: { data: { status: "running" } } }), 5000);
assert.equal(jobObserver.options.refetchInterval({ state: { data: { status: "failed" } } }), false);
const plainObserver = client.getQueryCache().find({ queryKey: ["plain", "user-1"] }).observers[0];
assert.equal(plainObserver.options.refetchInterval, false, "no automatic polling for ordinary reads");
assert.equal(plainObserver.options.refetchOnWindowFocus, false);
assert.equal(plainObserver.options.refetchOnReconnect, false);

// 계정 변경: 캐시 비움 → 새 계정 key로 다시 조회
const admin = { ...user, id: "admin-1", userType: "admin" };
await act(async () => session.login({ token: "token-admin", user: admin }));
assert.equal(client.getQueryData(["plain", "user-1"]), undefined, "previous account cache is cleared");
await wait(() => results.plain.data === "data:token-admin", "new account reads again");
await wait(() => reads.admin === 1, "admin-only query runs for an admin");

// 늦은 응답 폐기: 응답 도착 전에 계정이 바뀌면 이전 계정 결과를 쓰지 않는다
const lateUser = { ...user, id: "late-user" };
await act(async () => session.login({ token: "token-late", user: lateUser }));
await wait(() => typeof releaseLate === "function", "late read is pending");
await act(async () => session.login({ token: "token-next", user: { ...user, id: "next-user" } }));
await act(async () => releaseLate("stale-late-value"));
await wait(() => results.plain.data === "data:token-next", "current account result is shown");
assert.equal(client.getQueryData(["plain", "late-user"]), undefined, "late response of the previous account is not cached");

await act(async () => renderer.unmount());
client.clear();
console.log(
  "query rules QA passed: account keys, AbortSignal, no retry on 404, no window/reconnect/interval refetch, admin-only gating, cache reset on account change, late response discard, 5s polling only while running",
);
