import http from 'node:http';
import https from 'node:https';

/**
 * LLM 생성 서비스 가용성(llm-recommendation-job.md '생성 서비스 가용성' 절).
 * - 확인: `GET ${LLM_HEALTH_URL:-http://llm:8010/health}`, 제한 1.5초. 가용 = HTTP 200이고 본문 `status`가 `"ready"`.
 *   연결 거부·DNS 실패·시간 초과·200 아님·본문 형식 틀림은 모두 꺼짐이다.
 * - llm_worker는 모델을 적재한 뒤에야 HTTP를 띄우고 주 소비자가 끝나면 프로세스도 끝나므로 /health 200 = 모델 적재 + 소비자 살아 있음.
 *   적재 중에는 /health가 없어 꺼짐으로 본다.
 * - 성공·실패 결과 모두 10초 캐시. 확인 중에 들어온 요청은 같은 확인(in-flight promise)을 기다린다(확인은 한 번만).
 */
export const GENERATION_SERVICE_UNAVAILABLE = 'generation_service_unavailable';
/** LLM이 소비하는 작업 종류. 프로토콜 설명 등 새 LLM 작업 종류는 여기에만 더한다(접수 거절 대상). */
export const LLM_JOB_KINDS: ReadonlySet<string> = new Set(['explain', 'recommend_thresholds', 'explain_protocol']);
export const DEFAULT_LLM_HEALTH_URL = 'http://llm:8010/health';
export const HEALTH_TIMEOUT_MS = 1_500;
export const AVAILABILITY_CACHE_MS = 10_000;
/** /health 본문은 `{"status":"ready"}` 한 줄이다. 이보다 크면 형식이 틀린 것으로 본다. */
const MAX_HEALTH_BODY_BYTES = 4_096;

export type GenerationAvailability = { available: boolean; checkedAt: string };
export type HealthProbe = (url: string, timeoutMs: number) => Promise<boolean>;

const ready = (body: unknown) =>
  typeof body === 'object' && body !== null && !Array.isArray(body) && (body as { status?: unknown }).status === 'ready';

/** /health 한 번 확인. 어떤 실패도 throw하지 않고 false다. 제한 시간은 연결·응답 본문까지 전체에 적용한다. */
export const probeHealth: HealthProbe = (url, timeoutMs) => new Promise((resolve) => {
  let target: URL;
  try { target = new URL(url); } catch { resolve(false); return; }
  const client = target.protocol === 'http:' ? http : target.protocol === 'https:' ? https : null;
  if (!client) { resolve(false); return; }
  let settled = false;
  let request: http.ClientRequest | undefined;
  const finish = (value: boolean) => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    resolve(value);
    request?.destroy();
  };
  const timer = setTimeout(() => finish(false), timeoutMs);
  try {
    request = client.get(target, { headers: { Accept: 'application/json' } }, (response) => {
      // 3xx도 200이 아니므로 꺼짐이다(따라가지 않음).
      if (response.statusCode !== 200) { response.resume(); finish(false); return; }
      const chunks: Buffer[] = [];
      let size = 0;
      response.on('data', (chunk: Buffer) => {
        size += chunk.length;
        if (size > MAX_HEALTH_BODY_BYTES) { finish(false); return; }
        chunks.push(chunk);
      });
      response.on('end', () => {
        try { finish(ready(JSON.parse(Buffer.concat(chunks).toString('utf8')))); } catch { finish(false); }
      });
      response.on('error', () => finish(false));
      response.on('aborted', () => finish(false));
    });
    request.on('error', () => finish(false));
  } catch {
    finish(false);
  }
});

export type AvailabilityOptions = { url: string; timeoutMs?: number; cacheMs?: number; now?: () => number; probe?: HealthProbe };

export function createGenerationAvailability({
  url, timeoutMs = HEALTH_TIMEOUT_MS, cacheMs = AVAILABILITY_CACHE_MS, now = Date.now, probe = probeHealth,
}: AvailabilityOptions) {
  let cached: { value: GenerationAvailability; expiresAt: number } | null = null;
  let inflight: Promise<GenerationAvailability> | null = null;
  return {
    check(): Promise<GenerationAvailability> {
      if (cached && now() < cached.expiresAt) return Promise.resolve(cached.value);
      if (inflight) return inflight;
      inflight = probe(url, timeoutMs)
        .catch(() => false)
        .then((available) => {
          const checkedAt = now();
          const value = { available, checkedAt: new Date(checkedAt).toISOString() };
          // 캐시는 확인이 끝난 시각부터 센다(가용·꺼짐 모두).
          cached = { value, expiresAt: checkedAt + cacheMs };
          return value;
        })
        .finally(() => { inflight = null; });
      return inflight;
    },
  };
}

let shared: ReturnType<typeof createGenerationAvailability> | null = null;
/** 프로세스 공용 확인기. 주소는 처음 쓸 때 환경 변수에서 읽는다(빈 값은 기본값, compose `${LLM_HEALTH_URL:-…}`와 같음). */
export function generationAvailability() {
  shared ??= createGenerationAvailability({ url: process.env.LLM_HEALTH_URL || DEFAULT_LLM_HEALTH_URL });
  return shared;
}
