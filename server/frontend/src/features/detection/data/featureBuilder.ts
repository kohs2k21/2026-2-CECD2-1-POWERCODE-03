import { featureCatalog } from "./catalog";
import type { FeatureEditorValue, DerivedOperation } from "./createDraft";
import type { FeatureDefinition } from "./types";
export const operationLabels: Record<DerivedOperation, string> = {
  duration: "시간 차이",
  subtract: "수치 차이",
  ratio: "비율",
  log1p: "log1p",
  isMissing: "결측 여부",
  hour: "시간 추출",
  month: "월 추출",
  weekday: "요일 추출",
  weekend: "주말 여부",
  trainMedian: "학습 중앙값",
};
export const requiresRightInput = (operation: DerivedOperation) =>
  ["duration", "subtract", "ratio"].includes(operation);
export const allowedOperations = (
  feature: FeatureDefinition | undefined,
): DerivedOperation[] =>
  feature?.type === "timestamp"
    ? ["duration", "isMissing", "hour", "month", "weekday", "weekend"]
    : feature?.type === "number"
      ? ["subtract", "ratio", "log1p", "isMissing", "trainMedian"]
      : ["isMissing"];
export const defaultFeatureEditor = (
  feature?: FeatureDefinition,
): FeatureEditorValue => ({
  name: feature?.name ?? "",
  operation: feature?.fitRequired
    ? "trainMedian"
    : feature?.id === "A00"
      ? "duration"
      : feature?.id === "F05" || feature?.id === "F06"
        ? "ratio"
        : "isMissing",
  left: feature?.inputs[0] ?? "process.END_TIME",
  right: feature?.inputs[1] ?? "process.START_TIME",
  unit: feature?.id === "A00" ? "ms" : "unitless",
  missingPolicy: "preserve",
  timezone: "",
});
export const validateFeatureEditor = (
  editor: FeatureEditorValue,
  features: FeatureDefinition[] = featureCatalog,
) => {
  const errors: string[] = [];
  const left = features.find((feature) => feature.id === editor.left);
  const right = features.find((feature) => feature.id === editor.right);
  if (!editor.name.trim() || editor.name.trim().length > 80)
    errors.push("파생변수 이름은 1~80자로 입력해 주세요.");
  if (!left) errors.push("원천 속성을 선택해 주세요.");
  else if (!allowedOperations(left).includes(editor.operation))
    errors.push("선택한 속성 타입에서 허용되지 않는 연산입니다.");
  if (
    requiresRightInput(editor.operation) &&
    (!right || right.type !== left?.type)
  )
    errors.push("같은 타입의 두 번째 속성을 선택해 주세요.");
  if (editor.operation === "duration" && !["ms", "s"].includes(editor.unit))
    errors.push("시간 차이 단위는 ms 또는 s를 선택해 주세요.");
  if (
    left?.type === "timestamp" &&
    editor.operation !== "isMissing" &&
    !editor.timezone
  )
    errors.push("원천 시각의 기준 시간대를 선택해 주세요.");
  return errors;
};
export type PreviewResult = {
  value: number | null;
  reason: string;
  unit: string;
};
export const previewFeature = (
  editor: FeatureEditorValue,
  leftSample: string,
  rightSample: string,
): PreviewResult => {
  const dependency = featureCatalog.find(
    (feature) =>
      feature.id === editor.left &&
      (feature.fitRequired ||
        feature.readiness === "unconfirmed" ||
        feature.readiness === "deferred"),
  );
  if (dependency)
    return {
      value: null,
      reason: dependency.fitRequired
        ? "입력 피처의 학습된 전처리 통계가 없습니다."
        : dependency.reason,
      unit: editor.unit,
    };
  const invalid = validateFeatureEditor(editor);
  if (invalid.length)
    return { value: null, reason: invalid[0], unit: editor.unit };
  if (editor.operation === "trainMedian")
    return {
      value: null,
      reason:
        "학습된 전처리 통계가 없습니다. train에서 fit 후 확인할 수 있습니다.",
      unit: editor.unit,
    };
  if (editor.operation === "isMissing")
    return {
      value: leftSample.trim() === "" ? 1 : 0,
      reason: "검증 입력의 결측 여부",
      unit: "unitless",
    };
  if (
    !leftSample.trim() ||
    (requiresRightInput(editor.operation) && !rightSample.trim())
  )
    return {
      value: null,
      reason:
        editor.missingPolicy === "reject"
          ? "필수 입력 결측으로 계산 보류"
          : "원천 NULL은 NULL로 유지",
      unit: editor.unit,
    };
  if (
    ["duration", "hour", "month", "weekday", "weekend"].includes(
      editor.operation,
    )
  ) {
    if (!/(Z|[+-]\d{2}:\d{2})$/.test(leftSample))
      return {
        value: null,
        reason: "시각에 UTC Z 또는 시간대 offset이 필요합니다.",
        unit: editor.unit,
      };
    const left = Date.parse(leftSample),
      right = Date.parse(rightSample);
    if (
      !Number.isFinite(left) ||
      (editor.operation === "duration" &&
        (!/(Z|[+-]\d{2}:\d{2})$/.test(rightSample) || !Number.isFinite(right)))
    )
      return {
        value: null,
        reason: "유효하지 않은 원천 시각",
        unit: editor.unit,
      };
    if (editor.operation === "duration")
      return left < right
        ? {
            value: null,
            reason: "음수 처리시간은 계산 보류",
            unit: editor.unit,
          }
        : {
            value: (left - right) / (editor.unit === "s" ? 1000 : 1),
            reason: "검증 입력의 시간 차이",
            unit: editor.unit,
          };
    const date = new Date(
      left + (editor.timezone === "Asia/Seoul" ? 9 * 3600000 : 0),
    );
    const value =
      editor.operation === "hour"
        ? date.getUTCHours()
        : editor.operation === "month"
          ? date.getUTCMonth() + 1
          : editor.operation === "weekday"
            ? date.getUTCDay()
            : date.getUTCDay() === 0 || date.getUTCDay() === 6
              ? 1
              : 0;
    return {
      value,
      reason: "선택 시간대 기준 검증 입력 계산",
      unit: "unitless",
    };
  }
  const left = Number(leftSample),
    right = Number(rightSample);
  if (
    !Number.isFinite(left) ||
    (requiresRightInput(editor.operation) && !Number.isFinite(right))
  )
    return {
      value: null,
      reason: "유효하지 않은 수치 입력",
      unit: editor.unit,
    };
  if (editor.operation === "ratio" && right === 0)
    return {
      value: null,
      reason: "0 분모로 계산할 수 없습니다.",
      unit: editor.unit,
    };
  if (editor.operation === "log1p" && left < 0)
    return {
      value: null,
      reason: "log1p는 비음수 입력만 허용합니다.",
      unit: editor.unit,
    };
  const value =
    editor.operation === "subtract"
      ? left - right
      : editor.operation === "ratio"
        ? left / right
        : Math.log1p(left);
  return Number.isFinite(value)
    ? { value, reason: "검증 입력 계산", unit: editor.unit }
    : { value: null, reason: "계산 범위를 벗어났습니다.", unit: editor.unit };
};
