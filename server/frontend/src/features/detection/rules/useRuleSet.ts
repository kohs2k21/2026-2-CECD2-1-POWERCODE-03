import { useAccountQuery } from "@/lib/query";
import { readRuleSet, readRuleSetHistory, readRuleSetRevision, readSoftRuleFields } from "./ruleSetApi";

export const useRuleSet = () => useAccountQuery({ name: "rule-set", read: readRuleSet, adminOnly: true, staleTime: 0 });
/** 변경 기록 첫 쪽. 리비전이 바뀌면 다시 읽는다. */
export const useRuleHistory = (revision: number) =>
  useAccountQuery({ name: "rule-set-history", params: [revision], read: (signal) => readRuleSetHistory(undefined, signal), adminOnly: true, staleTime: 0 });
/** 이력 스냅샷(불변). 같은 hash인 행의 이름 비교에만 읽는다. */
export const useRuleRevision = (revision: number, enabled: boolean) =>
  useAccountQuery({ name: "rule-set-revision", params: [revision], read: () => readRuleSetRevision(revision), adminOnly: true, enabled, staleTime: Infinity });
export const useSoftRuleFields = () => useAccountQuery({ name: "soft-rule-fields", read: readSoftRuleFields, adminOnly: true });
