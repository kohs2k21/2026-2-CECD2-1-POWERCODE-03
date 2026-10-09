import { featureCatalog } from "./catalog";
import type { CreateDraft } from "./createDraft";
import { compileFormula } from "./formulaCompiler";
import {
  canEditCanonicalFeature,
  defaultFeatureEditor,
  legacyEditorExpression,
} from "./formulaLegacy";
import type { FormulaFeature } from "./formulaTypes";

export const featureOptions = (draft: CreateDraft): FormulaFeature[] => {
  const catalog: FormulaFeature[] = featureCatalog.map((feature) => {
    const edit = draft.featureEdits[feature.id];
    const expression = edit
      ? (edit.expression ?? legacyEditorExpression(edit))
      : canEditCanonicalFeature(feature)
        ? legacyEditorExpression(defaultFeatureEditor(feature))
        : undefined;
    return {
      ...feature,
      expression,
      name: edit?.name || feature.name,
      timezone: edit?.timezone,
      missingPolicy: edit?.missingPolicy,
    };
  });
  const custom: FormulaFeature[] = draft.customFeatureIds.map((id) => {
    const edit = draft.featureEdits[id];
    return {
      id,
      name: edit?.name || "이름 없는 파생변수",
      source: "derived",
      type: "number",
      unit: edit?.unit || "unitless",
      readiness: edit ? "ready" : "unconfirmed",
      reason: edit ? "입력 검증 필요" : "정의가 없습니다.",
      expression: edit
        ? (edit.expression ?? legacyEditorExpression(edit))
        : undefined,
      timezone: edit?.timezone,
      missingPolicy: edit?.missingPolicy,
    };
  });
  const features = [...catalog, ...custom];
  return features.map((feature) => {
    if (feature.expression === undefined) return feature;
    const compiled = compileFormula(feature.expression, features);
    return {
      ...feature,
      type: compiled.outputType ?? feature.type,
      unit: compiled.unit,
      inputs: compiled.inputIds,
      fitRequired: Boolean(feature.fitRequired || compiled.fitRequired),
      readiness: compiled.errors.length
        ? "unconfirmed"
        : feature.readiness === "deferred" ||
            feature.readiness === "unconfirmed"
          ? feature.readiness
          : compiled.fitRequired
            ? "fit-required"
            : "ready",
      reason:
        compiled.errors[0] ??
        (compiled.fitRequired ? "학습 구간 fit 통계 필요" : feature.reason),
    } as FormulaFeature;
  });
};
export const featureDefinitionPayload = (draft: CreateDraft) => {
  const features = featureOptions(draft);
  return Object.fromEntries(
    Object.entries(draft.featureEdits).map(([id, editor]) => {
      const expression = editor.expression ?? legacyEditorExpression(editor);
      const compiled = compileFormula(expression, features);
      return [
        id,
        {
          ...editor,
          expression,
          compiled: {
            version: compiled.version,
            ast: compiled.ast,
            outputType: compiled.outputType,
            unit: compiled.unit,
            inputIds: compiled.inputIds,
            fitRequired: compiled.fitRequired,
          },
          errors: compiled.errors,
        },
      ];
    }),
  );
};
