import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import type { DecisionDetail } from "../data/analysisApi";
import { analysisErrorTitle } from "../data/analysisApi";
import { readEvidence } from "../data/evidence";
import { jobPending, useExplainJob } from "../data/useAnalysis";
import { jobStatusLabels } from "../labels";

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));
const factValue = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/**
 * AI 설명(POST /api/jobs {kind:"explain"} + GET /api/jobs/:id). 최종 판정만 요청할 수 있다.
 * 요청 중(queued·running)일 때만 5초마다 결과를 조회하고, 실패·대기는 작업 상태 그대로 보여 준다.
 */
export const ExplainPanel = ({ detail }: { detail: DecisionDetail }) => {
  const finalReady = detail.kind === "final";
  const demo = Boolean(readEvidence(detail.evidence).demo);
  return (
    <SectionCard title="AI 설명" headingLevel={2}>
      {finalReady && !demo ? (
        <ExplainBody detail={detail} />
      ) : (
        <div className="grid gap-2 text-sm">
          <div>
            <Button variant="outline" size="sm" disabled>
              설명 요청
            </Button>
          </div>
          <p className="text-muted-foreground">
            {demo ? "CPU 모델 판정입니다. 이 데모에서는 LLM 설명을 실행하지 않습니다." : "최종 판정 후 요청할 수 있습니다."}
          </p>
        </div>
      )}
    </SectionCard>
  );
};

const ExplainBody = ({ detail }: { detail: DecisionDetail }) => {
  const { jobId, job, request } = useExplainJob(detail);
  const current = job.data;
  const running = request.isPending || jobPending(current);
  const failed = current?.status === "failed";
  // 기존 설명 작업(jobId)을 아직 읽지 못했거나 읽기에 실패하면 새 요청을 막는다(중복 요청 방지, 오류는 그대로 표시·다시 시도).
  const lookupBlocked = Boolean(jobId) && !current && (job.isPending || job.isError);
  const summary = current?.resultSummary;
  return (
    <div className="grid gap-3 text-sm">
      {!jobId && !request.isPending && <p className="text-muted-foreground">아직 설명을 요청하지 않은 판정입니다.</p>}
      {jobId && job.isPending && !current && <LoadingState />}
      {job.isError && <ErrorState title={analysisErrorTitle(job.error, "설명 작업")} onRetry={() => void job.refetch()} />}
      {current && (
        <div className="flex flex-wrap items-center gap-2" role="status">
          <Badge variant={failed ? "critical" : current.status === "succeeded" ? "success" : "info"}>
            {running ? "요청 중" : current.status === "succeeded" ? "완료" : failed ? "실패" : (jobStatusLabels[current.status] ?? current.status)}
          </Badge>
          <span className="text-muted-foreground">
            설명 작업 {jobStatusLabels[current.status] ?? current.status}
            {current.errorCode ? ` · ${current.errorCode}` : ""}
          </span>
        </div>
      )}
      {current?.status === "succeeded" && (
        <>
          {summary?.facts?.length ? (
            <ul className="grid list-disc gap-1 pl-5">
              {summary.facts.map((fact) => (
                <li key={fact.id}>
                  {fact.name}: {factValue(fact.value)}
                </li>
              ))}
            </ul>
          ) : null}
          {summary?.nextChecks?.length ? (
            <>
              <h3 className="font-medium">확인할 것(원인 후보)</h3>
              <ul className="grid list-disc gap-1 pl-5">
                {summary.nextChecks.map((check) => (
                  <li key={check.code}>{check.text}</li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      )}
      {request.isError && <ErrorState title={errorText(request.error)} />}
      {(!current || failed) && (
        <div>
          <Button variant="outline" size="sm" disabled={running || lookupBlocked} onClick={() => request.mutate()}>
            {failed ? "설명 다시 요청" : "설명 요청"}
          </Button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">설명은 저장된 근거를 문장으로 풀 뿐, 이상 신호·검사 완료를 바꾸지 않습니다.</p>
    </div>
  );
};
