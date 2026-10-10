import { useDraftStore } from "../../../stores/draftStore";
import type { CompositionInput } from "../data/configuration";

export const compositionInputFrom = (
  value: unknown,
): CompositionInput | null => {
  if (!value || typeof value !== "object") return null;
  const input = value as Partial<CompositionInput>;
  return typeof input.name === "string" &&
    typeof input.modelArtifactId === "string" &&
    typeof input.explanationVersion === "string" &&
    Array.isArray(input.ruleVersionIds) &&
    input.ruleVersionIds.every((id) => typeof id === "string")
    ? {
        name: input.name,
        modelArtifactId: input.modelArtifactId,
        ruleVersionIds: [...input.ruleVersionIds],
        explanationVersion: input.explanationVersion,
      }
    : null;
};

export const useCompositionDraft = (
  path: string,
  initial: CompositionInput,
) => {
  const stored = useDraftStore((state) => state.drafts[path]?.value);
  const record =
    stored && typeof stored === "object"
      ? (stored as Record<string, unknown>)
      : {};
  const input = compositionInputFrom(record.composition) ?? initial;
  const replace = (next: CompositionInput) => {
    const current = useDraftStore.getState().drafts[path]?.value;
    const preserved =
      current && typeof current === "object"
        ? (current as Record<string, unknown>)
        : {};
    useDraftStore.getState().edit(path, {
      ...preserved,
      editor:
        preserved.editor ??
        (path === "/detection/evaluation"
          ? "evaluation"
          : "versions-composition"),
      composition: next,
    });
  };
  return {
    input,
    replace,
    change: (patch: Partial<CompositionInput>) =>
      replace({ ...input, ...patch }),
  };
};
