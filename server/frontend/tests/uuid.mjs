// randomUuid: HTTP 접속(비보안 컨텍스트, crypto.randomUUID 없음)에서도 getRandomValues로 v4 UUID를 만든다.
import assert from "node:assert/strict";

const { randomUuid } = await import("../src/lib/uuid.ts");
const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
// 서버 Idempotency-Key 허용 형식(backend jobRoutes·comparisonRoutes).
const KEY = /^[a-zA-Z0-9_.:-]{1,128}$/;

assert.match(randomUuid(), V4, "secure context uses crypto.randomUUID");

Object.defineProperty(globalThis.crypto, "randomUUID", { value: undefined, configurable: true });
assert.equal(typeof globalThis.crypto.randomUUID, "undefined");
const keys = Array.from({ length: 50 }, () => randomUuid());
for (const key of keys) {
  assert.match(key, V4, "fallback emits RFC 4122 v4");
  assert.match(key, KEY, "fallback fits the server idempotency key format");
}
assert.equal(new Set(keys).size, keys.length, "fallback values differ");
delete globalThis.crypto.randomUUID;

console.log("uuid QA passed: randomUUID path, getRandomValues fallback v4 format and distinct values without randomUUID");
