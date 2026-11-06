import { useEffect, useRef, useState } from "react";
import { IconBrain, IconLoader2 } from "@tabler/icons-react";
import { motion, useReducedMotion } from "motion/react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import type { PipelineJob } from "@/services/api/pipeline.api";
import type { DecisionDetail } from "../data/analysisApi";
import { analysisErrorTitle } from "../data/analysisApi";
import { readEvidence } from "../data/evidence";
import { jobPending, useExplainJob } from "../data/useAnalysis";
import { useTypewriter } from "../hooks/useTypewriter";
import { jobStatusLabels } from "../labels";

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));
const factValue = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value));

/**
 * AI 판정 근거 요약(POST /api/jobs {kind:"explain"} + GET /api/jobs/:id). 최종 판정만 요청할 수 있다.
 * 제목·요청 버튼 아이콘은 #100 이전 LLM Report(LlmSection)의 IconBrain.
 * 요청 중(queued·running)일 때만 5초마다 결과를 조회하고, 실패·대기는 작업 상태 그대로 보여 준다.
 */
export const ExplainPanel = ({ detail }: { detail: DecisionDetail }) => {
  const finalReady = detail.kind === "final";
  const demo = Boolean(readEvidence(detail.evidence).demo);
  return (
    <SectionCard title="AI 판정 근거 요약" icon={<IconBrain />} headingLevel={2}>
      {finalReady && !demo ? (
        <ExplainBody detail={detail} />
      ) : (
        <div className="grid gap-2 text-sm">
          <div>
            <Button variant="outline" size="sm" disabled>
              <IconBrain aria-hidden="true" />
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
  // 이 화면을 보는 동안 진행 중(queued·running)이던 작업이나 방금 요청한 작업이 완료되면 새로 도착한 결과로 보고 타자 효과로 보인다.
  // 처음 열었을 때 이미 완료된 결과(재방문 포함)는 바로 보인다.
  const watched = useRef(new Set<string>());
  useEffect(() => {
    if (current && jobPending(current)) watched.current.add(current.id);
  }, [current]);
  const fresh =
    current?.status === "succeeded" && (watched.current.has(current.id) || request.data?.created.id === current.id);
  return (
    <div className="grid gap-3 text-sm">
      {!jobId && !request.isPending && <p className="text-muted-foreground">아직 설명을 요청하지 않은 판정입니다.</p>}
      {jobId && job.isPending && !current && <LoadingState />}
      {job.isError && <ErrorState title={analysisErrorTitle(job.error, "설명 작업")} onRetry={() => void job.refetch()} />}
      {current && (
        <div className="flex flex-wrap items-center gap-2" role="status">
          <Badge variant={failed ? "critical" : current.status === "succeeded" ? "success" : "info"}>
            {running && <IconLoader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}
            {running ? "요청 중" : current.status === "succeeded" ? "완료" : failed ? "실패" : (jobStatusLabels[current.status] ?? current.status)}
          </Badge>
          <span className="text-muted-foreground">
            설명 작업 {jobStatusLabels[current.status] ?? current.status}
            {current.errorCode ? ` · ${current.errorCode}` : ""}
          </span>
        </div>
      )}
      {running && (
        // 와이어프레임 dExplain 요청 중 막대(.progress). 상태는 위 role="status" 문구로 전달하므로 막대는 장식.
        <div data-slot="explain-progress" className="h-1.5 w-full overflow-hidden rounded-full bg-info-soft" aria-hidden="true">
          <span className="block h-full w-1/3 animate-progress-slide rounded-full bg-info-text motion-reduce:w-full motion-reduce:animate-none" />
        </div>
      )}
      {current?.status === "succeeded" && <ExplainResult key={current.id} summary={current.resultSummary} fresh={fresh} />}
      {request.isError && <ErrorState title={errorText(request.error)} />}
      {(!current || failed) && (
        <div>
          <Button variant="outline" size="sm" disabled={running || lookupBlocked} onClick={() => request.mutate()}>
            <IconBrain aria-hidden="true" />
            {failed ? "설명 다시 요청" : "설명 요청"}
          </Button>
        </div>
      )}
      <p className="text-xs text-muted-foreground">설명은 저장된 근거를 문장으로 풀 뿐, 이상 신호·검사 완료를 바꾸지 않습니다.</p>
    </div>
  );
};

type ExplainSummary = PipelineJob["resultSummary"];
type ExplainFacts = NonNullable<NonNullable<ExplainSummary>["facts"]>;
type ExplainChecks = NonNullable<NonNullable<ExplainSummary>["nextChecks"]>;
const factLine = (fact: ExplainFacts[number]) => `${fact.name}: ${factValue(fact.value)}`;

/**
 * 설명 결과(저장된 근거 facts + 확인할 것 nextChecks). 새로 도착한 결과는 #100 이전 상세 화면(AnimatedPanel)처럼 아래에서 떠오르며
 * 나타나고, 항목을 차례로 타자 효과(#100 이전 TypewriterText, 12ms/글자)로 보인다. 이미 있던 결과(처음 열기·재방문)는 움직임 없이 바로 보인다.
 * 동작 줄이기 설정이면 효과 없이 전체를 바로 보인다. 타자 효과 중에도 화면 읽기 프로그램에는 숨김 사본으로 전체 문장을
 * 한 번에 제공하고, 글자가 늘어나는 사본은 aria-hidden.
 */
const ExplainResult = ({ summary, fresh }: { summary: ExplainSummary; fresh: boolean }) => {
  const reduceMotion = useReducedMotion();
  const facts = summary?.facts ?? [];
  const checks = summary?.nextChecks ?? [];
  const total = [...facts.map(factLine), ...checks.map((check) => check.text)].reduce((sum, line) => sum + line.length, 0);
  const [typing] = useState(fresh && !reduceMotion && total > 0);
  const { shown, done } = useTypewriter(total, typing);
  // 마운트할 때 한 번 정한다(재렌더로 바뀌지 않음). 이미 있던 결과·동작 줄이기 = initial false(움직임 없음).
  const [enter] = useState(() => (reduceMotion || !fresh ? false : { opacity: 0, y: 10, scale: 0.995 }));
  return (
    <motion.div
      className="grid gap-3"
      data-entrance={enter ? "fresh" : "none"}
      initial={enter}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
    >
      {typing && !done ? (
        <>
          <div className="sr-only">
            <ExplainLines facts={facts} checks={checks} />
          </div>
          <div className="grid gap-3" aria-hidden="true" data-typing="true">
            <ExplainLines facts={facts} checks={checks} shown={shown} />
          </div>
        </>
      ) : (
        <ExplainLines facts={facts} checks={checks} />
      )}
    </motion.div>
  );
};

/** `shown`(보이는 글자 수)이 있으면 facts → nextChecks 순서로 그만큼만 보인다(아직 닿지 않은 항목·제목은 숨김). 없으면 전체. */
const ExplainLines = ({ facts, checks, shown }: { facts: ExplainFacts; checks: ExplainChecks; shown?: number }) => {
  let offset = 0;
  const cut = (line: string) => {
    const start = offset;
    offset += line.length;
    if (shown === undefined) return line;
    return shown > start ? line.slice(0, shown - start) : null;
  };
  const factItems = facts.map((fact) => ({ key: fact.id, text: cut(factLine(fact)) })).filter((entry) => entry.text !== null);
  const checkItems = checks.map((check) => ({ key: check.code, text: cut(check.text) })).filter((entry) => entry.text !== null);
  return (
    <>
      {factItems.length ? (
        <ul className="grid list-disc gap-1 pl-5 wrap-anywhere">
          {factItems.map((entry) => (
            <li key={entry.key}>{entry.text}</li>
          ))}
        </ul>
      ) : null}
      {checkItems.length ? (
        <>
          <h3 className="font-medium">확인할 것(원인 후보)</h3>
          <ul className="grid list-disc gap-1 pl-5">
            {checkItems.map((entry) => (
              <li key={entry.key}>{entry.text}</li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
};
