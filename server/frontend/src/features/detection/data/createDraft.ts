import type { RuleDefinition } from "./types";
import { featureCatalog } from "./catalog";
import { useDraftStore } from "../../../stores/draftStore";
export type DerivedOperation =
  | "duration"
  | "subtract"
  | "ratio"
  | "log1p"
  | "isMissing"
  | "hour"
  | "month"
  | "weekday"
  | "weekend"
  | "trainMedian";
export type FeatureEditorValue = {
  expression?: string;
  name: string;
  operation: DerivedOperation;
  left: string;
  right: string;
  unit: string;
  missingPolicy: "preserve" | "reject";
  timezone: string;
};
export type TrainingConfig = {
  modelId: string;
  featureIds: string[];
  trees: string;
  contamination: string;
  seed: string;
};
export type CreateDraft = {
  editor: "create";
  name: string;
  snapshotId: string;
  start: string;
  end: string;
  selectedFeatureIds: string[];
  featureEdits: Record<string, FeatureEditorValue>;
  customFeatureIds: string[];
  customFeatureSequence?: number;
  training: TrainingConfig;
  trainingByModel: Record<string, TrainingConfig>;
  rules: Record<string, RuleDefinition>;
  ruleThresholdInputs: Record<string, string>;
  recommendationDecisions: Record<string, "accepted" | "rejected">;
};
export const createDefaultDraft = (): CreateDraft => ({
  editor: "create",
  name: "새 탐지 구성",
  snapshotId: "",
  start: "",
  end: "",
  selectedFeatureIds: featureCatalog
    .filter((feature) => feature.defaultSelected)
    .map((feature) => feature.id),
  featureEdits: {},
  customFeatureIds: [],
  training: {
    modelId: "isolation-forest",
    featureIds: featureCatalog
      .filter((feature) => feature.defaultSelected)
      .map((feature) => feature.id),
    trees: "100",
    contamination: "0.05",
    seed: "42",
  },
  rules: {},
  ruleThresholdInputs: {},
  trainingByModel: {},
  recommendationDecisions: {},
});
export const useCreateDraft = () => {
  const stored = useDraftStore((state) => state.drafts["/detection/create"]);
  const draft = {
    ...createDefaultDraft(),
    ...(stored?.value && (stored.value as CreateDraft).editor === "create"
      ? (stored.value as CreateDraft)
      : {}),
  };
  const edit = (next: CreateDraft) =>
    useDraftStore.getState().edit("/detection/create", next);
  return {
    draft,
    dirty: stored?.dirty ?? false,
    edit,
    update: (patch: Partial<CreateDraft>) => edit({ ...draft, ...patch }),
    reset: () => useDraftStore.getState().discard("/detection/create"),
  };
};
export const appendCustomFeature = (
  draft: CreateDraft,
  value: FeatureEditorValue,
) => {
  const sequence =
    Math.max(
      draft.customFeatureSequence ?? 0,
      ...[...draft.customFeatureIds, ...Object.keys(draft.featureEdits)].map(
        (id) => Number(/^custom-(\d+)$/.exec(id)?.[1] ?? 0),
      ),
    ) + 1;
  const id = `custom-${sequence}`;
  return {
    id,
    draft: {
      ...draft,
      customFeatureSequence: sequence,
      customFeatureIds: [...draft.customFeatureIds, id],
      selectedFeatureIds: [...draft.selectedFeatureIds, id],
      featureEdits: { ...draft.featureEdits, [id]: { ...value } },
    },
  };
};
export const removeCustomFeature = (
  draft: CreateDraft,
  id: string,
): CreateDraft => {
  if (!draft.customFeatureIds.includes(id)) return draft;
  const featureEdits = Object.fromEntries(
    Object.entries(draft.featureEdits)
      .filter(([key]) => key !== id)
      .map(([key, value]) => [
        key,
        {
          ...value,
          left: value.left === id ? "" : value.left,
          right: value.right === id ? "" : value.right,
        },
      ]),
  );
  const removeInput = (config: TrainingConfig) => ({
    ...config,
    featureIds: config.featureIds.filter((key) => key !== id),
  });
  return {
    ...draft,
    customFeatureSequence: Math.max(
      draft.customFeatureSequence ?? 0,
      ...draft.customFeatureIds.map((key) =>
        Number(/^custom-(\d+)$/.exec(key)?.[1] ?? 0),
      ),
    ),
    customFeatureIds: draft.customFeatureIds.filter((key) => key !== id),
    selectedFeatureIds: draft.selectedFeatureIds.filter((key) => key !== id),
    featureEdits,
    training: removeInput(draft.training),
    trainingByModel: Object.fromEntries(
      Object.entries(draft.trainingByModel).map(([key, value]) => [
        key,
        removeInput(value),
      ]),
    ),
    rules: Object.fromEntries(
      Object.entries(draft.rules).map(([key, value]) => [
        key,
        value.field === id ? { ...value, field: "" } : value,
      ]),
    ),
  };
};
export const validateDataDraft = (draft: CreateDraft) => {
  const errors: string[] = [];
  if (!draft.name.trim() || draft.name.trim().length > 80)
    errors.push("구성 이름은 1~80자로 입력해 주세요.");
  if (!draft.snapshotId) errors.push("고정 데이터셋을 선택해 주세요.");
  if (!draft.start || !draft.end)
    errors.push("학습 시작일과 종료일을 선택해 주세요.");
  else if (draft.start > draft.end)
    errors.push("학습 종료일은 시작일보다 빠를 수 없습니다.");
  if (draft.selectedFeatureIds.length === 0)
    errors.push("학습에 사용할 피처를 선택해 주세요.");
  return errors;
};
export const validateTrainingConfig = (config: TrainingConfig) => {
  const errors: string[] = [];
  if (!config.modelId) errors.push("모델을 선택해 주세요.");
  if (config.featureIds.length === 0)
    errors.push("모델 입력 피처를 선택해 주세요.");
  if (
    !/^\d+$/.test(config.trees.trim()) ||
    Number(config.trees) < 1 ||
    Number(config.trees) > 2000
  )
    errors.push("트리 수는 1~2000 사이 정수여야 합니다.");
  if (
    !config.contamination.trim() ||
    !Number.isFinite(Number(config.contamination)) ||
    Number(config.contamination) <= 0 ||
    Number(config.contamination) > 0.5
  )
    errors.push("이상 비율은 0 초과 0.5 이하 수치여야 합니다.");
  if (!/^\d+$/.test(config.seed.trim()) || Number(config.seed) > 2147483647)
    errors.push("시드는 0~2147483647 사이 정수여야 합니다.");
  return errors;
};
export const validateRule = (rule: RuleDefinition) => {
  const errors: string[] = [];
  if (!rule.name.trim()) errors.push("룰 이름을 입력해 주세요.");
  if (!rule.field) errors.push("룰 속성을 선택해 주세요.");
  const feature = featureCatalog.find((item) => item.id === rule.field);
  if (!feature) errors.push("등록된 룰 속성을 선택해 주세요.");
  if (
    (rule.operator === "gt" || rule.operator === "gte") &&
    feature?.type !== "number"
  )
    errors.push("수치 비교 조건에는 수치 속성이 필요합니다.");
  if (rule.operator === "mismatch" && rule.field !== "F07")
    errors.push("합계 불일치 조건은 합계 차이 피처를 사용해야 합니다.");
  if (rule.operator === "gt" || rule.operator === "gte")
    if (
      rule.threshold === null ||
      !Number.isFinite(rule.threshold) ||
      rule.threshold < 0
    )
      errors.push("임계값은 0 이상의 유효한 수치여야 합니다.");
  return errors;
};
