import { useAccountQuery } from "@/lib/query";
import { readCandidateDetail, readCandidateList } from "./candidateApi";
import { readCandidateSnapshots } from "./candidateJobApi";

export const candidateListQueryName = "rule-candidates";
export const candidateDetailQueryName = "rule-candidate";
export const candidateSnapshotsQueryName = "rule-candidate-snapshots";

/** 후보 작업이 대기·실행 중인가(이때만 목록을 5초마다 다시 읽는다 — 끝나면 새 결과가 목록에 들어온다). */
export const candidateJobRunning = (status: string | null | undefined) => status === "queued" || status === "running";

/** 최근 후보 작업 결과(인터페이스 하나). 인터페이스를 바꾸는 동안 직전 결과를 유지한다. 관리자 전용. */
export const useCandidateList = (interfaceId: string | null) =>
  useAccountQuery({
    name: candidateListQueryName, params: [interfaceId], adminOnly: true, keepPrevious: true,
    read: (signal) => readCandidateList(interfaceId, signal),
    isRunning: (data) => candidateJobRunning(data?.latestJob?.status),
  });
export const useCandidateDetail = (candidateId: string) =>
  useAccountQuery({
    name: candidateDetailQueryName, params: [candidateId], adminOnly: true, enabled: Boolean(candidateId),
    read: (signal) => readCandidateDetail(candidateId, signal),
  });
/** 후보 작업을 요청할 수 있는 저장 데이터셋(확인창을 열 때만 읽는다). */
export const useCandidateSnapshots = (enabled: boolean) =>
  useAccountQuery({ name: candidateSnapshotsQueryName, adminOnly: true, enabled, staleTime: 0, read: readCandidateSnapshots });
