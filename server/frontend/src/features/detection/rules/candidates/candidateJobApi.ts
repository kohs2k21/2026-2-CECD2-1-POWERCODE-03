import { httpClient } from "@/services/api/client";

/**
 * 후보 작업 요청(contracts/rule-candidates-ui.md 6·6.1절): 요청할 수 있는 저장 데이터셋 목록과 작업 접수.
 * 형식이 틀리면 빈 목록으로 바꾸지 않고 오류로 던진다. 오류(400·403·409·503)는 httpClient가 HttpError로 던진다.
 */
export type CandidateSnapshot = { id: string; sourceId: string; createdAt: string; readyAt: string | null; from: string | null; until: string | null };
export type CandidateJobAccepted = { id: string; status: string; kind: string };

type Json = Record<string, unknown>;
const object = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown) => (typeof value === "string" && value ? value : null);

export const parseCandidateSnapshots = (value: unknown): CandidateSnapshot[] => {
  if (!object(value) || !Array.isArray(value.items)) throw new Error("저장 데이터셋 응답 형식 오류");
  return value.items.map((item) => {
    if (!object(item) || !str(item.id) || !str(item.sourceId) || !str(item.createdAt)) throw new Error("저장 데이터셋 응답 형식 오류");
    return { id: item.id as string, sourceId: item.sourceId as string, createdAt: item.createdAt as string, readyAt: str(item.readyAt), from: str(item.from), until: str(item.until) };
  });
};

export const readCandidateSnapshots = async (signal?: AbortSignal) =>
  parseCandidateSnapshots(await httpClient.get<unknown>("/api/rule-set/candidates/snapshots", { signal }));

/** 같은 확인창의 재전송은 같은 키(서버가 같은 작업 200을 돌려준다, 6절 멱등). 선택 키(제외 단계·패턴)는 보내지 않는다 — Trainer 기본값. */
export const requestCandidateJob = async (snapshotId: string, idempotencyKey: string): Promise<CandidateJobAccepted> => {
  const value = await httpClient.post<unknown>("/api/rule-set/candidates/jobs", { snapshotId }, { headers: { "Idempotency-Key": idempotencyKey } });
  if (!object(value) || !str(value.id) || !str(value.status)) throw new Error("후보 작업 응답 형식 오류");
  return { id: value.id as string, status: value.status as string, kind: str(value.kind) ?? "" };
};
