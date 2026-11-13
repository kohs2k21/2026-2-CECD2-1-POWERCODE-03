import assert from "node:assert/strict";

// 판정 SSE 공유 연결(decision-stream-hub): 같은 토큰 구독자 2개 → 연결(fetch) 1회, 모두 해제 → 연결 닫힘,
// 구독자별 커서 걸러 주기, 앞선 커서 구독자 → 그 커서부터 다시 연결, 끊김 → 3초 뒤 마지막 순번부터 재연결, 토큰 변경 → 멈춤.
const store = new Map([["token", "token-a"]]);
globalThis.window = {
  location: { origin: "http://local.test" },
  localStorage: { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: (key) => store.delete(key) },
};
const { subscribeDecisionStream, DECISION_STREAM_RETRY_MS } = await import("../src/services/api/decision-stream-hub.ts");

const encoder = new TextEncoder();
const connections = [];
globalThis.fetch = async (url, options) => {
  const target = new URL(String(url), "http://local.test");
  assert.equal(target.pathname, "/api/pipeline/events");
  let stream;
  const connection = {
    search: target.search,
    after: options.headers["Last-Event-ID"],
    token: options.headers.Authorization,
    signal: options.signal,
    send: (frame) => stream.enqueue(encoder.encode(frame)),
    end: () => stream.close(),
  };
  connections.push(connection);
  return new Response(
    new ReadableStream({
      start(controller) {
        stream = controller;
        options.signal.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
};
const decision = (id) => `id: ${id}\nevent: decision\ndata: ${JSON.stringify({ id: `d-${id}`, status: "complete", eventKind: "final" })}\n\n`;
const interim = (id) => `id: ${id}\nevent: interim\ndata: ${JSON.stringify({ eventKind: "first_anomaly", interimId: `i-${id}`, jobId: "j-1", attemptNo: 1 })}\n\n`;
const tick = (ms = 5) => new Promise((resolve) => setTimeout(resolve, ms));
const wait = async (check, label, tries = 100) => {
  for (let i = 0; i < tries && !check(); i++) await tick();
  assert.ok(check(), label);
};
const subscriber = (after) => {
  const log = { events: [], opened: 0, disconnects: [] };
  log.unsubscribe = subscribeDecisionStream({
    after,
    onEvent: (event, id) => log.events.push(`${event.kind}:${id}`),
    onOpen: () => (log.opened += 1),
    onDisconnect: (error) => log.disconnects.push(error),
  });
  return log;
};

// 1) 같은 토큰 구독자 2개 → fetch 1회(include=interims), 이미 열린 연결에 붙으면 바로 열림 알림, 구독자별 커서로 걸러 준다.
{
  const bell = subscriber("10");
  await wait(() => bell.opened === 1, "first subscriber sees the stream open");
  const page = subscriber("12");
  assert.equal(page.opened, 1, "joining an open connection reports open at once");
  await tick(20);
  assert.equal(connections.length, 1, "two subscribers share one connection");
  assert.equal(connections[0].search, "?include=interims");
  assert.equal(connections[0].after, "10", "connection starts at the earliest cursor");
  assert.equal(connections[0].token, "Bearer token-a");
  connections[0].send(decision(11) + interim(12) + decision(13));
  await wait(() => page.events.length === 1, "later subscriber receives events after its own cursor");
  assert.deepEqual(bell.events, ["decision:11", "interim:12", "decision:13"]);
  assert.deepEqual(page.events, ["decision:13"], "events at or before the subscriber cursor are not delivered");

  // 2) 하나 해제 → 연결 유지, 마지막 해제 → 연결 닫힘(abort). 해제 뒤 이벤트는 받지 않는다.
  page.unsubscribe();
  assert.equal(connections[0].signal.aborted, false, "connection stays while a subscriber remains");
  bell.unsubscribe();
  assert.equal(connections[0].signal.aborted, true, "last unsubscribe closes the connection");
  page.unsubscribe();
  await tick(20);
  assert.equal(connections.length, 1, "no reconnect after the last unsubscribe");
}

// 3) 연결 위치보다 앞선 커서로 새 구독자 → 그 커서부터 다시 연결(재생분은 기존 구독자에게 중복 없음).
{
  connections.length = 0;
  const bell = subscriber("20");
  await wait(() => bell.opened === 1, "bell open");
  connections[0].send(decision(21) + decision(22));
  await wait(() => bell.events.length === 2, "bell receives 21, 22");
  const page = subscriber("19");
  await wait(() => connections.length === 2, "older cursor restarts the shared connection");
  assert.equal(connections[0].signal.aborted, true, "previous connection closed");
  assert.equal(connections[1].after, "19", "restarted from the older cursor");
  await wait(() => page.opened === 1, "new subscriber sees the restarted stream open");
  connections[1].send(decision(20) + decision(21) + decision(22) + decision(23));
  await wait(() => page.events.length === 4, "older subscriber receives the replay");
  assert.deepEqual(page.events, ["decision:20", "decision:21", "decision:22", "decision:23"]);
  assert.deepEqual(bell.events, ["decision:21", "decision:22", "decision:23"], "replay is not delivered twice");

  // 4) 서버가 끊으면 구독자에게 재연결 대기를 알리고 3초 뒤 마지막 순번부터 재연결(연결은 여전히 1개).
  connections[1].end();
  await wait(() => bell.disconnects.length === 1 && page.disconnects.length === 1, "disconnect reported to every subscriber");
  assert.deepEqual(bell.disconnects, [null], "normal end has no error text");
  await tick(DECISION_STREAM_RETRY_MS - 500);
  assert.equal(connections.length, 2, "no reconnect before the retry delay");
  await wait(() => connections.length === 3, "reconnects after the retry delay", 200);
  assert.equal(connections[2].after, "23", "reconnect resumes after the last received event");
  await wait(() => bell.opened === 3 && page.opened === 2, "both subscribers see the reconnect open (bell also saw the restart)");

  // 5) 로그인 토큰이 바뀌면 이벤트를 넘기지 않고 연결을 멈춘다(재연결 없음). 새 토큰 구독은 새 연결.
  store.set("token", "token-b");
  connections[2].send(decision(24));
  await wait(() => connections[2].signal.aborted, "token change stops the old connection");
  assert.equal(bell.events.at(-1), "decision:23", "event after a token change is not delivered");
  const next = subscriber("30");
  await wait(() => connections.length === 4, "new token opens its own connection");
  assert.equal(connections[3].token, "Bearer token-b");
  await tick(DECISION_STREAM_RETRY_MS + 200);
  assert.equal(connections.length, 4, "stopped connection does not retry");
  bell.unsubscribe();
  page.unsubscribe();
  next.unsubscribe();
  assert.equal(connections[3].signal.aborted, true);
}
console.log("decision-stream-hub: ok");
