import { useDraftStore } from "../../../stores/draftStore";
import type { EvaluationCondition } from "../data/types";

type EvaluationDraft = {
  editor: "evaluation";
  conditionsByCandidate: Record<string, EvaluationCondition>;
};
const draftPath = "/detection/evaluation";

export const useEvaluationConditions = (
  candidateId: string,
  initial: EvaluationCondition,
) => {
  const stored = useDraftStore((state) => state.drafts[draftPath]?.value);
  const draft =
    stored &&
    typeof stored === "object" &&
    "editor" in stored &&
    stored.editor === "evaluation"
      ? (stored as EvaluationDraft)
      : undefined;
  const condition = draft?.conditionsByCandidate?.[candidateId] ?? initial;
  const change = (key: keyof EvaluationCondition, value: string) => {
    const current = useDraftStore.getState().drafts[draftPath]?.value;
    const preserved = current && typeof current === "object" ? current : {};
    useDraftStore.getState().edit(draftPath, {
      ...preserved,
      editor: "evaluation",
      conditionsByCandidate: {
        ...draft?.conditionsByCandidate,
        [candidateId]: { ...condition, [key]: value },
      },
    } satisfies EvaluationDraft);
  };
  return { condition, change };
};
