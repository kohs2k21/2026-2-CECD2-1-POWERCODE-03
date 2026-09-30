import assert from "node:assert/strict";

const tokenStore = new Map([["token", "qa-frontend-token"]]);
globalThis.window = {
  location: { origin: "http://127.0.0.1:5173" },
  localStorage: {
    getItem: (key) => tokenStore.get(key) ?? null,
    setItem: (key, value) => tokenStore.set(key, value),
    removeItem: (key) => tokenStore.delete(key),
  },
};

const { AnomalyStreamError, consumeAnomalyStream } = await import(
  "../src/services/api/anomaly-stream.api.ts"
);

const event = {
  schemaVersion: 1,
  eventId: "qa-event-1",
  provenance: "backend-ingest",
  detectedAt: "2026-01-01T00:00:00.000Z",
  responseCode: "E123",
  anomalyScore: 0.5,
  processTimeMs: 1000,
  riskScore: 45,
  riskLevel: 2,
  severity: "Warning",
};

const responseHeaders = { "content-type": "text/event-stream" };

function chunkedResponse(chunks) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: responseHeaders });
}

let request;
let fetchCount = 0;
globalThis.fetch = async (url, options) => {
  fetchCount += 1;
  request = { url, options };
  const validFrame = `id: ${event.eventId}\nevent: anomaly\ndata: ${JSON.stringify(event)}\n\n`;
  const invalidFrame = 'event: anomaly\ndata: {"schemaVersion":1}\n\n';
  const splitAt = Math.floor(validFrame.length / 2);
  return chunkedResponse([
    validFrame.slice(0, splitAt),
    validFrame.slice(splitAt),
    invalidFrame,
  ]);
};

const received = [];
const invalidReasons = [];
await consumeAnomalyStream({
  signal: new AbortController().signal,
  onEvent: (receivedEvent) => received.push(receivedEvent),
  onInvalidEvent: (reason) => invalidReasons.push(reason),
});

assert.equal(request.url, "/api/anomaly/realtime-stream");
assert.equal(request.options.headers.Authorization, "Bearer qa-frontend-token");
assert.equal(request.options.headers.Accept, "text/event-stream");
assert.equal(received.length, 1, "one valid fragmented frame should be delivered");
assert.equal(received[0].eventId, event.eventId);
assert.equal(received[0].processName, undefined, "optional fields may be omitted");
assert.equal(invalidReasons.length, 1, "invalid frames should be reported and ignored");
assert.equal(fetchCount, 1, "the valid stream should issue one fetch");

tokenStore.delete("token");
await assert.rejects(
  () =>
    consumeAnomalyStream({
      signal: new AbortController().signal,
      onEvent: () => {},
    }),
  (error) => error instanceof AnomalyStreamError && error.status === 401,
  "a missing token must prevent opening the stream",
);
assert.equal(fetchCount, 1, "a missing token must not issue another fetch");
tokenStore.set("token", "qa-frontend-token");

const abortController = new AbortController();
let abortStreamController;
globalThis.fetch = async (_url, options) => {
  const stream = new ReadableStream({
    start(controller) {
      abortStreamController = controller;
      options.signal.addEventListener(
        "abort",
        () => controller.error(new DOMException("Aborted", "AbortError")),
        { once: true },
      );
    },
  });
  return new Response(stream, { status: 200, headers: responseHeaders });
};

const pending = consumeAnomalyStream({
  signal: abortController.signal,
  onEvent: () => {},
});
assert.ok(abortStreamController, "the stream reader should be opened before cancellation");
abortController.abort();
await assert.rejects(
  pending,
  (error) => error instanceof DOMException && error.name === "AbortError",
  "aborting the stream must stop consumption",
);

console.log("frontend realtime stream QA passed: Bearer auth, fragmented SSE, optional fields, invalid frame handling, and cancellation");
