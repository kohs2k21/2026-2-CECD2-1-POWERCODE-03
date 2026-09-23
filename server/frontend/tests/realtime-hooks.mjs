import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { useRealtimeAnomalies } from "../src/features/analysis/hooks/useRealtimeAnomalies.ts";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const tokenStore = new Map();
const timers = new Map();
let nextTimerId = 0;
globalThis.window = {
  location: { origin: "http://127.0.0.1:5173" },
  localStorage: {
    getItem: (key) => tokenStore.get(key) ?? null,
    setItem: (key, value) => tokenStore.set(key, value),
    removeItem: (key) => tokenStore.delete(key),
  },
  setTimeout: (callback, delay) => {
    const timerId = ++nextTimerId;
    timers.set(timerId, { callback, delay });
    return timerId;
  },
  clearTimeout: (timerId) => timers.delete(timerId),
};

const responseHeaders = { "content-type": "text/event-stream" };
const event = (eventId) => ({
  schemaVersion: 1,
  eventId,
  provenance: "backend-ingest",
  detectedAt: "2026-01-01T00:00:00.000Z",
  responseCode: "E123",
  anomalyScore: 0.5,
  processTimeMs: 1000,
  riskScore: 45,
  riskLevel: 2,
  severity: "Warning",
});

function eventResponse(events) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const currentEvent of events) {
        controller.enqueue(
          encoder.encode(
            `id: ${currentEvent.eventId}\nevent: anomaly\ndata: ${JSON.stringify(currentEvent)}\n\n`,
          ),
        );
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: responseHeaders });
}

function closedResponse() {
  return new Response(new ReadableStream({ start: (controller) => controller.close() }), {
    status: 200,
    headers: responseHeaders,
  });
}

function pendingResponse(signal, onController) {
  const stream = new ReadableStream({
    start(controller) {
      onController(controller);
      signal.addEventListener(
        "abort",
        () => controller.error(new DOMException("Aborted", "AbortError")),
        { once: true },
      );
    },
  });
  return new Response(stream, { status: 200, headers: responseHeaders });
}

async function flushUntil(predicate, label) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (predicate()) return;
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail(`timed out waiting for ${label}`);
}

let latestState;
function Probe({ enabled = true }) {
  latestState = useRealtimeAnomalies(enabled);
  return null;
}

const probeElement = () => React.createElement(Probe);
const delayedProbeElement = () => React.createElement(Probe, { enabled: true });

async function mountProbe(element = probeElement()) {
  let renderer;
  await act(async () => {
    renderer = create(element);
  });
  return renderer;
}

// A missing session token must not start a fetch from the hook.
tokenStore.delete("token");
let fetchCount = 0;
globalThis.fetch = async () => {
  fetchCount += 1;
  throw new Error("fetch must not run without a token");
};
latestState = undefined;
let renderer = await mountProbe();
assert.equal(fetchCount, 0, "missing token must prevent a hook fetch");
assert.equal(latestState.status, "idle");
await act(async () => renderer.unmount());

// An empty but valid stream is open as soon as the response is validated.
tokenStore.set("token", "qa-frontend-token");
let idleStreamController;
let idleStreamSignal;
globalThis.fetch = async (_url, options) => {
  fetchCount += 1;
  idleStreamSignal = options.signal;
  return pendingResponse(options.signal, (controller) => {
    idleStreamController = controller;
  });
};
latestState = undefined;
renderer = await mountProbe();
await act(async () => flushUntil(() => latestState.status === "open", "idle stream open"));
assert.equal(latestState.events.length, 0, "the idle stream should have no events yet");
await act(async () => renderer.unmount());
assert.equal(idleStreamSignal.aborted, true, "unmount must abort the active stream request");
assert.ok(idleStreamController, "the stream body should be pending before unmount");

// A delivered event survives automatic retry exhaustion and a manual retry;
// replaying its ID must not add a duplicate card.
timers.clear();
tokenStore.set("token", "qa-frontend-token");
const originalEvent = event("qa-manual-retry-keeps-event");
const secondEvent = event("qa-manual-retry-keeps-second-event");
let requestCount = 0;
globalThis.fetch = async () => {
  requestCount += 1;
  if (requestCount === 1) {
    return eventResponse([originalEvent, secondEvent]);
  }
  if (requestCount === 5) {
    return eventResponse([originalEvent]);
  }
  return closedResponse();
};
latestState = undefined;
renderer = await mountProbe();
await act(async () =>
  flushUntil(() => latestState.status === "retrying" && requestCount === 1, "first retry"),
);
assert.deepEqual(latestState.events.map(({ eventId }) => eventId), [originalEvent.eventId, secondEvent.eventId]);

async function runRetry(delay, expectedStatus, expectedRequests) {
  const timerEntry = [...timers.entries()].find(([, timer]) => timer.delay === delay);
  assert.ok(timerEntry, `a ${delay}ms reconnect should be scheduled`);
  timers.delete(timerEntry[0]);
  await act(async () => {
    timerEntry[1].callback();
    await flushUntil(() => requestCount === expectedRequests, `${delay}ms request`);
  });
  await act(async () =>
    flushUntil(() => latestState.status === expectedStatus, `${delay}ms status`),
  );
}

await runRetry(500, "retrying", 2);
await runRetry(1_000, "retrying", 3);
await runRetry(2_000, "closed", 4);
assert.equal(requestCount, 4, "automatic reconnects must stop after the configured budget");
assert.deepEqual(latestState.events.map(({ eventId }) => eventId), [originalEvent.eventId, secondEvent.eventId]);
assert.equal(timers.size, 0, "the retry budget should leave no scheduled reconnect");

await act(async () => latestState.retry());
await act(async () => flushUntil(() => requestCount === 5, "manual reconnect request"));
await act(async () =>
  flushUntil(
    () => latestState.status === "retrying",
    "manual reconnect after automatic retries close",
  ),
);
assert.deepEqual(
  latestState.events.map(({ eventId }) => eventId),
  [originalEvent.eventId, secondEvent.eventId],
  "manual retry must retain prior events and ignore the replayed first event ID",
);
await act(async () => renderer.unmount());
assert.equal(timers.size, 0, "unmount must cancel a scheduled retry timer");

// Unmounting a connection that is waiting on its body aborts that request.
timers.clear();
let unmountSignal;
globalThis.fetch = async (_url, options) => {
  unmountSignal = options.signal;
  return pendingResponse(options.signal, () => {});
};
latestState = undefined;
renderer = await mountProbe(delayedProbeElement());
await act(async () => flushUntil(() => latestState.status === "open", "pending stream open"));
await act(async () => renderer.unmount());
assert.equal(unmountSignal.aborted, true, "unmount must abort an open fetch body");
assert.equal(timers.size, 0, "an aborted stream must not schedule another reconnect");

console.log(
  "frontend realtime hook QA passed: missing-token fetch guard, idle open state, dedupe across manual retry, reconnect budget, retry cleanup, and abort on unmount",
);
