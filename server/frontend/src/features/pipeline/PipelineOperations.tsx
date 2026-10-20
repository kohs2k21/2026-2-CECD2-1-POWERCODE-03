import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchCollectorMetrics, fetchCollectorState, type CollectorMetric, adoptSuggestedRule, createPipelineJob, fetchActiveConfiguration, fetchAppliedConfigurations,
  fetchApplication, fetchDriftMetrics, fetchPipelineJob, fetchPipelineJobs, fetchFeatureSets, fetchTrainerCapabilities,
  fetchPipelineMetrics, fetchProcessDecisions, fetchReviewObservations, fetchSources,
  requestApplication, submitObservationLabel,
  fetchWindowDecisions, fetchWindowMonitors, type WindowDecision, type WindowMonitor,
  type ActiveConfiguration, type Application, type DriftMetric, type FeatureSet, type TrainerCapabilities,
  type PipelineJob, type ProcessDecision, type QueueMetric, type ReviewObservation, type Source, type StageMetric,
} from "../../services/api/pipeline.api";
import "./pipeline.css";
import { consumeDecisionStream } from "../../services/api/decision-stream.api";

import { InputControls } from "./InputControls";
import { DecisionDiagnostics } from "./DecisionDiagnostics";
import { useAuthSession } from "../../services/auth/AuthSessionProvider";
import { forgetJobRequestKey, jobRequestKey } from "../../services/api/job-idempotency";
import { HttpError } from "../../services/api/client";
import { TrainerJobResult } from "./TrainerJobResult";
import {
  emptyResultVisible, initialRefreshStatus, mergeRefreshStatus, refreshAlertText, rows, trackRefresh,
  type RefreshKey, type RefreshStatus,
} from "./pipelineRefresh";

type JobKind = PipelineJob["kind"];
type JobMap = Partial<Record<JobKind, PipelineJob>>;

function localInput(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function errorText(error: unknown): string {
  if (error instanceof HttpError && error.code) return error.code;
  return error instanceof Error ? error.message : "요청을 완료하지 못했습니다.";
}

export function PipelineOperations() {
  const { user } = useAuthSession();
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [from, setFrom] = useState(() => localInput(new Date(Date.now() - 30 * 86400_000)));
  const [until, setUntil] = useState(() => localInput(new Date()));
  const [history, setHistory] = useState<PipelineJob[]>([]);
  const [featureSets, setFeatureSets] = useState<FeatureSet[]>([]);
  const [featureSetsTruncated, setFeatureSetsTruncated] = useState(false);
  const [featureSetId, setFeatureSetId] = useState("");
  const [capabilities, setCapabilities] = useState<TrainerCapabilities | null>(null);
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [modelKey, setModelKey] = useState("");
  const [collectorMetrics,setCollectorMetrics] = useState<CollectorMetric[]>([]);
  const [collectorState,setCollectorState] = useState<{sourceId:string;endpoint:string;errorCode:string|null}[]>([]);
  const [mode, setMode] = useState("parallel");
  const [deadlineMs, setDeadlineMs] = useState(3000);
  const [combinationApproved, setCombinationApproved] = useState(false);
  const [explanations, setExplanations] = useState<Record<string, PipelineJob>>({});
  const [ruleIds, setRuleIds] = useState<string[]>([]);
  const [application, setApplication] = useState<Application | null>(null);
  const [applied, setApplied] = useState<Application[]>([]);
  const [restoreId, setRestoreId] = useState("");
  const [active, setActive] = useState<ActiveConfiguration | null>(null);
  const [queues, setQueues] = useState<QueueMetric[]>([]);
  const [stages, setStages] = useState<StageMetric[]>([]);
  const [drift, setDrift] = useState<DriftMetric[]>([]);
  const [reviewRows, setReviewRows] = useState<ReviewObservation[]>([]);
  const [decisions, setDecisions] = useState<ProcessDecision[]>([]);
  const [windowDecisions, setWindowDecisions] = useState<WindowDecision[]>([]);
  const [windowMonitors, setWindowMonitors] = useState<WindowMonitor[]>([]);
  const [labelReason, setLabelReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatus>(initialRefreshStatus);

  // 조회별로 끝나는 즉시 성공 값만 반영하고 실패는 식별자별로 기록한다. 사용자 동작 오류(error)와 분리한다. reject하지 않는다.
  const track = useCallback(<V extends Partial<Record<RefreshKey, unknown>>>(
    loaders: { [K in keyof V]: () => Promise<V[K]> }, appliers: { [K in keyof V]: (value: V[K]) => void },
  ) => trackRefresh(loaders, appliers, errorText, (outcome) => {
    setRefreshStatus((previous) => mergeRefreshStatus(previous, outcome));
  }), []);

  const refresh = useCallback(() => track({
    sources: fetchSources, jobs: fetchPipelineJobs, active: fetchActiveConfiguration,
    metrics: fetchPipelineMetrics, drift: fetchDriftMetrics, applied: fetchAppliedConfigurations, decisions: fetchProcessDecisions,
    windows: fetchWindowDecisions, monitors: fetchWindowMonitors,
  }, {
    sources: (value) => { const items = rows(value); setSources(items); setSourceId((current) => current || items[0]?.id || ""); },
    jobs: (value) => setHistory(rows(value)), active: setActive,
    metrics: (metrics) => { const queueRows = rows(metrics.queues), stageRows = rows(metrics.stages); setQueues(queueRows); setStages(stageRows); },
    drift: (value) => setDrift(rows(value)), applied: (value) => setApplied(rows(value)), decisions: (value) => setDecisions(rows(value)),
    windows: (value) => setWindowDecisions(rows(value)), monitors: (value) => setWindowMonitors(rows(value)),
  }), [track]);

  const jobs = useMemo(() => {
    const start = Date.parse(from);
    const end = Date.parse(until);
    const sourceJobs = history.filter((job) => job.inputSpec.sourceId === sourceId);
    const periodJobs = sourceJobs.filter((job) => Date.parse(String(job.inputSpec.from)) === start &&
      Date.parse(String(job.inputSpec.until)) === end);
    const latest: JobMap = {};
    latest.recommend_feature = periodJobs.find((job) => job.kind === "recommend_feature");
    latest.recommend_rule = periodJobs.find((job) => job.kind === "recommend_rule" && job.inputSpec.featureSetId === featureSetId);
    latest.train = periodJobs.find((job) => {
      const policy = job.inputSpec.detectionPolicy as { mode?: string; deadlineMs?: number } | undefined;
      return job.kind === "train" && job.inputSpec.featureSetId === featureSetId &&
        policy?.mode === mode && policy.deadlineMs === deadlineMs &&
        (mode === "rule_only" || (job.inputSpec.modelKey ?? "iforest") === modelKey) &&
        JSON.stringify(job.inputSpec.ruleVersionIds ?? []) === JSON.stringify(mode === "ml_only" ? [] : ruleIds);
    });
    latest.evaluate = sourceJobs.find((job) => job.kind === "evaluate" &&
      job.inputSpec.configVersionId === latest.train?.resultSummary?.configVersionId);
    return latest;
  }, [history, sourceId, from, until, featureSetId, mode, deadlineMs, modelKey, ruleIds]);

  const recommendedFeatureSetId = jobs.recommend_feature?.resultSummary?.featureSetId;
  useEffect(() => {
    let current = true;
    void fetchTrainerCapabilities().then((value) => {
      if (!current) return;
      setCapabilities(value);
      setModelKey((previous) => value.models.some((model) => model.key === previous && model.supported)
        ? previous : value.models.find((model) => model.supported)?.key ?? "");
    }).catch((reason) => { if (current) setError(errorText(reason)); });
    return () => { current = false; };
  }, [catalogRevision]);

  useEffect(() => {
    if (!sourceId) return;
    let current = true;
    void fetchFeatureSets(sourceId).then(({items, hasMore}) => {
      if (!current) return;
      setFeatureSets(items);
      setFeatureSetsTruncated(hasMore);
      setFeatureSetId((previous) => {
        if (recommendedFeatureSetId && items.some((item) => item.id === recommendedFeatureSetId && item.supported)) return recommendedFeatureSetId;
        return items.some((item) => item.id === previous && item.supported) ? previous : items.find((item) => item.supported)?.id ?? "";
      });
    }).catch((reason) => { if (current) setError(errorText(reason)); });
    return () => { current = false; };
  }, [sourceId, recommendedFeatureSetId, catalogRevision]);

  useEffect(() => {
    void refresh().catch((reason) => setError(errorText(reason)));
  }, [refresh]);

  useEffect(() => {
    if (!sourceId) return;
    let current = true;
    const refreshCollection = () => {
      void fetchCollectorMetrics(sourceId).then((items) => { if (current) setCollectorMetrics(items); }).catch(reason => { if (current) setError(errorText(reason)); });
      void fetchCollectorState().then((items) => { if (current) setCollectorState(items); }).catch(reason => { if (current) setError(errorText(reason)); });
    };
    refreshCollection();
    const timer = setInterval(refreshCollection,5000);
    void fetchReviewObservations(sourceId).then((items) => { if (current) setReviewRows(items); }).catch((reason) => { if (current) setError(errorText(reason)); });
    return () => { current = false; clearInterval(timer); };
  }, [sourceId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const pending = history.filter(
        (job): job is PipelineJob => Boolean(job && ["queued", "running"].includes(job.status)),
      );
      for (const job of pending) {
        void fetchPipelineJob(job.id).then((updated) => {
          setHistory((previous) => previous.map((item) => item.id === updated.id ? updated : item));
        }).catch((reason) => setError(errorText(reason)));
      }
      if (application && ["queued", "applying"].includes(application.status)) {
        void fetchApplication(application.id).then((updated) => {
          setApplication(updated);
          if (updated.status === "succeeded") {
            void track({ active: fetchActiveConfiguration, applied: fetchAppliedConfigurations }, { active: setActive, applied: (value) => setApplied(rows(value)) });
          }
        }).catch((reason) => setError(errorText(reason)));
      }

      void track({ windows: fetchWindowDecisions, monitors: fetchWindowMonitors },
        { windows: (value) => setWindowDecisions(rows(value)), monitors: (value) => setWindowMonitors(rows(value)) });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [history, application, track]);

  useEffect(() => {
    const controller = new AbortController();
    let after = "0";
    let timer: ReturnType<typeof setTimeout> | undefined;
    const connect = async () => {
      try {
        await consumeDecisionStream(controller.signal, after, (value,id) => {
          after = id;
          setDecisions(previous => [value,...previous.filter(item => item.id !== value.id)].slice(0,50));
        });
      } catch (reason) {
        if (!controller.signal.aborted) setError(errorText(reason));
      }
      if (!controller.signal.aborted) timer = setTimeout(() => {void connect();},3000);
    };
    void connect();
    return () => {controller.abort();if (timer) clearTimeout(timer);};
  }, []);

  useEffect(() => {
    const pending = Object.entries(explanations).filter(([,job]) => ['queued','running'].includes(job.status));
    if (!pending.length) return;
    const timer = setInterval(() => {
      for (const [id,job] of pending) void fetchPipelineJob(job.id).then(updated => {
        setExplanations(previous => ({...previous,[id]:updated}));
      }).catch(reason => setError(errorText(reason)));
    },3000);
    return () => clearInterval(timer);
  }, [explanations]);

  async function explain(decision: ProcessDecision) {
    try {
      const job = await createPipelineJob({kind:'explain',sourceId:decision.sourceId,decisionId:decision.id});
      setExplanations(previous => ({...previous,[decision.id]:job}));
    } catch (reason) {setError(errorText(reason));}
  }

  const ruleSuggestions = jobs.recommend_rule?.resultSummary?.suggestions ?? [];
  const configId = jobs.train?.resultSummary?.configVersionId;
  const snapshotId = jobs.train?.resultSummary?.snapshotId;
  const evaluationId = jobs.evaluate?.resultSummary?.evaluationId;
  const canApply = jobs.evaluate?.status === "succeeded" && jobs.evaluate.resultSummary?.applicability === "valid" && Boolean(configId && evaluationId);
  const selectedSource = useMemo(() => sources.find((source) => source.id === sourceId), [sources, sourceId]);
  const selectedFeatureSet = featureSets.find((item) => item.id === featureSetId);
  const selectedModel = capabilities?.models.find((item) => item.key === modelKey);
  const validPeriod = Number.isFinite(Date.parse(from)) && Number.isFinite(Date.parse(until)) &&
    Date.parse(from) < Date.parse(until) && Date.parse(until) - Date.parse(from) <= 366 * 86400_000;
  const sourceHistory = history.filter((job) => job.inputSpec.sourceId === sourceId && ["train", "evaluate"].includes(job.kind));
  const jobsKnown = emptyResultVisible(refreshStatus, "jobs");
  const alertText = refreshAlertText(refreshStatus, error);

  function changeSource(id: string) {
    setSourceId(id);
    setFeatureSets([]);
    setFeatureSetsTruncated(false);
    setFeatureSetId("");
    setRuleIds([]);
    setCombinationApproved(false);
    setApplication(null);
    setReviewRows([]);
    setCollectorMetrics([]);
    setCollectorState([]);
    setError(null);
  }

  async function submit(kind: JobKind) {
    if (!user || !sourceId || !validPeriod) return;
    setBusy(true);
    setError(null);
    try {
      const input: Record<string, unknown> = { kind, sourceId };
      if (kind === "evaluate") {
        if (!configId || !snapshotId) return;
        input.configVersionId = configId;
        input.snapshotId = snapshotId;
        input.purpose = "validation";
      } else {
        input.from = new Date(from).toISOString();
        input.until = new Date(until).toISOString();
        if (kind !== "recommend_feature") input.featureSetId = featureSetId;
        const reusedSnapshot = jobs.recommend_feature?.resultSummary?.snapshotId;
        if (kind !== "recommend_feature" && reusedSnapshot &&
            jobs.recommend_feature?.inputSpec.from === input.from && jobs.recommend_feature?.inputSpec.until === input.until) {
          input.snapshotId = reusedSnapshot;
        }
        if (kind === "train") {
          if (!selectedFeatureSet?.supported || (mode !== "rule_only" && !selectedModel?.supported)) return;
          if (mode !== "rule_only") input.modelKey = modelKey;
          input.ruleVersionIds = mode === 'ml_only' ? [] : ruleIds;
          input.detectionPolicy = {mode,deadlineMs,combination:mode === 'parallel' ? 'any_anomaly' : 'selected_path'};
        }
      }
      const job = await createPipelineJob(input, jobRequestKey(user.id, input));
      setHistory((previous) => [job, ...previous.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function rerun(previous: PipelineJob) {
    if (!user || !["failed", "succeeded"].includes(previous.status)) return;
    setBusy(true);
    setError(null);
    try {
      const action = { ...previous.inputSpec, newExecutionOf: previous.id };
      const key = jobRequestKey(user.id, action);
      const job = await createPipelineJob(previous.inputSpec, key);
      setHistory((items) => [job, ...items.filter((item) => item.id !== job.id)]);
      forgetJobRequestKey(user.id, action);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function evaluateCandidate(training: PipelineJob) {
    const candidate = training.resultSummary;
    if (!user || training.status !== "succeeded" || !candidate?.configVersionId || !candidate.snapshotId) return;
    setBusy(true);
    setError(null);
    try {
      const input = { kind: "evaluate", sourceId: training.inputSpec.sourceId,
        configVersionId: candidate.configVersionId, snapshotId: candidate.snapshotId, purpose: "validation" };
      const job = await createPipelineJob(input, jobRequestKey(user.id, input));
      setHistory((items) => [job, ...items.filter((item) => item.id !== job.id)]);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function adopt(index: number) {
    const jobId = jobs.recommend_rule?.id;
    if (!jobId) return;
    setBusy(true);
    setError(null);
    try {
      const rule = await adoptSuggestedRule(jobId, index);
      setRuleIds((current) => current.includes(rule.id) ? current : [...current, rule.id]);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!configId || !evaluationId || !active) return;
    setBusy(true);
    setError(null);
    try {
      setApplication(await requestApplication(configId, {
        operation: "apply", evaluationId,
        expectedRevision: active.revision,
        expectedActiveVersionId: active.configVersionId,
      }));
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    const previous = applied.find((item) => item.id === restoreId);
    if (!previous?.targetConfigVersionId || !previous.evaluationId || !active) return;
    setBusy(true);
    setError(null);
    try {
      setApplication(await requestApplication(previous.targetConfigVersionId, {
        operation: "restore", evaluationId: previous.evaluationId,
        expectedRevision: active.revision,
        expectedActiveVersionId: active.configVersionId,
      }));
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function labelObservation(observation: ReviewObservation, label: "normal" | "anomaly") {
    if (!/^[A-Z][A-Z0-9_]{2,63}$/.test(labelReason)) return;
    setBusy(true);
    setError(null);
    try {
      await submitObservationLabel(observation.id, {
        label, reasonCode: labelReason, expectedRevision: observation.labelRevision,
      });
      setReviewRows(await fetchReviewObservations(sourceId));
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="pipeline-workspace">
      <header className="pipeline-header">
        <div>
          <h2>학습·탐지 운영</h2>
          <p>저장된 로그로 후보를 만들고 평가한 뒤 승인된 구성만 실시간 탐지에 적용합니다.</p>
        </div>
        <button type="button" onClick={() => {
          setCatalogRevision((revision) => revision + 1);
          void refresh().catch((reason) => setError(errorText(reason)));
        }}>새로고침</button>
      </header>

      {alertText && <p className="pipeline-error" role="alert">{alertText}</p>}
      <section className="pipeline-card">
        <h3>대상 데이터</h3>
        <div className="pipeline-fields">
          <label>출처
            <select value={sourceId} disabled={busy} onChange={(event) => changeSource(event.target.value)}>
              {sources.map((source) => <option key={source.id} value={source.id}>{source.displayName} ({source.datasetKey})</option>)}
            </select>
          </label>
          <label>시작 시각<input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>종료 시각<input type="datetime-local" value={until} onChange={(event) => setUntil(event.target.value)} /></label>
        </div>
        <p>기간은 저장 시각 기준입니다. 출처 시각의 시간대가 확인되기 전에는 발생 시각으로 해석하지 않습니다.</p>
        {!selectedSource && emptyResultVisible(refreshStatus, "sources") && <p>저장된 데이터 출처가 없습니다. 먼저 Collector로 로그를 저장하세요.</p>}
      </section>

      <InputControls key={sourceId} sourceId={sourceId} />
      <section className="pipeline-grid">
        <div className="pipeline-card">
          <h3>1. 파생변수 생성</h3>
          <button disabled={busy || !sourceId || !validPeriod} onClick={() => void submit("recommend_feature")}>변수 생성 작업 등록</button>
          <JobState job={jobs.recommend_feature} known={jobsKnown} />
          {jobs.recommend_feature?.resultSummary?.stopReason && <p>{jobs.recommend_feature.resultSummary.stopReason}</p>}
          {jobs.recommend_feature?.resultSummary?.reviews?.map((review, index) => <p key={index}>{review.state}: {review.reason}</p>)}
          {recommendedFeatureSetId && <p>생성된 피처셋: <code>{recommendedFeatureSetId}</code><br />정제 행: {jobs.recommend_feature?.resultSummary?.materializedCount ?? "미측정"}</p>}
        </div>
        <div className="pipeline-card">
          <h3>2. Rule 추천·채택</h3>
          <button disabled={busy || !validPeriod || !selectedFeatureSet?.supported} onClick={() => void submit("recommend_rule")}>Rule 추천 작업 등록</button>
          <JobState job={jobs.recommend_rule} known={jobsKnown} />
          {ruleSuggestions.map((suggestion, index) => (
            <div className="pipeline-suggestion" key={`${suggestion.ruleKey}-${index}`}>
              <span>{suggestion.displayName} · {suggestion.outcome.decisionCode}</span>
              <details>
                <summary>판정 조건과 근거</summary>
                <p>{suggestion.evidenceSpec?.decision?.reason ?? "별도 의미 검토가 필요합니다."}</p>
                <code>{JSON.stringify(suggestion.condition)}</code>
                {suggestion.evidenceSpec?.request?.measurement && <p>
                  유효 값 {suggestion.evidenceSpec.request.measurement.valid_count}개 ·
                  결측 {suggestion.evidenceSpec.request.measurement.missing_count}개
                </p>}
              </details>
              <button disabled={busy} onClick={() => void adopt(index)}>검토 후 초안 채택</button>
            </div>
          ))}
          {jobs.recommend_rule?.resultSummary?.reviews?.map((review, index) => (
            <p key={index}>{review.featureName} ·
              {review.validation?.passed === false || review.facts?.passed === false ? "검증 탈락" : review.output?.state ?? review.state} ·
              {review.output?.reason ?? review.reason}</p>
          ))}
          <label>학습 구성에 포함할 Rule 버전 ID
            <input value={ruleIds.join(",")} onChange={(event) => setRuleIds(event.target.value.split(",").map((id) => id.trim()).filter(Boolean))}
              placeholder="채택한 Rule ID가 자동 입력됩니다" />
          </label>
        </div>
        <div className="pipeline-card">
          <h3>3. 탐지 구성 생성·학습</h3>
          <label>고정 피처셋<select value={featureSetId} onChange={(event) => {setFeatureSetId(event.target.value);setRuleIds([]);}}>
            <option value="">피처셋 선택</option>
            {featureSets.map((item) => <option key={item.id} value={item.id} disabled={!item.supported}>
              {item.id.slice(0, 8)} · {item.featureNames.length}개{item.supported ? "" : ` · ${item.reason ?? "지원하지 않음"}`}
            </option>)}
          </select></label>
          {featureSetsTruncated && <p>최근 등록 피처셋 200개를 표시합니다.</p>}
          {selectedFeatureSet && <p>입력 순서: {selectedFeatureSet.featureNames.join(", ")}</p>}
          <label>탐지 모드<select value={mode} onChange={event => {setMode(event.target.value);setCombinationApproved(false);}}>
            <option value="parallel">Rule + ML 병렬</option><option value="rule_only">Rule 전용</option><option value="ml_only">ML 전용</option>
          </select></label>
          {mode !== "rule_only" && <label>모델<select value={modelKey} onChange={(event) => setModelKey(event.target.value)}>
            <option value="">모델 선택</option>
            {capabilities?.models.map((model) => <option key={model.key} value={model.key} disabled={!model.supported}>
              {model.displayName}{model.supported ? "" : ` · ${model.reason ?? "지원하지 않음"}`}
            </option>)}
          </select></label>}
          <label>판정 마감 시간 (ms)<input type="number" min={100} max={30000} value={deadlineMs}
            onChange={event => setDeadlineMs(Number(event.target.value))} /></label>
          {mode === 'parallel' && <label className="pipeline-policy-choice"><input type="checkbox" checked={combinationApproved}
            onChange={event => setCombinationApproved(event.target.checked)} />이 구성에서는 한 경로라도 이상이면 이상으로 결합하도록 선택합니다.</label>}
          <p>마감 시간은 운영 설정입니다. Rule 전용은 IF 학습 없이 후보 구성을 만듭니다.</p>
          <button disabled={busy || !user || !validPeriod || !selectedFeatureSet?.supported || (mode !== 'rule_only' && !selectedModel?.supported) || !Number.isInteger(deadlineMs) || deadlineMs < 100 || deadlineMs > 30000 ||
            (mode !== 'ml_only' && !ruleIds.length) || (mode === 'parallel' && !combinationApproved)} onClick={() => void submit("train")}>선택 기간 학습 실행</button>
          <JobState job={jobs.train} known={jobsKnown} />
          {configId && <p>후보 구성: <code>{configId}</code></p>}
        </div>
        <div className="pipeline-card">
          <h3>4. 평가·승인</h3>
          <button disabled={busy || !configId || !snapshotId} onClick={() => void submit("evaluate")}>검증 분할 평가</button>
          <JobState job={jobs.evaluate} known={jobsKnown} />
          {(jobs.evaluate || jobsKnown) && <p>평가 적합성: {jobs.evaluate?.resultSummary?.applicability ?? "미평가"}</p>}
          <button disabled={busy || !canApply} onClick={() => void apply()}>평가된 구성 적용 요청</button>
          {application && <p>적용 상태: {application.status}{application.errorCode ? ` · ${application.errorCode}` : ""}</p>}
          {/* 구성 없음 응답도 객체이므로 configVersionId로 판단한다. 구성이 있으면 직전 성공값을 계속 표시한다. */}
          {(active?.configVersionId || emptyResultVisible(refreshStatus, "active")) && <p>현재 활성 구성: <code>{active?.configVersionId ?? "없음"}</code></p>}
          <label>이전 승인 구성으로 복원
            <select value={restoreId} onChange={(event) => setRestoreId(event.target.value)}>
              <option value="">복원 대상을 선택하세요</option>
              {applied.filter((item) => item.targetConfigVersionId !== active?.configVersionId).map((item) => (
                <option key={item.id} value={item.id}>{item.targetConfigVersionId} · {item.activatedAt ?? ""}</option>
              ))}
            </select>
          </label>
          <button disabled={busy || !restoreId} onClick={() => void restore()}>복원 요청</button>
        </div>
      </section>

      <section className="pipeline-card">
        <h3>학습·평가 작업 이력</h3>
        {sourceHistory.length === 0 && jobsKnown && <p>등록된 작업이 없습니다.</p>}
        {sourceHistory.map((job) => <details key={job.id} className="pipeline-job-result">
          <summary>{job.kind === "train" ? "학습" : "평가"} · {job.id.slice(0, 8)} · {job.status}</summary>
          <JobState job={job} />
          <p>작업 ID: <code>{job.id}</code></p>
          <p>피처셋: <code>{String(job.inputSpec.featureSetId ?? job.resultSummary?.featureSetId ?? "미제공")}</code></p>
          <p>기간: {String(job.inputSpec.from ?? "고정 스냅샷")} ~ {String(job.inputSpec.until ?? "")}</p>
          <TrainerJobResult job={job} />
          {job.kind === "train" && job.status === "succeeded" && job.resultSummary?.configVersionId && job.resultSummary.snapshotId &&
            <button disabled={busy || !user} onClick={() => void evaluateCandidate(job)}>이 후보 검증 평가</button>}
          <button disabled={busy} onClick={() => void fetchPipelineJob(job.id).then((updated) => {
            setHistory((items) => items.map((item) => item.id === updated.id ? updated : item));
          }).catch((reason) => setError(errorText(reason)))}>작업 조회</button>
          {["failed", "succeeded"].includes(job.status) && <button disabled={busy || !user} onClick={() => void rerun(job)}>새로 실행</button>}
        </details>)}
      </section>

      <section className="pipeline-card">
        <h3>1분 시간창 탐지</h3>
        {windowMonitors.length === 0 && emptyResultVisible(refreshStatus, "monitors") && <p>등록된 시간창 감시가 없습니다.</p>}
        {windowMonitors.map((monitor) => <p key={monitor.id}>{monitor.algorithm} · {monitor.lastError ?? (monitor.enabled ? "감시 활성" : "중지")}</p>)}
        {windowDecisions.slice(0, 10).map((decision) => <p key={decision.id}>
          {decision.adapterType} / {decision.processId} · {decision.windowStart} · {decision.evidence.algorithm} ·
          전체 점수 {decision.margins.global.toFixed(4)} · {decision.anomalies.global ? "이상 후보" : "임계값 미초과"} ·
          요청량 {decision.evidence.trafficAssessed ? "평가됨" : "미평가"} / 지연 {decision.evidence.latencyAssessed ? "평가됨" : "미평가"}
        </p>)}
      </section>

      <section className="pipeline-card">
        <h3>평가 정답 근거</h3>
        <p>PROCESS 상태를 자동으로 정답으로 간주하지 않습니다. 담당자가 확인한 관측에만 라벨을 남기며, 새 학습 스냅샷은 생성 시점의 라벨을 고정합니다.</p>
        <label>근거 코드
          <input value={labelReason} onChange={(event) => setLabelReason(event.target.value.toUpperCase())}
            placeholder="예: VERIFIED_INCIDENT" maxLength={64} />
        </label>
        <div className="pipeline-review-list">
          {reviewRows.map((row) => (
            <div className="pipeline-review-row" key={row.id}>
              <div><strong>{row.processId ?? "식별 불가"}</strong> · {row.status ?? "상태 미제공"} · 오류 {row.errorCount ?? "미제공"}
                <small>{row.id} · {row.label ?? "미검토"}{row.reasonCode ? ` · ${row.reasonCode}` : ""}</small>
              </div>
              <div>
                <button disabled={busy || !/^[A-Z][A-Z0-9_]{2,63}$/.test(labelReason)}
                  onClick={() => void labelObservation(row, "normal")}>정상 확인</button>
                <button disabled={busy || !/^[A-Z][A-Z0-9_]{2,63}$/.test(labelReason)}
                  onClick={() => void labelObservation(row, "anomaly")}>이상 확인</button>
              </div>
            </div>
          ))}
          {reviewRows.length === 0 && <p>검토할 PROCESS 관측이 없습니다.</p>}
        </div>
      </section>

      <section className="pipeline-card">
        <h3>최근 탐지 판정</h3>
        <div className="pipeline-review-list">
          {decisions.map((decision) => (
            <div className="pipeline-review-row" key={decision.id}>
              <div><strong>{decision.decisionCode ?? "판정 코드 없음"}</strong> · {decision.status}
                <small>모드 {decision.evidence?.requestedMode ?? '이전 구성'} · 실제 완료 경로 {decision.evidence?.actualPaths?.join(', ') ?? '기록 없음'} ·
                  {decision.evidence?.anomalous === true ? '이상 확인' : decision.evidence?.anomalous === false ? '선택 경로 정상' : '최종 정상 여부 미확인'}</small>
                <small>Rule {decision.ruleStatus} · IF {decision.mlStatus} · 점수 {decision.anomalyScore ?? "미측정"}</small>
                <DecisionDiagnostics decision={decision} />
                <button onClick={() => void explain(decision)}>저장된 근거 설명 요청</button>
                {explanations[decision.id] && <><JobState job={explanations[decision.id]} />
                  {explanations[decision.id].resultSummary?.facts?.map(fact => <p key={fact.id}>{fact.name}: {JSON.stringify(fact.value)}</p>)}
                  {explanations[decision.id].resultSummary?.nextChecks?.map(check => <p key={check.code}>{check.text}</p>)}</>}
                <small>{decision.processObservationId} · {new Date(decision.decidedAt).toLocaleString()}</small>
              </div>
            </div>
          ))}
          {decisions.length === 0 && emptyResultVisible(refreshStatus, "decisions") && <p>저장된 판정이 없습니다.</p>}
        </div>
      </section>

      <section className="pipeline-card">
        <h3>Collector API 실시간성 측정</h3>
        <p>최근 10분 · 응답 시간과 DB 저장 시간을 측정합니다. 원천 발생→수집 지연은 원천 시각 미검증으로 미측정입니다.</p>
        {collectorMetrics.length === 0 && <p>해당 원천의 API 수집 측정값이 없습니다.</p>}
        {collectorMetrics.map(row => <p key={row.endpoint}>{row.endpoint}: {row.requests}회 · 오류 {row.failures}회 ·
          빈 응답 {row.emptyResponses}회 · 비어 있지 않은 응답 {row.nonemptyResponses}회 ·
          p95 {row.p95Ms?.toFixed(1) ?? '미측정'} ms · p99 {row.p99Ms?.toFixed(1) ?? '미측정'} ms ·
          DB 저장 p95 {row.persistP95Ms?.toFixed(1) ?? '미측정'} ms · 조회 간격 p95 {row.pollGapP95Seconds?.toFixed(2) ?? '미측정'}초</p>)}
        {collectorState.filter(row => row.sourceId===sourceId).map(row => <p key={row.endpoint}>{row.endpoint} 수집 상태: {row.errorCode === 'monitoring_log_contract_required' ? '응답 축적 중 · 탐지용 로그 변환 명세 확인 필요' :
          row.errorCode === 'source_auth_rejected' ? '원천 API 토큰 확인 필요' : row.errorCode === 'source_unavailable' ? '원천 API 연결 실패' : row.errorCode ?? '조회 성공'}</p>)}
        <h3>운영 상태</h3>
        <div className="pipeline-metrics">
          {queues.map((row) => <p key={`${row.queue}-${row.status}`}>{row.queue} · {row.status}: {row.count}건</p>)}
          {stages.map((row) => <p key={row.stage}>{row.stage}: p95 {Number(row.p95_ms).toFixed(1)} ms ({row.count}건)</p>)}
        </div>
        <h4>변수 결측률 변화</h4>
        {drift.length === 0 ? emptyResultVisible(refreshStatus, "drift") && <p>측정 결과가 없습니다.</p> : drift.map((row) => (
          <p key={`${row.featureName}-${row.measuredAt}`}>
            {row.featureName}: 기준 {row.baselineMissingRate ?? "미확인"} → 최근 {row.recentMissingRate ?? "미확인"}
            {row.reviewRequired ? " · 재학습 검토 필요" : row.reasonCode ? ` · ${row.reasonCode}` : ""}
          </p>
        ))}
      </section>
    </main>
  );
}

// known=false: 작업 이력 조회가 아직 성공하지 않았거나 실패 중이므로 '없음'을 표시하지 않는다.
function JobState({ job, known = true }: { job?: PipelineJob; known?: boolean }) {
  if (!job) return known ? <p>등록된 작업이 없습니다.</p> : null;
  return <p>작업 {job.id.slice(0, 8)} · {job.status}{job.errorCode ? ` · ${job.errorCode}` : ""}
    {job.attemptCount !== undefined && ` · 시도 ${job.attemptCount}${job.maxAttempts !== undefined ? `/${job.maxAttempts}` : ""}`}
    {job.status === "queued" && job.nextRunAt && ` · 다음 실행 ${new Date(job.nextRunAt).toLocaleString()}`}</p>;
}
