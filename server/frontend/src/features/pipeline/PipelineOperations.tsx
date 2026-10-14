import { useCallback, useEffect, useMemo, useState } from "react";
import {
  adoptSuggestedRule, createPipelineJob, fetchActiveConfiguration, fetchAppliedConfigurations,
  fetchApplication, fetchDriftMetrics, fetchPipelineJob, fetchPipelineJobs,
  fetchPipelineMetrics, fetchProcessDecisions, fetchReviewObservations, fetchSources,
  requestApplication, submitObservationLabel,
  fetchWindowDecisions, fetchWindowMonitors, type WindowDecision, type WindowMonitor,
  type ActiveConfiguration, type Application, type DriftMetric,
  type PipelineJob, type ProcessDecision, type QueueMetric, type ReviewObservation, type Source, type StageMetric,
} from "../../services/api/pipeline.api";
import "./pipeline.css";

type JobKind = PipelineJob["kind"];
type JobMap = Partial<Record<JobKind, PipelineJob>>;

function localInput(value: Date): string {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 16);
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "요청을 완료하지 못했습니다.";
}

export function PipelineOperations() {
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [from, setFrom] = useState(() => localInput(new Date(Date.now() - 30 * 86400_000)));
  const [until, setUntil] = useState(() => localInput(new Date()));
  const [history, setHistory] = useState<PipelineJob[]>([]);
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

  const refresh = useCallback(async () => {
    const [sourceRows, jobRows, activeRow, metrics, driftRows, appliedRows, decisionRows, windows, monitors] = await Promise.all([
      fetchSources(), fetchPipelineJobs(), fetchActiveConfiguration(),
      fetchPipelineMetrics(), fetchDriftMetrics(), fetchAppliedConfigurations(), fetchProcessDecisions(),
      fetchWindowDecisions(), fetchWindowMonitors(),
    ]);
    setSources(sourceRows);
    setSourceId((current) => current || sourceRows[0]?.id || "");
    setActive(activeRow);
    setQueues(metrics.queues);
    setStages(metrics.stages);
    setDrift(driftRows);
    setApplied(appliedRows);
    setHistory(jobRows);
    setDecisions(decisionRows);
    setWindowDecisions(windows);
    setWindowMonitors(monitors);
  }, []);

  const jobs = useMemo(() => {
    const sourceJobs = history.filter((job) => job.inputSpec.sourceId === sourceId);
    const latest: JobMap = {};
    latest.recommend_feature = sourceJobs.find((job) => job.kind === "recommend_feature");
    const setId = latest.recommend_feature?.resultSummary?.featureSetId;
    latest.recommend_rule = sourceJobs.find((job) => job.kind === "recommend_rule" && job.inputSpec.featureSetId === setId);
    latest.train = sourceJobs.find((job) => job.kind === "train" && job.inputSpec.featureSetId === setId);
    latest.evaluate = sourceJobs.find((job) => job.kind === "evaluate" &&
      job.inputSpec.configVersionId === latest.train?.resultSummary?.configVersionId);
    return latest;
  }, [history, sourceId]);

  useEffect(() => {
    void refresh().catch((reason) => setError(errorText(reason)));
  }, [refresh]);

  useEffect(() => {
    if (!sourceId) return;
    void fetchReviewObservations(sourceId).then(setReviewRows).catch((reason) => setError(errorText(reason)));
  }, [sourceId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      const pending = Object.values(jobs).filter(
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
            void fetchActiveConfiguration().then(setActive);
            void fetchAppliedConfigurations().then(setApplied);
          }
        }).catch((reason) => setError(errorText(reason)));
      }
      void fetchProcessDecisions().then(setDecisions).catch((reason) => setError(errorText(reason)));
      void fetchWindowDecisions().then(setWindowDecisions).catch((reason) => setError(errorText(reason)));
      void fetchWindowMonitors().then(setWindowMonitors).catch((reason) => setError(errorText(reason)));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [jobs, application]);

  const featureSetId = jobs.recommend_feature?.resultSummary?.featureSetId;
  const ruleSuggestions = jobs.recommend_rule?.resultSummary?.suggestions ?? [];
  const configId = jobs.train?.resultSummary?.configVersionId;
  const snapshotId = jobs.train?.resultSummary?.snapshotId;
  const evaluationId = jobs.evaluate?.resultSummary?.evaluationId;
  const canApply = jobs.evaluate?.resultSummary?.applicability === "valid" && Boolean(configId && evaluationId);
  const selectedSource = useMemo(() => sources.find((source) => source.id === sourceId), [sources, sourceId]);

  async function submit(kind: JobKind) {
    if (!sourceId || !from || !until) return;
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
        if (kind === "train") input.ruleVersionIds = ruleIds;
      }
      const job = await createPipelineJob(input);
      setHistory((previous) => [job, ...previous.filter((item) => item.id !== job.id)]);
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
        <button type="button" onClick={() => void refresh().catch((reason) => setError(errorText(reason)))}>새로고침</button>
      </header>

      {error && <p className="pipeline-error" role="alert">{error}</p>}
      <section className="pipeline-card">
        <h3>대상 데이터</h3>
        <div className="pipeline-fields">
          <label>출처
            <select value={sourceId} onChange={(event) => { setSourceId(event.target.value); setRuleIds([]); }}>
              {sources.map((source) => <option key={source.id} value={source.id}>{source.displayName} ({source.datasetKey})</option>)}
            </select>
          </label>
          <label>시작 시각<input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
          <label>종료 시각<input type="datetime-local" value={until} onChange={(event) => setUntil(event.target.value)} /></label>
        </div>
        <p>기간은 저장 시각 기준입니다. 출처 시각의 시간대가 확인되기 전에는 발생 시각으로 해석하지 않습니다.</p>
        {!selectedSource && <p>저장된 데이터 출처가 없습니다. 먼저 Collector로 로그를 저장하세요.</p>}
      </section>

      <section className="pipeline-grid">
        <div className="pipeline-card">
          <h3>1. 파생변수 생성</h3>
          <button disabled={busy || !sourceId} onClick={() => void submit("recommend_feature")}>변수 생성 작업 등록</button>
          <JobState job={jobs.recommend_feature} />
          {jobs.recommend_feature?.resultSummary?.stopReason && <p>{jobs.recommend_feature.resultSummary.stopReason}</p>}
          {jobs.recommend_feature?.resultSummary?.reviews?.map((review, index) => <p key={index}>{review.state}: {review.reason}</p>)}
          {featureSetId && <p>고정 변수 구성: <code>{featureSetId}</code><br />정제 행: {jobs.recommend_feature?.resultSummary?.materializedCount ?? 0}</p>}
        </div>
        <div className="pipeline-card">
          <h3>2. Rule 추천·채택</h3>
          <button disabled={busy || !featureSetId} onClick={() => void submit("recommend_rule")}>Rule 추천 작업 등록</button>
          <JobState job={jobs.recommend_rule} />
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
          <h3>3. IF 학습</h3>
          <button disabled={busy || !featureSetId} onClick={() => void submit("train")}>선택 기간 학습 실행</button>
          <JobState job={jobs.train} />
          {configId && <p>후보 구성: <code>{configId}</code></p>}
        </div>
        <div className="pipeline-card">
          <h3>4. 평가·승인</h3>
          <button disabled={busy || !configId || !snapshotId} onClick={() => void submit("evaluate")}>검증 분할 평가</button>
          <JobState job={jobs.evaluate} />
          <p>평가 적합성: {jobs.evaluate?.resultSummary?.applicability ?? "미평가"}</p>
          <button disabled={busy || !canApply} onClick={() => void apply()}>평가된 구성 적용 요청</button>
          {application && <p>적용 상태: {application.status}{application.errorCode ? ` · ${application.errorCode}` : ""}</p>}
          <p>현재 활성 구성: <code>{active?.configVersionId ?? "없음"}</code></p>
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
        <h3>1분 시간창 탐지</h3>
        {windowMonitors.length === 0 && <p>등록된 시간창 감시가 없습니다.</p>}
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
                <small>Rule {decision.ruleStatus} · IF {decision.mlStatus} · 점수 {decision.anomalyScore ?? "미측정"}</small>
                <small>{decision.processObservationId} · {new Date(decision.decidedAt).toLocaleString()}</small>
              </div>
            </div>
          ))}
          {decisions.length === 0 && <p>저장된 판정이 없습니다.</p>}
        </div>
      </section>

      <section className="pipeline-card">
        <h3>운영 상태</h3>
        <div className="pipeline-metrics">
          {queues.map((row) => <p key={`${row.queue}-${row.status}`}>{row.queue} · {row.status}: {row.count}건</p>)}
          {stages.map((row) => <p key={row.stage}>{row.stage}: p95 {Number(row.p95_ms).toFixed(1)} ms ({row.count}건)</p>)}
        </div>
        <h4>변수 결측률 변화</h4>
        {drift.length === 0 ? <p>측정 결과가 없습니다.</p> : drift.map((row) => (
          <p key={`${row.featureName}-${row.measuredAt}`}>
            {row.featureName}: 기준 {row.baselineMissingRate ?? "미확인"} → 최근 {row.recentMissingRate ?? "미확인"}
            {row.reviewRequired ? " · 재학습 검토 필요" : row.reasonCode ? ` · ${row.reasonCode}` : ""}
          </p>
        ))}
      </section>
    </main>
  );
}

function JobState({ job }: { job?: PipelineJob }) {
  if (!job) return <p>등록된 작업이 없습니다.</p>;
  return <p>작업 {job.id.slice(0, 8)} · {job.status}{job.errorCode ? ` · ${job.errorCode}` : ""}</p>;
}
