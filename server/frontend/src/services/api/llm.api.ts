import { httpClient } from "./client";

/** LLM 생성 서비스 가용성(llm-recommendation-job.md '생성 서비스 가용성' 절): GET /api/llm/status. */
export type LlmStatus = { available: boolean; checkedAt: string; errorCode: string | null };

export const GENERATION_SERVICE_UNAVAILABLE = "generation_service_unavailable";

export function fetchLlmStatus(signal?: AbortSignal): Promise<LlmStatus> {
  return httpClient.get("/api/llm/status", { signal });
}
