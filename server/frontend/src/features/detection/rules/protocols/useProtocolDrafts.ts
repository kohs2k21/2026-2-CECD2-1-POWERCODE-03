import { useAccountQuery } from "@/lib/query";
import { candidateJobRunning } from "../candidates/useCandidates";
import { readProtocolDrafts } from "../ruleSetApi";

export const protocolDraftsQueryName = "protocol-drafts";

/**
 * 관측 경로 초안(관리자). interfaceId null = 인터페이스 요약 목록, 값 = 그 인터페이스 경로까지. enabled false면 읽지 않는다(상세는 단추를 눌렀을 때만).
 * 최근 후보 작업(`latestJob`)이 대기·실행 중이면 5초마다 다시 읽는다 — 끝나 성공하면 그 결과의 초안이 들어온다(규칙 추천 목록과 같은 진행 중 조회 규칙).
 */
export const useProtocolDrafts = (interfaceId: string | null, enabled = true) =>
  useAccountQuery({
    name: protocolDraftsQueryName, params: [interfaceId], adminOnly: true, enabled,
    read: (signal) => readProtocolDrafts(interfaceId, signal),
    isRunning: (data) => candidateJobRunning(data?.latestJob?.status),
  });
