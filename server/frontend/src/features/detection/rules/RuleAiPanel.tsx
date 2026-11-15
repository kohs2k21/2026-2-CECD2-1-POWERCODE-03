import { useEffect, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ErrorState } from "@/components/ui/feedback";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSourcesQuery } from "@/features/operations/data/inputs";
import { useLlmStatus } from "@/hooks/useLlmStatus";
import { DASH, numberText } from "@/features/operations/format";
import { useAccountQuery } from "@/lib/query";
import { cn } from "@/lib/utils";
import { HttpError } from "@/services/api/client";
import { jobRequestKey } from "@/services/api/job-idempotency";
import { GENERATION_SERVICE_UNAVAILABLE } from "@/services/api/llm.api";
import { fetchPipelineJob, type PipelineJob } from "@/services/api/pipeline.api";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { SOFT_TERM, errorCode, numberLabel, operatorLabels, type DraftSoftRule, type RuleDraft } from "./ruleDraft";
import {
  CANDIDATE_BASIS, basisLabel, candidateApprovalValue, candidateRows, candidateTargets, heldWindowExcludedRows, parseRecommendation, progressText,
  recommendationProgress, recommendationRequest, recommendationRows, selectable, startRecommendation,
  type CandidateApprovalValue, type CandidateRow, type RecommendationItem, type RecommendationResult, type RecommendationRow, type RecommendationStatus,
} from "./ruleAiApi";
import type { RuleSetState, SoftRuleField } from "./ruleSetApi";
import type { Approval, ThresholdCandidate } from "./candidates/candidateApi";
import { approvedThreshold } from "./candidates/candidateApproval";
import { APPROVE, APPROVED, THRESHOLD_SECTION, thresholdText } from "./candidates/candidateLabels";

const STATUS_LABEL: Record<RecommendationStatus, string> = { proposed: "제안", request_evidence: "근거 부족", deferred: "보류", no_applicable: "해당 없음", rejected: "거부" };
const SOURCE_LABEL = { data_p95: "데이터 p95", ai_proposal: "AI 제안" } as const;

/** 실행한 대상: 켜진 규칙 수와 함께 보낸 처리 시간 후보(10차). 결과 행·검증은 실행 시점 후보로 한다(그 뒤 인터페이스를 바꿔도 같음). */
type Targets = { count: number; candidates: ThresholdCandidate[] };
type AiRun =
  | { status: "idle" }
  | ({ status: "running" } & Targets)
  | { status: "failed"; code: string }
  /** 작업 상태 조회만 실패. 작업은 살아 있을 수 있어 같은 작업을 다시 읽는다. */
  | ({ status: "readFailed"; code: string } & Targets)
  /** `note`: 후보 행이 있는 결과에서 [적용]한 뒤의 적용 안내(후보 행을 닫지 않으려고 결과를 그대로 둔다). */
  | { status: "done"; result: RecommendationResult; unpicked: string[]; candidates: ThresholdCandidate[]; note?: string }
  | { status: "applied"; note: string };

const pending = (job: PipelineJob | undefined) => job?.status === "queued" || job?.status === "running";

/** 규칙 추천 화면의 후보 결과 중 AI 규칙 추천이 읽는 값(candidateApi `CandidateList.result`). */
type CandidateResult = { snapshotId: string | null; sourceId: string | null; thresholdCandidates: ThresholdCandidate[] } | null | undefined;

/**
 * 작업 실행·결과 상태. 대상은 저장된 버전이라 미저장 변경이 있으면 시작하지 않는다(화면은 `저장 후 추천`).
 * LLM 생성 서비스가 꺼져 있으면(GET /api/llm/status, llm-recommendation-job.md '생성 서비스 가용성' 절) 시작하지 않고 코드를 그대로 보인다.
 * 수식 추천(threshold-stats, LLM 없음)은 이 상태와 무관하다.
 * (10차) 대상 = 저장된 켜진 규칙 + 지금 보이는 처리 시간 후보(candidate 상태). 이상 징후 규칙이 0개여도 후보가 있으면 시작한다.
 */
export const useRuleAi = ({ state, dirty, reload, candidateResult }: {
  state: RuleSetState; dirty: boolean; reload: () => Promise<unknown>; candidateResult?: CandidateResult;
}) => {
  const { user } = useAuthSession();
  const sources = useSourcesQuery();
  const llm = useLlmStatus();
  const [selectedSource, setSelectedSource] = useState("");
  const [run, setRun] = useState<AiRun>({ status: "idle" });
  const [jobId, setJobId] = useState<string | null>(null);
  const sourceId = sources.data?.find((source) => source.id === selectedSource)?.id ?? sources.data?.[0]?.id ?? "";
  const count = state.softRules.filter((rule) => rule.enabled).length;
  const candidates = candidateTargets(candidateResult, sourceId);
  const job = useAccountQuery({
    name: "threshold-recommendation-job", params: [jobId], read: (signal) => fetchPipelineJob(jobId!, signal),
    enabled: Boolean(jobId), staleTime: 0, isRunning: (data) => run.status === "running" && pending(data),
  });

  useEffect(() => {
    if (run.status !== "running" || !jobId) return;
    if (job.isError) {
      if (!job.isFetching) setRun({ status: "readFailed", code: errorCode(job.error), count: run.count, candidates: run.candidates });
    } else if (job.data?.status === "failed") {
      setRun({ status: "failed", code: job.data.errorCode ?? "recommendation_failed" });
    } else if (job.data?.status === "succeeded") {
      const result = parseRecommendation(job.data.resultSummary, state, run.candidates.map((item) => item.candidateId));
      setRun(result ? { status: "done", result, unpicked: [], candidates: run.candidates } : { status: "failed", code: "recommendation_result_invalid" });
    }
  }, [job.data, job.error, job.isError, job.isFetching, jobId, run, state]);
  // 버전이 바뀌면 이전 버전 기준 결과는 닫는다. 실패 표시는 남긴다(충돌 뒤 재조회로 버전이 바뀌어도 안내가 사라지지 않게).
  // (10차) 후보 행이 있는 결과는 남긴다: 후보 승인 1건마다 새 버전이 저장되므로, 닫으면 나머지 후보의 AI 결과를 다시 계산해야 한다.
  useEffect(() => {
    setRun((current) => (current.status === "failed" || (current.status === "done" && current.candidates.length > 0) ? current : { status: "idle" }));
    setJobId(null);
  }, [state.revision]);

  const start = async () => {
    setJobId(null);
    setRun({ status: "running", count, candidates: candidates?.items ?? [] });
    const body = recommendationRequest(sourceId, state.revision, candidates);
    try {
      setJobId(await startRecommendation(body, jobRequestKey(user?.id ?? "", body, true)));
    } catch (error) {
      setRun({ status: "failed", code: errorCode(error) });
      if (error instanceof HttpError && error.code === "rule_set_revision_conflict") await reload();
      // 상태 조회가 늦어 접수에서 거절됐으면(503) 가용성을 바로 다시 읽어 버튼 상태를 맞춘다.
      if (error instanceof HttpError && error.code === GENERATION_SERVICE_UNAVAILABLE) void llm.refresh();
    }
  };
  return {
    run, setRun, start, sources: sources.data ?? [], sourcesReady: sources.isSuccess, sourcesError: sources.isError ? errorCode(sources.error) : null,
    /** 실행 중 진행률(running일 때만, 형식이 맞을 때만). 진행 막대 값과 `처리 n/전체` 문구로 쓴다. */
    progress: run.status === "running" && job.data?.status === "running" ? recommendationProgress(job.data.resultSummary) : null,
    sourceId, setSelectedSource, count, dirty, llmUnavailable: llm.unavailableCode,
    /** 대상(켜진 규칙 또는 처리 시간 후보)이 하나라도 있어야 시작한다. */
    canStart: run.status !== "running" && run.status !== "readFailed" && (count > 0 || Boolean(candidates)) && !dirty && Boolean(sourceId) && !llm.unavailableCode,
    /** 같은 작업을 다시 읽는다(새 작업을 만들지 않음). */
    retryRead: () => {
      if (run.status !== "readFailed") return;
      void job.refetch();
      setRun({ status: "running", count: run.count, candidates: run.candidates });
    },
    close: () => {
      setRun({ status: "idle" });
      setJobId(null);
    },
  };
};
type RuleAi = ReturnType<typeof useRuleAi>;

export const RuleAiControls = ({ ai }: { ai: RuleAi }) => (
  <>
    {ai.dirty && <span className="text-sm text-muted-foreground">저장 후 추천</span>}
    {ai.sourcesError && <span role="alert" className="text-sm text-destructive-text">출처 조회 실패 · {ai.sourcesError}</span>}
    {ai.llmUnavailable && ai.run.status !== "running" && (
      <span data-llm-unavailable="" className="text-sm text-destructive-text">AI 규칙 추천 · {ai.llmUnavailable}</span>
    )}
    {ai.sourcesReady && ai.sources.length === 0 && <span className="text-sm text-muted-foreground">저장된 데이터 출처가 없습니다.</span>}
    {ai.sources.length > 1 && (
      <div className="w-full sm:w-56">
        <NativeSelect aria-label="출처" value={ai.sourceId} onChange={(event) => ai.setSelectedSource(event.target.value)}>
          {ai.sources.map((source) => <NativeSelectOption key={source.id} value={source.id}>{source.displayName} ({source.datasetKey})</NativeSelectOption>)}
        </NativeSelect>
      </div>
    )}
    <Button size="sm" variant="outline" disabled={!ai.canStart} onClick={() => void ai.start()}>
      {ai.run.status === "running" ? "AI 규칙 추천 실행 중…" : "AI 규칙 추천"}
    </Button>
  </>
);


/** 근거 칸(규칙 행·후보 행 공통): 출처·표본·min·max·p95, 기준(basis), 근거 문장. */
const EvidenceCell = ({ item }: { item: RecommendationItem }) => {
  const { evidence } = item;
  const basis = basisLabel(item.basis);
  return (
    <TableCell className="min-w-56 whitespace-normal">
      <span className="grid">
        <span>
          {SOURCE_LABEL[item.source]} · 표본 {numberText(evidence.n)} · min {evidence.min === null ? DASH : numberLabel(evidence.min)} · max {evidence.max === null ? DASH : numberLabel(evidence.max)} · p95 {evidence.p95 === null ? DASH : numberLabel(evidence.p95)}
        </span>
        {basis && <span data-ai-basis="" className="text-xs text-muted-foreground">{basis}</span>}
        {item.rationale && <span className="text-xs text-muted-foreground">{item.rationale}</span>}
      </span>
    </TableCell>
  );
};
const StatusCell = ({ item, children }: { item: RecommendationItem; children?: ReactNode }) => (
  <TableCell className="whitespace-normal">
    <span className="flex flex-wrap items-center gap-1">
      <Badge variant={item.status === "proposed" ? "success" : "secondary"}>{STATUS_LABEL[item.status]}</Badge>
      {item.rejectedReason}
      {children}
    </span>
  </TableCell>
);

const ResultRow = ({ row, field, picked, onPick }: { row: RecommendationRow; field: SoftRuleField | undefined; picked: boolean; onPick: (value: boolean) => void }) => {
  const { rule, item } = row;
  const ok = selectable(row);
  const unit = field?.unit ?? item.unit;
  return (
    <TableRow data-ai-row={rule.key} className={cn(!ok && "text-muted-foreground")}>
      <TableCell><Checkbox aria-label={`${rule.name} 선택`} checked={ok && picked} disabled={!ok} onCheckedChange={(value) => onPick(value === true)} /></TableCell>
      <TableCell className="whitespace-normal">
        <span className="grid">
          {rule.name}
          <span className="text-xs text-muted-foreground">{field?.displayName ?? rule.field} · {rule.field}</span>
        </span>
      </TableCell>
      <TableCell className="whitespace-nowrap">
        {operatorLabels[rule.operator]} {numberLabel(rule.threshold)} {unit}
        {ok && (
          <>
            {" → "}
            <span title={`원값 ${item.threshold}`} data-raw={item.threshold}>
              {operatorLabels[item.operator ?? rule.operator]} {numberLabel(item.displayThreshold)} {unit}
            </span>
          </>
        )}
      </TableCell>
      <StatusCell item={item} />
      <EvidenceCell item={item} />
    </TableRow>
  );
};

/** 후보 행 [승인]이 쓰는 승인 흐름(candidates/approvalFlow `useRowApproval`)의 필요한 부분. */
export type CandidateApprover = {
  busy: boolean;
  start: (candidateId: string, value?: CandidateApprovalValue) => void;
  noteFor: (candidateId: string) => ReactNode;
};

/**
 * (10차) 처리 시간 후보 행: 임계값 칸은 Trainer 값(기준 · 학습 기간 전체 p95)과 AI 값(제안일 때, 기준 · 학습 기간 앞 80% p95)을 각 기준과 함께 보인다
 * (두 p95를 기준 표시 없이 나란히 두지 않음 — llm-recommendation-job.md 10차 결정 1). [승인]은 후보 승인 흐름 그대로이고 저장 값만 고른다:
 * AI가 제안이면 AI 값, 아니면 Trainer 값(결정 5). 이미 같은 조건이 있으면 승인됨, 저장 형식이 닫혀 있으면 꺼짐(목록 행과 같음).
 */
const CandidateResultRow = ({ row, field, state, approver, available }: {
  row: CandidateRow; field: SoftRuleField | undefined; state: RuleSetState; approver: CandidateApprover | undefined; available: Approval | undefined;
}) => {
  const { candidate, item } = row;
  const proposed = item.status === "proposed";
  const approved = candidate.field ? approvedThreshold(state, { field: candidate.field, scope: candidate.scope }) : null;
  const canApprove = Boolean(approver && available?.available) && approved === false && !approver?.busy;
  const aiBasis = basisLabel(item.basis);
  const unit = field?.unit ?? item.unit;
  return (
    <TableRow data-ai-candidate-row={candidate.candidateId} className={cn(!proposed && "text-muted-foreground")}>
      <TableCell>
        <span className="grid justify-items-start gap-1">
          <Button size="sm" disabled={!canApprove} onClick={() => approver?.start(candidate.candidateId, candidateApprovalValue(row))}>{APPROVE}</Button>
          {approver?.noteFor(candidate.candidateId)}
        </span>
      </TableCell>
      <TableCell className="whitespace-normal">
        <span className="grid">
          <span className="font-mono text-sm">{candidate.scope.processId}</span>
          <span className="text-xs text-muted-foreground">
            {candidate.scope.adapterType ?? "모든 어댑터"} · {field?.displayName ?? item.field} · {item.field}
          </span>
        </span>
      </TableCell>
      <TableCell className="whitespace-nowrap">
        <span className="grid">
          <span data-candidate-threshold="">{thresholdText(candidate)}</span>
          <span data-candidate-basis="" className="text-xs text-muted-foreground">{CANDIDATE_BASIS}</span>
          {proposed && item.operator && (
            <>
              <span title={`원값 ${item.threshold}`} data-raw={item.threshold}>
                {"→ "}{operatorLabels[item.operator]} {numberLabel(item.displayThreshold)} {unit}
              </span>
              {aiBasis && <span data-ai-basis="" className="text-xs text-muted-foreground">{aiBasis}</span>}
            </>
          )}
        </span>
      </TableCell>
      <StatusCell item={item}>{approved && <Badge variant="secondary" data-approved>{APPROVED}</Badge>}</StatusCell>
      <EvidenceCell item={item} />
    </TableRow>
  );
};

const COLUMNS = ["선택", "규칙(대상 필드)", "임계값", "상태", "근거"];
const GroupRow = ({ title }: { title: string }) => (
  <TableRow data-ai-group="">
    <TableHead colSpan={COLUMNS.length} scope="colgroup" className="bg-muted/50">{title}</TableHead>
  </TableRow>
);
/** 대상 수 문구: 후보가 없으면 10차 전 문구 그대로(`켜진 이상 징후 규칙 n개`). */
const targetsText = (count: number, candidates: number) => `켜진 ${SOFT_TERM} ${count}개${candidates ? ` · ${THRESHOLD_SECTION} ${candidates}개` : ""}`;

/**
 * 와이어프레임 renderRuleAi: 실행 중 → 결과 표 → 적용(입력칸만). 실패는 작업 전체 실패, 부분 결과 없음.
 * (10차) 처리 시간 후보를 함께 보냈으면 표 아래 묶음에 후보 행([승인])을 둔다. 후보 행이 있으면 [적용] 뒤에도 결과를 닫지 않는다.
 */
export const RuleAiBox = ({
  ai, state, draft, fields, onApply, approver, available,
}: {
  ai: RuleAi; state: RuleSetState; draft: RuleDraft; fields: SoftRuleField[];
  onApply: (updates: { key: string; patch: Pick<DraftSoftRule, "threshold" | "operator"> }[]) => void;
  /** 후보 행 [승인]. 없으면 후보 행 [승인]은 꺼진다. */
  approver?: CandidateApprover; available?: Approval;
}) => {
  const { run } = ai;
  if (run.status === "idle") return null;
  const close = <Button size="sm" variant="outline" onClick={ai.close}>닫기</Button>;
  const box = "grid gap-3 rounded-lg border p-3";
  const line = "flex flex-wrap items-center gap-2 text-sm";
  if (run.status === "running") {
    return (
      <div className={box} data-rule-ai="running">
        <div className={line}>
          <Badge variant="info">실행 중</Badge>
          <span>{targetsText(run.count, run.candidates.length)}{ai.llmUnavailable ? ` · ${ai.llmUnavailable}` : ""}</span>
        </div>
        {ai.progress ? (
          <>
            <Progress value={ai.progress.processed} max={ai.progress.total} aria-label="AI 규칙 추천 실행 중" />
            <span data-ai-progress="" className="text-sm text-muted-foreground">{progressText(ai.progress)}</span>
          </>
        ) : (
          <Progress value={100} className="animate-pulse" aria-label="AI 규칙 추천 실행 중" />
        )}
      </div>
    );
  }
  if (run.status === "readFailed") {
    return (
      <div className={box} data-rule-ai="readFailed">
        <ErrorState title={`AI 규칙 추천 조회 실패 · ${run.code}`} onRetry={ai.retryRead} />
        <div className={line}>{close}</div>
      </div>
    );
  }
  if (run.status === "failed") {
    const timeout = run.code === "generation_timeout";
    return (
      <div className={box} data-rule-ai="failed">
        <p role="alert" className="text-sm text-destructive-text">
          {timeout
            ? `시간 초과 · ${run.code} · 응답 기한 안에 결과가 오지 않았습니다. 부분 결과는 없고 입력칸은 그대로입니다.`
            : `추천 실패 · ${run.code} · 작업 전체가 실패해 부분 결과가 없습니다. 입력칸은 그대로입니다.`}
        </p>
        <div className={line}>
          <Badge variant={timeout ? "warning" : "critical"}>{timeout ? "시간 초과" : "실패"}</Badge>
          <Button size="sm" variant="outline" disabled={!ai.canStart} onClick={() => void ai.start()}>다시 시도</Button>
          {close}
        </div>
      </div>
    );
  }
  if (run.status === "applied") {
    return (
      <div className={box} data-rule-ai="applied">
        <div role="status" className={line}><Badge variant="success">적용됨</Badge><span>{run.note} · 저장해야 운영에 반영됩니다</span>{close}</div>
      </div>
    );
  }
  const rows = recommendationRows(run.result, state, draft);
  const candidates = candidateRows(run.result, run.candidates);
  const proposed = rows.filter(selectable);
  const picked = proposed.filter((row) => !run.unpicked.includes(row.rule.key));
  const proposedCount = proposed.length + candidates.filter(({ item }) => item.status === "proposed").length;
  const heldExcluded = heldWindowExcludedRows(run.result);
  const pick = (key: string, value: boolean) =>
    ai.setRun({ ...run, unpicked: value ? run.unpicked.filter((item) => item !== key) : [...run.unpicked, key] });
  const apply = () => {
    onApply(picked.map(({ rule, item }) => ({ key: rule.key, patch: { threshold: item.threshold!, operator: item.operator ?? rule.operator } })));
    const note = `${picked.length}건을 입력칸에 채움(${picked.map(({ rule }) => rule.name).join(", ")})`;
    // 후보 행이 있으면 결과를 그대로 두고(후보 승인이 남음) 채운 행은 선택을 푼다 — 같은 값을 두 번 채우지 않게.
    ai.setRun(candidates.length
      ? { ...run, note, unpicked: [...run.unpicked, ...picked.map(({ rule }) => rule.key)] }
      : { status: "applied", note });
  };
  const fieldOf = (id: string) => fields.find((item) => item.fieldId === id);
  return (
    <div className={box} data-rule-ai="done">
      <div className={line}>
        <Badge variant="success">완료</Badge>
        <span className="text-muted-foreground">{targetsText(rows.length, candidates.length)} · 제안 {proposedCount}건</span>
        {heldExcluded !== null && <span data-ai-held="" className="text-muted-foreground">보류 기간 제외 · {numberText(heldExcluded)}행</span>}
      </div>
      {run.note && (
        <div role="status" data-rule-ai-applied="" className={line}><Badge variant="success">적용됨</Badge><span>{run.note} · 저장해야 운영에 반영됩니다</span></div>
      )}
      <Table aria-label="AI 규칙 추천">
        <TableHeader>
          <TableRow>{COLUMNS.map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
        </TableHeader>
        <TableBody>
          {candidates.length > 0 && rows.length > 0 && <GroupRow title={SOFT_TERM} />}
          {rows.map((row) => (
            <ResultRow
              key={row.rule.key} row={row} field={fieldOf(row.rule.field)}
              picked={!run.unpicked.includes(row.rule.key)} onPick={(value) => pick(row.rule.key, value)}
            />
          ))}
          {candidates.length > 0 && <GroupRow title={THRESHOLD_SECTION} />}
          {candidates.map((row) => (
            <CandidateResultRow
              key={row.candidate.candidateId} row={row} field={fieldOf(row.item.field)} state={state} approver={approver} available={available}
            />
          ))}
        </TableBody>
      </Table>
      {rows.length > 0 ? (
        <>
          <p className="text-sm text-muted-foreground">제안 상태만 선택 가능 · 적용하면 조건·임계값 입력칸만 채웁니다(위험도는 그대로)</p>
          <div className={line}>
            <Button size="sm" disabled={picked.length === 0} onClick={apply}>적용</Button>
            {close}
            <span className="text-muted-foreground">선택 {picked.length}건</span>
          </div>
        </>
      ) : (
        <div className={line}>{close}</div>
      )}
    </div>
  );
};
