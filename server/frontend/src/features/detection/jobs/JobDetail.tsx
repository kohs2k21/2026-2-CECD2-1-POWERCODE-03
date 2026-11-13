import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { paths } from "@/app/routes";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { TrainerJobResult } from "@/features/detection/components/TrainerJobResult";
import { fullText } from "@/features/detection/models/period";
import { parameterLabel } from "@/features/detection/models/trainDraft";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, numberText, stampText } from "@/features/operations/format";
import { jobResultLink, jobStatusBadge, jobTarget, kindLabel, modelName, type JobNames } from "./jobFormat";
import type { JobAttempt, JobDetail } from "./jobsApi";
import { useJob, useJobNames } from "./useJobs";

const Mono = ({ children }: { children: string }) => <span className="font-mono text-xs break-all">{children}</span>;
const text = (value: unknown) => (typeof value === "string" && value ? value : null);
const id8 = (value: unknown) => text(value)?.slice(0, 8) ?? DASH;
const seconds = (iso: string | null | undefined) => stampText(iso, true);
const ATTEMPT_LABELS: Record<string, string> = { running: "실행 중", succeeded: "성공", failed: "실패" };
const attemptLabel = (status: JobAttempt["status"]) => ATTEMPT_LABELS[status] ?? status;

/** 작업 단계(와이어프레임 '작업 단계'): 저장된 시각·시도 기록만으로 그린다. 기록에 없는 단계는 만들지 않는다. */
const stages = (job: JobDetail): { mark: string; text: string }[] => {
  const rows = [{ mark: "✓", text: `접수 · ${seconds(job.createdAt)}` }];
  const attempts = Array.isArray(job.attempts) ? job.attempts : [];
  attempts.forEach((attempt) => {
    const end = attempt.finishedAt ? ` · 끝 ${seconds(attempt.finishedAt)}` : "";
    rows.push({
      mark: attempt.status === "running" ? "▶" : attempt.status === "succeeded" ? "✓" : "✕",
      text: `시도 ${attempt.attemptNo} · ${attemptLabel(attempt.status)} · 시작 ${seconds(attempt.startedAt)}${end}${attempt.errorCode ? ` · ${attempt.errorCode}` : ""}`,
    });
  });
  if (job.status === "queued") {
    rows.push({ mark: "·", text: job.errorCode ? `재시도 대기 · 다음 실행 ${seconds(job.nextRunAt)}` : attempts.length ? "대기" : "대기 · 실행 전" });
  }
  if (job.status === "succeeded") rows.push({ mark: "✓", text: `완료 · ${seconds(job.finishedAt)}` });
  if (job.status === "failed") rows.push({ mark: "✕", text: `실패 · ${seconds(job.finishedAt)}${job.errorCode ? ` · ${job.errorCode}` : ""}` });
  return rows;
};

const periodOf = (input: Record<string, unknown>) =>
  text(input.from) && text(input.until) ? `${fullText(String(input.from))} ~ ${fullText(String(input.until))}` : DASH;
const parametersText = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value) || !Object.keys(value).length) return "코드 기본값";
  return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${parameterLabel(key)} ${JSON.stringify(item)}`).join(" · ");
};

/** 작업 입력 요약(종류별). 값은 작업 입력 그대로이고, 이름은 조회되면 붙인다. 전체 입력은 아래 `작업 입력 원문`. */
const inputItems = (job: JobDetail, names: JobNames): { label: string; value: ReactNode }[] => {
  const input = job.inputSpec ?? {};
  const version = (id: unknown) => (text(id) ? <Link className="text-link" to={paths.detectionModel(String(id))}>{names.versionLabel?.(String(id)) ?? id8(id)}</Link> : DASH);
  const set = () => (text(input.evaluationSetId) ? (names.setName?.(String(input.evaluationSetId)) ?? id8(input.evaluationSetId)) : DASH);
  const snapshot = { label: "스냅샷", value: text(input.snapshotId) ? <Mono>{String(input.snapshotId)}</Mono> : DASH };
  switch (job.kind) {
    case "train":
      return [
        { label: "모델", value: modelName(names.methods, input.modelKey) },
        { label: "학습 기간", value: text(input.from) ? periodOf(input) : text(input.snapshotId) ? `스냅샷 ${id8(input.snapshotId)}` : DASH },
        { label: "입력 변수 세트", value: text(input.featureSetId) ? <Mono>{String(input.featureSetId)}</Mono> : DASH },
        { label: "요청 파라미터", value: parametersText(input.parameters) },
        { label: "제외 구간", value: `${Array.isArray(input.excludedIntervals) ? input.excludedIntervals.length : 0}구간` },
      ];
    case "evaluate":
      return [{ label: "모델 버전", value: version(input.configVersionId) }, snapshot, { label: "용도", value: text(input.purpose) ?? DASH }];
    case "evaluation_set":
      return [{ label: "평가 세트", value: set() }, snapshot, { label: "용도", value: text(input.purpose) ?? DASH }];
    case "compare": {
      const ids = Array.isArray(input.configVersionIds) ? input.configVersionIds : [];
      const baselines = Array.isArray(input.baselines) ? (input.baselines as { configVersionId?: unknown }[]) : [];
      const join = (items: unknown[]) => (items.length ? items.map((item, index) => <span key={index}>{index > 0 && ", "}{version(item)}</span>) : "없음");
      return [
        { label: "평가 세트", value: set() },
        { label: "비교 모델", value: join(ids) },
        { label: "기준 운영 모델", value: join(baselines.map((item) => item.configVersionId)) },
      ];
    }
    case "recommend_thresholds": {
      const data = (input.data && typeof input.data === "object" ? input.data : {}) as Record<string, unknown>;
      return [
        { label: "룰 버전", value: typeof input.ruleSetRevision === "number" ? `버전 ${input.ruleSetRevision}` : DASH },
        { label: "데이터", value: text(data.snapshotId) ? `스냅샷 ${id8(data.snapshotId)}` : periodOf(data) },
        { label: "방식", value: text(input.mode) ?? DASH },
      ];
    }
    case "recommend_relations":
      return [snapshot];
    default:
      return [{ label: "대상", value: jobTarget(job, names) }];
  }
};

/** 실패 뒤 다시 하기(와이어프레임): 같은 화면의 새 작업으로 간다. 같은 입력 재요청 버튼은 아직 없다. */
const restart = (job: JobDetail): { to: string; label: string } | null => {
  if (job.status !== "failed") return null;
  if (job.kind === "train") return { to: `${paths.detectionModels}?step=1`, label: "새 학습" };
  if (job.kind === "compare" || job.kind === "evaluation_set") return { to: `${paths.detectionEvaluations}?step=1`, label: "새 비교 평가" };
  if (job.kind === "recommend_relations") return { to: paths.detectionRuleCandidates, label: "규칙 추천" };
  if (job.kind === "recommend_thresholds") return { to: paths.detectionRules, label: "룰 설정" };
  return null;
};

const JobBody = ({ job }: { job: JobDetail }) => {
  const names = useJobNames();
  const result = job.resultSummary && typeof job.resultSummary === "object" ? job.resultSummary : null;
  const failedCount = typeof result?.failedCount === "number" ? result.failedCount : null;
  const badge = jobStatusBadge(job, failedCount);
  const link = jobResultLink(job, {
    configVersionId: text(result?.configVersionId), comparisonId: text(result?.comparisonId),
  }, names);
  const again = restart(job);
  return (
    <SectionCard title={`${kindLabel(job.kind)} 작업 ${job.id.slice(0, 8)}`} action={<Badge variant={badge.variant} data-job-status={job.status}>{badge.label}</Badge>}>
      {job.status === "running" && <div role="status" aria-label="작업 진행 중" className="h-2 w-full animate-pulse rounded-full bg-primary/40" />}
      {job.status === "failed" && (
        <Alert variant="destructive" role="alert"><AlertDescription>실패 · {job.errorCode ?? DASH}</AlertDescription></Alert>
      )}
      {job.status === "queued" && job.errorCode && (
        <Alert role="status"><AlertDescription>재시도 대기 · {job.errorCode} · 시도 {job.attemptCount}/{job.maxAttempts}</AlertDescription></Alert>
      )}
      <div className="grid gap-2">
        <h3 className="text-sm font-semibold">작업 단계</h3>
        <ol aria-label="작업 단계" className="grid gap-1 text-sm">
          {stages(job).map((row, index) => <li key={index}><span aria-hidden="true" className="inline-block w-5 text-muted-foreground">{row.mark}</span>{row.text}</li>)}
        </ol>
      </div>
      <DescriptionList
        items={[
          { label: "종류", value: kindLabel(job.kind) },
          ...inputItems(job, names),
          { label: "요청 시각", value: stampText(job.createdAt) },
          { label: "끝난 시각", value: stampText(job.finishedAt) },
          { label: "시도", value: `${numberText(job.attemptCount)}/${numberText(job.maxAttempts)}` },
          { label: "작업 ID", value: <Mono>{job.id}</Mono> },
        ]}
      />
      {(link || again) && (
        <div className="flex flex-wrap gap-2">
          {link && <Button asChild size="sm"><Link to={link.to}>결과 보기 · {link.label}</Link></Button>}
          {again && <Button asChild size="sm" variant="outline"><Link to={again.to}>{again.label}</Link></Button>}
        </div>
      )}
      {job.status === "succeeded" && (job.kind === "train" || job.kind === "evaluate") && (
        <details className="text-sm">
          <summary className="cursor-pointer">결과 요약</summary>
          <div className="mt-2 grid gap-1"><TrainerJobResult job={job} /></div>
        </details>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer">작업 입력 원문</summary>
        <pre className="mt-2 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">{JSON.stringify(job.inputSpec, null, 2)}</pre>
      </details>
    </SectionCard>
  );
};

/** 탐지 설정 › 작업 상세(와이어프레임 #93 '작업 상세'): 상태·작업 단계(시도 기록)·입력 요약·실패 코드·결과 연결. 대기·실행 중이면 5초마다 다시 읽는다. */
export const JobDetailPage = () => {
  const { jobId = "" } = useParams();
  const job = useJob(jobId);
  return (
    <div className="grid gap-6">
      <ScreenTitle>작업 상세 {jobId}</ScreenTitle>
      {job.isPending && <LoadingState>작업 조회 중…</LoadingState>}
      {job.isError && <ErrorState title={`작업 조회 실패 · ${errorCode(job.error)}`} onRetry={() => void job.refetch()} retryLabel="다시 조회" />}
      {job.data && <JobBody job={job.data} />}
    </div>
  );
};
