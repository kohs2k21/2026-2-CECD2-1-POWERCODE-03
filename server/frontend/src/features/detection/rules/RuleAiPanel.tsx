import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ErrorState } from "@/components/ui/feedback";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSourcesQuery } from "@/features/operations/data/inputs";
import { DASH, numberText } from "@/features/operations/format";
import { useAccountQuery } from "@/lib/query";
import { cn } from "@/lib/utils";
import { HttpError } from "@/services/api/client";
import { jobRequestKey } from "@/services/api/job-idempotency";
import { fetchPipelineJob, type PipelineJob } from "@/services/api/pipeline.api";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { SOFT_TERM, errorCode, numberLabel, operatorLabels, type DraftSoftRule, type RuleDraft } from "./ruleDraft";
import {
  parseRecommendation, recommendationRequest, recommendationRows, selectable, startRecommendation,
  type RecommendationResult, type RecommendationRow, type RecommendationStatus,
} from "./ruleAiApi";
import type { RuleSetState, SoftRuleField } from "./ruleSetApi";

const STATUS_LABEL: Record<RecommendationStatus, string> = { proposed: "제안", request_evidence: "근거 부족", deferred: "보류", no_applicable: "해당 없음", rejected: "거부" };
const SOURCE_LABEL = { data_p95: "데이터 p95", ai_proposal: "AI 제안" } as const;

type AiRun =
  | { status: "idle" }
  | { status: "running"; count: number }
  | { status: "failed"; code: string }
  /** 작업 상태 조회만 실패. 작업은 살아 있을 수 있어 같은 작업을 다시 읽는다. */
  | { status: "readFailed"; code: string; count: number }
  | { status: "done"; result: RecommendationResult; unpicked: string[] }
  | { status: "applied"; note: string };

const pending = (job: PipelineJob | undefined) => job?.status === "queued" || job?.status === "running";

/** 작업 실행·결과 상태. 대상은 저장된 버전이라 미저장 변경이 있으면 시작하지 않는다(화면은 `저장 후 추천`). */
export const useRuleAi = ({ state, dirty, reload }: { state: RuleSetState; dirty: boolean; reload: () => Promise<unknown> }) => {
  const { user } = useAuthSession();
  const sources = useSourcesQuery();
  const [selectedSource, setSelectedSource] = useState("");
  const [run, setRun] = useState<AiRun>({ status: "idle" });
  const [jobId, setJobId] = useState<string | null>(null);
  const sourceId = sources.data?.find((source) => source.id === selectedSource)?.id ?? sources.data?.[0]?.id ?? "";
  const count = state.softRules.filter((rule) => rule.enabled).length;
  const job = useAccountQuery({
    name: "threshold-recommendation-job", params: [jobId], read: (signal) => fetchPipelineJob(jobId!, signal),
    enabled: Boolean(jobId), staleTime: 0, isRunning: (data) => run.status === "running" && pending(data),
  });

  useEffect(() => {
    if (run.status !== "running" || !jobId) return;
    if (job.isError) {
      if (!job.isFetching) setRun({ status: "readFailed", code: errorCode(job.error), count: run.count });
    } else if (job.data?.status === "failed") {
      setRun({ status: "failed", code: job.data.errorCode ?? "recommendation_failed" });
    } else if (job.data?.status === "succeeded") {
      const result = parseRecommendation(job.data.resultSummary, state);
      setRun(result ? { status: "done", result, unpicked: [] } : { status: "failed", code: "recommendation_result_invalid" });
    }
  }, [job.data, job.error, job.isError, job.isFetching, jobId, run, state]);
  // 버전이 바뀌면 이전 버전 기준 결과는 닫는다. 실패 표시는 남긴다(충돌 뒤 재조회로 버전이 바뀌어도 안내가 사라지지 않게).
  useEffect(() => {
    setRun((current) => (current.status === "failed" ? current : { status: "idle" }));
    setJobId(null);
  }, [state.revision]);

  const start = async () => {
    setJobId(null);
    setRun({ status: "running", count });
    const body = recommendationRequest(sourceId, state.revision);
    try {
      const started = await startRecommendation(body, jobRequestKey(user?.id ?? "", body, true), state);
      if ("jobId" in started) {
        setJobId(started.jobId);
      } else {
        const result = parseRecommendation(started.result, state);
        setRun(result ? { status: "done", result, unpicked: [] } : { status: "failed", code: "recommendation_result_invalid" });
      }
    } catch (error) {
      setRun({ status: "failed", code: errorCode(error) });
      if (error instanceof HttpError && error.code === "rule_set_revision_conflict") await reload();
    }
  };
  return {
    run, setRun, start, sources: sources.data ?? [], sourcesReady: sources.isSuccess, sourcesError: sources.isError ? errorCode(sources.error) : null,
    sourceId, setSelectedSource, count, dirty,
    canStart: run.status !== "running" && run.status !== "readFailed" && count > 0 && !dirty && Boolean(sourceId),
    /** 같은 작업을 다시 읽는다(새 작업을 만들지 않음). */
    retryRead: () => {
      if (run.status !== "readFailed") return;
      void job.refetch();
      setRun({ status: "running", count: run.count });
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

const ResultRow = ({ row, field, picked, onPick }: { row: RecommendationRow; field: SoftRuleField | undefined; picked: boolean; onPick: (value: boolean) => void }) => {
  const { rule, item } = row;
  const ok = selectable(row);
  const unit = field?.unit ?? item.unit;
  const { evidence } = item;
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
      <TableCell className="whitespace-normal">
        <span className="flex flex-wrap items-center gap-1">
          <Badge variant={ok ? "success" : "secondary"}>{STATUS_LABEL[item.status]}</Badge>
          {item.rejectedReason}
        </span>
      </TableCell>
      <TableCell className="min-w-56 whitespace-normal">
        <span className="grid">
          <span>
            {SOURCE_LABEL[item.source]} · 표본 {numberText(evidence.n)} · min {evidence.min === null ? DASH : numberLabel(evidence.min)} · max {evidence.max === null ? DASH : numberLabel(evidence.max)} · p95 {evidence.p95 === null ? DASH : numberLabel(evidence.p95)}
          </span>
          {item.rationale && <span className="text-xs text-muted-foreground">{item.rationale}</span>}
        </span>
      </TableCell>
    </TableRow>
  );
};

/** 와이어프레임 renderRuleAi: 실행 중 → 결과 표 → 적용(입력칸만). 실패는 작업 전체 실패, 부분 결과 없음. */
export const RuleAiBox = ({
  ai, state, draft, fields, onApply,
}: {
  ai: RuleAi; state: RuleSetState; draft: RuleDraft; fields: SoftRuleField[];
  onApply: (updates: { key: string; patch: Pick<DraftSoftRule, "threshold" | "operator"> }[]) => void;
}) => {
  const { run } = ai;
  if (run.status === "idle") return null;
  const close = <Button size="sm" variant="outline" onClick={ai.close}>닫기</Button>;
  const box = "grid gap-3 rounded-lg border p-3";
  const line = "flex flex-wrap items-center gap-2 text-sm";
  if (run.status === "running") {
    return (
      <div className={box} data-rule-ai="running">
        <div className={line}><Badge variant="info">실행 중</Badge><span>켜진 {SOFT_TERM} {run.count}개</span></div>
        <Progress value={100} className="animate-pulse" aria-label="AI 규칙 추천 실행 중" />
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
  const proposed = rows.filter(selectable);
  const picked = proposed.filter((row) => !run.unpicked.includes(row.rule.key));
  const pick = (key: string, value: boolean) =>
    ai.setRun({ ...run, unpicked: value ? run.unpicked.filter((item) => item !== key) : [...run.unpicked, key] });
  const apply = () => {
    onApply(picked.map(({ rule, item }) => ({ key: rule.key, patch: { threshold: item.threshold!, operator: item.operator ?? rule.operator } })));
    ai.setRun({ status: "applied", note: `${picked.length}건을 입력칸에 채움(${picked.map(({ rule }) => rule.name).join(", ")})` });
  };
  return (
    <div className={box} data-rule-ai="done">
      <div className={line}>
        <Badge variant="success">완료</Badge>
        <span className="text-muted-foreground">켜진 {SOFT_TERM} {rows.length}개 · 제안 {proposed.length}건</span>
      </div>
      <Table aria-label="AI 규칙 추천">
        <TableHeader>
          <TableRow>{["선택", "규칙(대상 필드)", "임계값", "상태", "근거"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <ResultRow
              key={row.rule.key} row={row} field={fields.find((item) => item.fieldId === row.rule.field)}
              picked={!run.unpicked.includes(row.rule.key)} onPick={(value) => pick(row.rule.key, value)}
            />
          ))}
        </TableBody>
      </Table>
      <p className="text-sm text-muted-foreground">제안 상태만 선택 가능 · 적용하면 조건·임계값 입력칸만 채웁니다(위험도는 그대로)</p>
      <div className={line}>
        <Button size="sm" disabled={picked.length === 0} onClick={apply}>적용</Button>
        {close}
        <span className="text-muted-foreground">선택 {picked.length}건</span>
      </div>
    </div>
  );
};
