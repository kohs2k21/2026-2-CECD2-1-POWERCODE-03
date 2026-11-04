import { useAccountQuery } from "@/lib/query";
import { restoreRequest } from "./ruleDraft";
import { previewRuleSet, readRuleSet, readRuleSetHistory, readRuleSetRevision, readSoftRuleFields, type RuleSetState } from "./ruleSetApi";

export const useRuleSet = () => useAccountQuery({ name: "rule-set", read: readRuleSet, adminOnly: true, staleTime: 0 });
/** 변경 기록 첫 쪽. 리비전이 바뀌면 다시 읽는다. */
export const useRuleHistory = (revision: number) =>
  useAccountQuery({ name: "rule-set-history", params: [revision], read: (signal) => readRuleSetHistory(undefined, signal), adminOnly: true, staleTime: 0 });
/**
 * 그 버전으로 되돌려도 내용이 같은지. 정규 내용 비교는 서버 규칙(스냅샷에 없는 규칙·이전 methodVersion 해석)이 필요해 미리보기의 `rule_set_unchanged`로 판단한다.
 * 저장소 변경 없음. 판단이 필요한 행(같은 hash이거나 hash 버전이 다른 행)만 읽는다.
 */
export const useRestoreIsNoop = (revision: number, state: RuleSetState, enabled: boolean) =>
  useAccountQuery({
    name: "rule-set-restore-noop", params: [revision, state.revision], adminOnly: true, enabled, staleTime: Infinity,
    read: async () => {
      const preview = await previewRuleSet(restoreRequest(await readRuleSetRevision(revision), state, true));
      return !preview.valid && preview.error.code === "rule_set_unchanged";
    },
  });
export const useSoftRuleFields = () => useAccountQuery({ name: "soft-rule-fields", read: readSoftRuleFields, adminOnly: true });
