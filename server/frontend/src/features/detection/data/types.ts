import type {
  EvaluationSet,
  EvaluationSpec,
  EvaluationSplitManifest,
  ScenarioOutcome,
} from "./evaluationTypes";
export type {
  EvaluationSet,
  EvaluationSpec,
  EvaluationSplitManifest,
  ScenarioOutcome,
} from "./evaluationTypes";
export type RawSource = "transaction" | "process";
export type ValueType =
  "timestamp" | "number" | "category" | "identifier" | "text";
export type FeatureReadiness =
  "ready" | "fit-required" | "unconfirmed" | "unsupported" | "deferred";
export type FeatureDefinition = {
  id: string;
  name: string;
  source: RawSource | "derived";
  type: ValueType;
  unit: string;
  expression: string;
  inputs: string[];
  readiness: FeatureReadiness;
  reason: string;
  defaultSelected: boolean;
  aliases: string[];
  fitRequired: boolean;
  collected: boolean | null;
  missingRate: number | null;
  distinctCount: number | null;
  availableAt: string;
};
export type Snapshot = {
  id: string;
  name: string;
  start: string;
  end: string;
  state: "ready" | "pending" | "failed";
  rowCount: number | null;
  schemaVersion: string;
  splitVersion: string;
  description: string;
};
export type ModelDefinition = {
  id: string;
  name: string;
  task: string;
  supportedTypes: ValueType[];
  description: string;
};
export type JobState =
  "accepted" | "queued" | "running" | "succeeded" | "failed";
export type TrainingJob = {
  id: string;
  name: string;
  modelId: string;
  snapshotId: string;
  featureIds: string[];
  state: JobState;
  requestedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  candidateId: string | null;
  failure: string | null;
  progress: number | null;
};
export type RuleDefinition = {
  id: string;
  name: string;
  field: string;
  operator: "gt" | "gte" | "isMissing" | "mismatch";
  threshold: number | null;
  unit: string;
  enabled: boolean;
  description: string;
};
export type RuleRecommendation = {
  id: string;
  name: string;
  rationale: string;
  rule: RuleDefinition;
  evidence: string;
  state: "review-required" | "accepted" | "rejected";
};
// Completed artifacts own their feature/fit/preprocessing bundle. UI composition cannot edit these independently.
export type ModelArtifact = {
  id: string;
  version: string;
  name: string;
  modelId: string;
  featureIds: string[];
  featureVersion: string;
  preprocessingVersion: string;
  fitVersion: string | null;
  snapshotId: string;
  state: JobState;
  completedAt: string | null;
  available: boolean | null;
  compatible: boolean | null;
};
export type RuleVersion = {
  id: string;
  version: string;
  name: string;
  rules: Omit<RuleDefinition, "enabled">[];
  state: JobState;
  completedAt: string | null;
};
export type EvaluationCondition = {
  snapshotId: string;
  protocolId: string;
  splitVersion: string;
  scenario: string;
  purpose: string;
  evaluationSetId?: string;
  evaluationSetRevision?: string;
};
export type EvaluationResult = {
  id: string;
  candidateId: string;
  state: JobState;
  condition: EvaluationCondition;
  metrics: {
    delayRecall: number | null;
    stallRecall: number | null;
    burstRecall: number | null;
    latencyMs: number | null;
    unavailableRate: number | null;
  };
  requestedAt: string | null;
  completedAt: string | null;
  failure: string | null;
  configurationFingerprint: string | null;
  evaluationSetId?: string | null;
  evaluationSetRevision?: string | null;
  scenarioOutcomes?: ScenarioOutcome[] | null;
};
export type VersionBundle = {
  id: string;
  name: string;
  state: "draft" | "candidate" | "active" | "archived";
  modelId: string | null;
  featureIds: string[];
  featureVersion: string;
  preprocessingVersion: string;
  fitVersion: string | null;
  ruleIds: string[];
  explanationVersion: string | null;
  evaluationId: string | null;
  snapshotId: string;
  trained: boolean;
  compatible: boolean | null;
  artifactAvailable: boolean | null;
  appliedAt: string | null;
  modelArtifactId: string | null;
  ruleVersionIds: string[];
  configurationFingerprint: string | null;
};
export type ApplicationEvent = {
  id: string;
  versionId: string;
  previousVersionId: string | null;
  action: "apply" | "rollback";
  state: JobState;
  requestedAt: string | null;
  appliedAt: string | null;
  actor: string;
  failure: string | null;
};
export type CollectionSource = {
  id: "T" | "P" | "M" | "B";
  name: string;
  state: "unknown" | "collecting" | "delayed" | "failed" | "stopped";
  lastSuccessAt: string | null;
  count: number | null;
  pending: number | null;
  gapCount: number | null;
  description: string;
  error: string | null;
};
export type StorageObservation = {
  id: string;
  name: string;
  path: string | null;
  state: "unknown" | "fresh" | "stale" | "failed";
  observedAt: string | null;
  totalBytes: number | null;
  availableBytes: number | null;
  quotaBytes: number | null;
  warningUsedPercent: number;
  warningAvailableBytes: number;
  error: string | null;
};
export type CollectionHistory = {
  id: string;
  source: CollectionSource["id"];
  range: string;
  state: JobState;
  requestedAt: string | null;
  count: number | null;
  failure: string | null;
};
export type Operation =
  | "saveDraft"
  | "createSnapshot"
  | "train"
  | "evaluate"
  | "apply"
  | "rollback"
  | "recommendRules";
// Creation receipt is distinct from completion of a rule version or candidate.
export type CreationOperation = "createRule" | "createCandidate";
export type DetectionOperation =
  Operation | CreationOperation | "createEvaluationSet";
export type DetectionData = {
  capabilities: Record<Operation | CreationOperation, boolean>;
  preparationCapabilities?: { createEvaluationSet: boolean };
  evaluationSets?: EvaluationSet[];
  evaluationSpecs?: EvaluationSpec[];
  evaluationSplits?: EvaluationSplitManifest[];
  modelArtifacts: ModelArtifact[];
  ruleVersions: RuleVersion[];
  snapshots: Snapshot[];
  models: ModelDefinition[];
  trainingJobs: TrainingJob[];
  rules: RuleDefinition[];
  recommendations: RuleRecommendation[];
  versions: VersionBundle[];
  evaluations: EvaluationResult[];
  activeVersionId: string | null;
  applications: ApplicationEvent[];
  collection: CollectionSource[];
  storage: StorageObservation[];
  collectionHistory: CollectionHistory[];
  latency: {
    sourceTimestampAvailable: boolean;
    clockSynchronized: boolean;
    endToEndMs: number | null;
    uiDeliveryMs: number | null;
    xaiMs: number | null;
  };
};
export type ActionReceipt = {
  requestId: string;
  state: "accepted";
  message: string;
  job?: {
    id: string;
    type: "training" | "evaluation";
    candidateId?: string;
  } | null;
};
export type DetectionGateway = {
  read: (signal?: AbortSignal) => Promise<DetectionData>;
  request: (
    operation: DetectionOperation,
    payload: Record<string, unknown>,
    signal?: AbortSignal,
  ) => Promise<ActionReceipt>;
};
