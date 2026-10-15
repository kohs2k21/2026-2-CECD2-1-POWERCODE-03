import { httpClient } from "./client";

export type Source = { id: string; datasetKey: string; displayName: string; createdAt: string };
export type RuleSuggestion = {
  ruleKey: string;
  displayName: string;
  condition: unknown;
  outcome: { decisionCode: string; severity: string };
  evidenceSpec?: { decision?: { reason: string; role: string }; request?: {
    measurement?: { valid_count: number; missing_count: number };
    baseline?: { value?: number; unit?: string };
  } };
};
export type PipelineJob = {
  id: string;
  kind: "recommend_feature" | "recommend_rule" | "train" | "evaluate" | "explain";
  status: "queued" | "running" | "succeeded" | "failed";
  inputSpec: Record<string, unknown>;
  attemptCount?: number;
  resultSummary?: {
    facts?: {id: string; name: string; value: unknown}[];
    nextChecks?: {code: string; text: string}[];
    featureSetId?: string;
    featureNames?: string[];
    materializedCount?: number;
    suggestions?: RuleSuggestion[];
    configVersionId?: string;
    snapshotId?: string;
    evaluationId?: string;
    applicability?: string;
    metrics?: Record<string, number>;
    reasonCodes?: string[];
    stopReason?: string;
    provider?: string;
    reviews?: { state?: string; reason?: string; featureName?: string; output?: { state: string; reason: string };
      validation?: { passed: boolean }; facts?: { passed: boolean } }[];
  } | null;
  errorCode?: string | null;
};

export type WindowDecision = {
  id: string; adapterType: string; processId: string; windowStart: string; windowEnd: string;
  margins: Record<string, number>; anomalies: Record<string, boolean>;
  evidence: { algorithm: string; trafficAssessed: boolean; latencyAssessed: boolean };
};
export type WindowMonitor = { id: string; algorithm: string; enabled: boolean; lastError: string | null };
export async function fetchWindowDecisions(): Promise<WindowDecision[]> {
  return (await httpClient.get<{ items: WindowDecision[] }>("/api/pipeline/windows")).items;
}
export async function fetchWindowMonitors(): Promise<WindowMonitor[]> {
  return (await httpClient.get<{ items: WindowMonitor[] }>("/api/pipeline/window-monitors")).items;
}
export type ActiveConfiguration = {
  configVersionId: string | null;
  revision: number;
  changedAt?: string;
  detectorState: Record<string, unknown>;
};
export type Application = {
  id: string;
  status: "queued" | "applying" | "succeeded" | "failed";
  errorCode?: string | null;
  targetConfigVersionId?: string;
  evaluationId?: string;
  activatedAt?: string;
};
export type QueueMetric = {
  queue: string; status: string; count: number; oldest_age_seconds: string;
};
export type StageMetric = {
  stage: string; count: number; p50_ms: number; p95_ms: number; p99_ms: number;
};
export type DriftMetric = {
  featureName: string; baselineCount: string; recentCount: string;
  baselineMissingRate: string | null; recentMissingRate: string | null;
  reviewRequired: boolean; reasonCode: string | null; measuredAt: string;
};
export type ReviewObservation = {
  id: string; persistedAt: string; processId: string | null;
  status: string | null; responseCode: string | null;
  errorCount: string | null; successCount: string | null;
  label: "normal" | "anomaly" | null; labelRevision: number;
  reasonCode: string | null;
};
export type ProcessDecision = {
  id: string; sourceId: string; processObservationId: string; configVersionId: string;
  status: "complete" | "partial" | "unavailable";
  ruleStatus: string; mlStatus: string; decisionCode: string | null;
  anomalyScore: string | null; decidedAt: string;
  evidence?: {anomalous?: boolean | null; requestedMode?: string; actualPaths?: string[];
    input?: {mode: "api" | "csv"; runId: string; configurationScope: string};
    demo?: {historicalReplay: boolean; runKey: string; scope: string};
    ruleSeverities?: string[]; detectionPolicy?: {mode: string; deadlineMs: number; combination: string}};
};

export async function fetchSources(): Promise<Source[]> {
  return (await httpClient.get<{ items: Source[] }>("/api/pipeline/sources")).items;
}

export function createPipelineJob(input: Record<string, unknown>): Promise<PipelineJob> {
  return httpClient.post("/api/jobs", input);
}

export function fetchPipelineJob(id: string): Promise<PipelineJob> {
  return httpClient.get(`/api/jobs/${encodeURIComponent(id)}`);
}

export async function fetchPipelineJobs(): Promise<PipelineJob[]> {
  return (await httpClient.get<{ items: PipelineJob[] }>("/api/jobs")).items;
}

export function adoptSuggestedRule(jobId: string, index: number): Promise<{ id: string; ruleKey: string }> {
  return httpClient.post(`/api/jobs/${encodeURIComponent(jobId)}/recommendations/${index}/adopt`, {});
}

export function fetchActiveConfiguration(): Promise<ActiveConfiguration> {
  return httpClient.get("/api/configurations/active");
}

export function requestApplication(configId: string, input: {
  operation: "apply" | "restore";
  evaluationId: string;
  expectedRevision: number;
  expectedActiveVersionId: string | null;
}): Promise<Application> {
  return httpClient.post(`/api/configurations/${encodeURIComponent(configId)}/applications`, input);
}

export function fetchApplication(id: string): Promise<Application> {
  return httpClient.get(`/api/configurations/applications/${encodeURIComponent(id)}`);
}

export async function fetchAppliedConfigurations(): Promise<Application[]> {
  return (await httpClient.get<{ items: Application[] }>("/api/configurations/applications")).items;
}

export function fetchPipelineMetrics(): Promise<{ queues: QueueMetric[]; stages: StageMetric[] }> {
  return httpClient.get("/api/pipeline/metrics");
}

export async function fetchDriftMetrics(): Promise<DriftMetric[]> {
  return (await httpClient.get<{ items: DriftMetric[] }>("/api/pipeline/drift")).items;
}

export async function fetchReviewObservations(sourceId: string): Promise<ReviewObservation[]> {
  return (await httpClient.get<{ items: ReviewObservation[] }>("/api/pipeline/observations", {
    params: { sourceId },
  })).items;
}

export function submitObservationLabel(observationId: string, input: {
  label: "normal" | "anomaly"; reasonCode: string; expectedRevision: number;
}): Promise<{ id: string; revisionNo: number; label: string; reasonCode: string }> {
  return httpClient.post(`/api/pipeline/labels/${encodeURIComponent(observationId)}`, input);
}

export async function fetchProcessDecisions(): Promise<ProcessDecision[]> {
  return (await httpClient.get<{ items: ProcessDecision[] }>("/api/pipeline/decisions")).items;
}

export type CollectorMetric = {endpoint:string;requests:number;failures:number;emptyResponses:number;
  nonemptyResponses:number;p95Ms:number|null;p99Ms:number|null;persistP95Ms:number|null;pollGapP95Seconds:number|null;lastSampleAt:string};
export async function fetchCollectorMetrics(sourceId:string): Promise<CollectorMetric[]> {
  return (await httpClient.get<{items:CollectorMetric[]}>("/api/pipeline/collector/metrics",{params:{sourceId}})).items;
}
export async function fetchCollectorState(): Promise<{sourceId:string;endpoint:string;errorCode:string|null}[]> {
  return (await httpClient.get<{items:{sourceId:string;endpoint:string;errorCode:string|null}[]}>("/api/pipeline/collector")).items;
}
