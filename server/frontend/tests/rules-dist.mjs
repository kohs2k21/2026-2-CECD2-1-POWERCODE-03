import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// 운영 빌드 검사(`pnpm build` 마지막 단계): AI 규칙 추천 계약 예시 값·평가 결과 예시 데이터는 산출물에 없어야 하고, 화면 자체는 있어야 한다.
const dist = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../dist");
const files = (await readdir(dist, { recursive: true, withFileTypes: true })).filter((entry) => entry.isFile() && /\.(js|html|css|map)$/.test(entry.name));
assert.ok(files.length > 0, "dist has build output");
const contents = await Promise.all(files.map((entry) => readFile(path.join(entry.parentPath, entry.name), "utf8")));
const all = contents.join("\n");
for (const sample of ["The 95th percentile of 46,980", "successful process durations", "All reference values are 0", "soft:elapsed_ms-3f9a2c1d", "soft:error_ratio-5d2e0b19", "invalid_generation_output"]) {
  assert.ok(!all.includes(sample), `contract sample leaked into dist: ${sample}`);
}
for (const sample of ["esb-process-research-v1\",profileVersion", "ESB 합성 E1", "원본 검증 세트 10월", "4d2f7c10-6b1e-4e39-9a44-1c0e7b85d2a6"]) {
  assert.ok(!all.includes(sample), `evaluation sample leaked into dist: ${sample}`);
}
assert.ok(all.includes("recommend_thresholds"), "production build keeps the real request");
console.log("rules dist QA passed: contract recommendation sample is not in the production build");
