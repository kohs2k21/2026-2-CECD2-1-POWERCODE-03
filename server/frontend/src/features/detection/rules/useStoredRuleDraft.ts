import { useMemo } from "react";
import { paths } from "@/app/routes";
import { useDraftStore } from "@/stores/draftStore";
import { draftChangeCount, draftFromState, type DraftSoftRule, type RuleDraft } from "./ruleDraft";
import type { RuleSetState } from "./ruleSetApi";

export type SoftPatch = { key: string; patch: Partial<Pick<DraftSoftRule, "threshold" | "operator">> };

/**
 * 룰 설정 편집 초안(세션 초안 저장소 `paths.detectionRules`)을 룰 설정 밖에서 읽고 고친다 — 규칙 추천 화면의 AI 규칙 추천·수식 추천 [적용].
 * [적용]은 전과 같이 입력칸(초안)만 채우고 서버에 저장하지 않는다. 저장은 룰 설정 화면의 저장 줄에서 한다(룰 설정은 들어올 때 이 초안을 이어 쓴다).
 * 변경이 없으면 초안을 지운다(룰 설정 화면과 같은 규칙: 변경 있는 초안만 보관).
 */
export const useStoredRuleDraft = (state: RuleSetState | undefined) => {
  const stored = useDraftStore((store) => store.drafts[paths.detectionRules]);
  const saved = stored?.dirty ? (stored.value as RuleDraft) : null;
  const draft = useMemo(() => saved ?? (state ? draftFromState(state) : null), [saved, state]);
  const dirty = saved !== null && draftChangeCount(saved) > 0;
  const editSoft = (updates: SoftPatch[]) => {
    if (!state || updates.length === 0) return;
    const store = useDraftStore.getState();
    const current = store.drafts[paths.detectionRules];
    const base = current?.dirty ? (current.value as RuleDraft) : draftFromState(state);
    const next: RuleDraft = {
      ...base,
      soft: base.soft.map((rule) => {
        const update = updates.find((item) => item.key === rule.key);
        return update ? { ...rule, ...update.patch } : rule;
      }),
    };
    if (draftChangeCount(next) > 0) store.edit(paths.detectionRules, next);
    else store.discard(paths.detectionRules);
  };
  return { draft, dirty, editSoft };
};
