import type { DetectionData, EvaluationCondition } from "./types";
import type {
  EvaluationPreparationDraft,
  EvaluationSet,
  ScenarioRecipe,
} from "./evaluationTypes";

export const newEvaluationPreparation = (): EvaluationPreparationDraft => ({
  name: "",
  snapshotId: "",
  splitManifestId: "",
  specId: "",
  purpose: "validation",
  metricDefinition: "",
  labelSource: "",
  acceptanceNote: "",
  metrics: [],
  scenarios: [],
  scenarioSequence: 0,
  csv: null,
});

export const scenarioLabels = {
  delay: "지연",
  stall: "먹통",
  burst: "폭주",
} as const;

export const scenarioErrors = (recipe: ScenarioRecipe): string[] => {
  const errors: string[] = [];
  if (!recipe.targetField.trim()) errors.push("대상 필드를 선택해 주세요.");
  if (!recipe.condition.trim()) errors.push("주입 조건을 입력해 주세요.");
  if (!recipe.intensity.trim()) errors.push("강도와 단위를 입력해 주세요.");
  if (
    !/^\d+$/.test(recipe.count) ||
    !Number.isSafeInteger(Number(recipe.count)) ||
    Number(recipe.count) <= 0 ||
    Number(recipe.count) > 100000
  )
    errors.push("건수는 1~100000의 정수로 입력해 주세요.");
  if (!/^\d+$/.test(recipe.seed) || !Number.isSafeInteger(Number(recipe.seed)))
    errors.push("seed는 0 이상의 안전한 정수로 입력해 주세요.");
  return errors;
};

export const preparationErrors = (
  draft: EvaluationPreparationDraft,
  data: DetectionData,
) => {
  const errors: string[] = [];
  if (!draft.name.trim() || draft.name.trim().length > 80)
    errors.push("세트 이름은 1~80자로 입력해 주세요.");
  if (
    !data.snapshots.some(
      (item) => item.id === draft.snapshotId && item.state === "ready",
    )
  )
    errors.push("준비된 고정 데이터셋을 선택해 주세요.");
  const split = data.evaluationSplits?.find(
    (item) => item.id === draft.splitManifestId,
  );
  if (!split || split.snapshotId !== draft.snapshotId)
    errors.push("데이터셋에 연결된 고정 분할이 필요합니다.");
  else if (
    split.overlapChecked !== true ||
    split.trainOnlyFit !== true ||
    !split.fitLineage
  )
    errors.push("분할 겹침 검사와 train-only fit 근거 확인이 필요합니다.");
  if (
    !draft.specId &&
    (!draft.metricDefinition.trim() ||
      !draft.labelSource.trim() ||
      !draft.metrics.length)
  )
    errors.push("지표·산식·라벨 근거를 입력해 주세요.");
  if (
    draft.specId &&
    !data.evaluationSpecs?.some((item) => item.id === draft.specId)
  )
    errors.push("평가기준을 찾을 수 없습니다.");
  if (!draft.scenarios.length)
    errors.push(
      "고정 세트 생성에는 이상 시나리오가 필요합니다. CSV 확인 정보만으로 시험 원본을 등록할 수 없습니다.",
    );
  draft.scenarios.forEach((recipe) =>
    scenarioErrors(recipe).forEach((error) =>
      errors.push(`${scenarioLabels[recipe.kind]}: ${error}`),
    ),
  );
  return errors;
};

export const evaluationSetErrors = (
  set: EvaluationSet | undefined,
  data: DetectionData,
) => {
  const errors: string[] = [];
  if (!set) return ["서버에 고정된 평가 세트를 선택해 주세요."];
  if (!set.immutable || set.state !== "ready" || !set.revision.trim())
    errors.push("불변 revision이 확인된 준비 완료 세트가 필요합니다.");
  if (
    !data.snapshots.some(
      (item) => item.id === set.snapshotId && item.state === "ready",
    )
  )
    errors.push("평가 데이터셋이 준비되지 않았습니다.");
  const split = data.evaluationSplits?.find(
    (item) =>
      item.id === set.splitManifestId &&
      item.revision === set.splitRevision &&
      item.snapshotId === set.snapshotId,
  );
  if (!split) errors.push("고정 분할과 revision을 확인할 수 없습니다.");
  else {
    if (split.overlapChecked !== true)
      errors.push("거래·실행 단위의 분할 겹침 검증이 확인되지 않았습니다.");
    if (split.trainOnlyFit !== true || !split.fitLineage)
      errors.push("train-only fit 근거가 확인되지 않았습니다.");
  }
  const spec = data.evaluationSpecs?.find(
    (item) => item.id === set.specId && item.revision === set.specRevision,
  );
  if (!spec) errors.push("평가기준과 revision을 확인할 수 없습니다.");
  else if (
    !spec.metrics.length ||
    !spec.metricDefinition.trim() ||
    !spec.labelSource.trim()
  )
    errors.push("지표 정의와 라벨 근거가 확인되지 않았습니다.");
  if (!["validation", "test"].includes(set.purpose))
    errors.push("validation/test 목적이 확인되지 않았습니다.");
  if (!set.labelSource.trim()) errors.push("라벨 출처가 확인되지 않았습니다.");
  return errors;
};

export const conditionFromEvaluationSet = (
  set: EvaluationSet,
): EvaluationCondition => ({
  snapshotId: set.snapshotId,
  protocolId: `${set.specId}@${set.specRevision}`,
  splitVersion: `${set.splitManifestId}@${set.splitRevision}`,
  scenario: set.scenarios.map((recipe) => recipe.id).join(","),
  purpose: set.purpose,
  evaluationSetId: set.id,
  evaluationSetRevision: set.revision,
});
