import { useDraftStore } from "../../../stores/draftStore";
import { newEvaluationPreparation } from "../data/evaluationPreparation";
import type { EvaluationPreparationDraft } from "../data/evaluationTypes";

const path = "/detection/evaluation";
export const useEvaluationPreparation = () => {
  const stored = useDraftStore((state) => state.drafts[path]?.value) as
    { preparation?: EvaluationPreparationDraft } | undefined;
  const draft = stored?.preparation ?? newEvaluationPreparation();
  const change = (patch: Partial<EvaluationPreparationDraft>) => {
    const current = useDraftStore.getState().drafts[path]?.value;
    const siblings =
      current && typeof current === "object"
        ? (current as Record<string, unknown>)
        : {};
    useDraftStore.getState().edit(path, {
      ...siblings,
      editor: "evaluation",
      preparation: {
        ...((siblings.preparation as EvaluationPreparationDraft) ??
          newEvaluationPreparation()),
        ...patch,
      },
    });
  };
  return { draft, change };
};
