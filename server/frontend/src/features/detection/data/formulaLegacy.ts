import type { DerivedOperation, FeatureEditorValue } from "./createDraft";
import type { FeatureDefinition } from "./types";
const canonicalOperations: Partial<Record<string, DerivedOperation>> = {
  F03: "weekend",
  F05: "ratio",
  F06: "ratio",
  F12: "log1p",
  F13: "isMissing",
  F23: "log1p",
  F24: "log1p",
  A00: "duration",
  A01: "month",
  A02: "weekday",
  A05: "hour",
  A12: "log1p",
};
export const canEditCanonicalFeature = (feature: FeatureDefinition) =>
  feature.source === "derived" && Boolean(canonicalOperations[feature.id]);
export const defaultFeatureEditor = (
  feature?: FeatureDefinition,
): FeatureEditorValue => ({
  name: feature?.name ?? "",
  operation: (feature && canonicalOperations[feature.id]) ?? "isMissing",
  left:
    feature?.id === "A00"
      ? "process.END_TIME"
      : feature?.id === "F12"
        ? "A00"
        : (feature?.inputs[0] ?? "process.END_TIME"),
  right:
    feature?.id === "A00"
      ? "process.START_TIME"
      : (feature?.inputs[1] ?? "process.START_TIME"),
  unit: feature?.id === "A00" ? "ms" : "unitless",
  missingPolicy: "preserve",
  timezone: "",
});
export const legacyEditorExpression = (editor: FeatureEditorValue): string => {
  const left = `[${editor.left}]`,
    right = `[${editor.right}]`;
  switch (editor.operation) {
    case "duration":
      return `${editor.unit === "s" ? "duration_s" : "duration_ms"}(${left}, ${right})`;
    case "subtract":
      return `${left} - ${right}`;
    case "ratio":
      return `${left} / ${right}`;
    case "isMissing":
      return `if(is_missing(${left}), 1, 0)`;
    case "trainMedian":
      return `train_median(${left})`;
    default:
      return `${editor.operation}(${left})`;
  }
};
