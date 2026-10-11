export type EvaluationPurpose = "validation" | "test";
export type EvaluationMetric =
  | "delayRecall"
  | "stallRecall"
  | "burstRecall"
  | "latencyMs"
  | "unavailableRate";
export type ScenarioKind = "delay" | "stall" | "burst";

export type EvaluationSplitPart = {
  start: string | null;
  end: string | null;
  rowCount: number | null;
  executionCount: number | null;
};
export type EvaluationSplitManifest = {
  id: string;
  revision: string;
  snapshotId: string;
  train: EvaluationSplitPart;
  validation: EvaluationSplitPart;
  test: EvaluationSplitPart;
  overlapChecked: boolean | null;
  trainOnlyFit: boolean | null;
  fitLineage: string | null;
  testRunCount: number | null;
};
export type AcceptanceCriterion = {
  metric: EvaluationMetric;
  operator: "gte" | "lte";
  value: number;
  source: string;
};
export type EvaluationSpec = {
  id: string;
  revision: string;
  name: string;
  metrics: EvaluationMetric[];
  metricDefinition: string;
  labelSource: string;
  acceptanceCriteria: AcceptanceCriterion[] | null;
};
export type ScenarioRecipe = {
  id: string;
  kind: ScenarioKind;
  targetField: string;
  condition: string;
  intensity: string;
  count: string;
  seed: string;
};
export type EvaluationSet = {
  id: string;
  revision: string;
  name: string;
  immutable: boolean;
  state: "ready" | "pending" | "failed";
  snapshotId: string;
  splitManifestId: string;
  splitRevision: string;
  specId: string;
  specRevision: string;
  purpose: EvaluationPurpose;
  scenarios: ScenarioRecipe[];
  labelSource: string;
};
export type ScenarioOutcome = {
  kind: ScenarioKind;
  injected: number | null;
  detected: number | null;
  missed: number | null;
  unavailable: number | null;
  excluded: number | null;
  labelSource: string | null;
  reason: string | null;
};
export type EvaluationCsvMetadata = {
  columns: string[];
  rowCount: number;
  labels: Record<string, number>;
  scenarios: Record<string, number>;
  samples: { scenario: string; label: string }[];
};
export type EvaluationPreparationDraft = {
  name: string;
  snapshotId: string;
  splitManifestId: string;
  specId: string;
  purpose: EvaluationPurpose;
  metricDefinition: string;
  labelSource: string;
  acceptanceNote: string;
  metrics: EvaluationMetric[];
  scenarios: ScenarioRecipe[];
  scenarioSequence: number;
  csv: EvaluationCsvMetadata | null;
};
