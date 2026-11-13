import { useAccountQuery } from "@/lib/query";
import { readCandidateDetail, readCandidateList } from "./candidateApi";

export const candidateListQueryName = "rule-candidates";
export const candidateDetailQueryName = "rule-candidate";

/** 최근 후보 작업 결과(인터페이스 하나). 인터페이스를 바꾸는 동안 직전 결과를 유지한다. 관리자 전용. */
export const useCandidateList = (interfaceId: string | null) =>
  useAccountQuery({
    name: candidateListQueryName, params: [interfaceId], adminOnly: true, keepPrevious: true,
    read: (signal) => readCandidateList(interfaceId, signal),
  });
export const useCandidateDetail = (candidateId: string) =>
  useAccountQuery({
    name: candidateDetailQueryName, params: [candidateId], adminOnly: true, enabled: Boolean(candidateId),
    read: (signal) => readCandidateDetail(candidateId, signal),
  });
