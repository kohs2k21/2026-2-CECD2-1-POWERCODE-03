import { useEffect } from "react";
import { IconLoader2 } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { useLlmStatus } from "@/hooks/useLlmStatus";
import { HttpError } from "@/services/api/client";
import { GENERATION_SERVICE_UNAVAILABLE } from "@/services/api/llm.api";
import { errorCode } from "../ruleDraft";
import { ChannelChain } from "./ChannelChain";
import {
  EXPLANATION_RUNNING, EXPLANATION_TITLE, RESULT_UNREADABLE, REVIEW_POINTS_TITLE, checkFailures, explanationNote, readExplanationResult, splitPathIds,
  type ProtocolExplanationRequest, type ProtocolExplanationResult,
} from "./protocolExplanation";
import { explanationPending, useProtocolExplanation } from "./useProtocolExplanation";

/** 문장 한 줄: 경로 id(`P1`…)는 id + 채널 칩·화살표(허용 경로 표시와 같은 ChannelChain). */
const Sentence = ({ text, paths }: { text: string; paths: ProtocolExplanationResult["paths"] }) => (
  <>
    {splitPathIds(text, paths).map((part, index) =>
      "pathId" in part ? (
        <span key={index} data-explanation-path={part.pathId} className="mx-0.5 inline-flex flex-wrap items-center gap-1 align-middle">
          <span className="font-mono text-xs">{part.pathId}</span>
          <ChannelChain channels={part.channels} className="inline-flex" />
        </span>
      ) : (
        <span key={index}>{part.text}</span>
      ),
    )}
  </>
);

const ExplanationResult = ({ result, headingLevel }: { result: ProtocolExplanationResult; headingLevel: 2 | 3 }) => {
  const Heading = headingLevel === 2 ? "h3" : "h4";
  return (
    <div className="grid gap-3" data-explanation-result>
      <p className="leading-7 whitespace-pre-wrap wrap-anywhere" data-explanation-text>
        <Sentence text={result.explanation} paths={result.paths} />
      </p>
      {result.reviewPoints.length > 0 && (
        <div className="grid gap-1" data-review-points>
          <Heading className="font-medium">{REVIEW_POINTS_TITLE}</Heading>
          <ul className="grid list-disc gap-1 pl-5 wrap-anywhere">
            {result.reviewPoints.map((point, index) => <li key={index}><Sentence text={point} paths={result.paths} /></li>)}
          </ul>
        </div>
      )}
      <p className="text-xs text-muted-foreground" data-explanation-note>{explanationNote(result.ruleSetRevision)}</p>
    </div>
  );
};

/**
 * 프로토콜 경로 설명 칸(관리자 화면에서만 그린다 — 호출하는 화면이 관리자를 확인). 제목 "LLM 설명", 오른쪽에 요청 버튼.
 * - 요청 중(접수·queued·running): "설명을 만드는 중입니다…" + 진행 막대, 버튼 비활성
 * - 성공: 본문 `explanation`, "확인할 점"(`reviewPoints`, 없으면 숨김), 작은 글씨 안내(버전 N = 결과 `ruleSetRevision`)
 * - 실패: `<항목> 실패 · <코드>`(작업 `errorCode`, 점검 실패는 사유 코드를 이어 붙임 — 문장은 서버가 저장하지 않음), 접수 거절도 같은 형식(400·403·503 코드 그대로)
 * - LLM 꺼짐(#168 `GET /api/llm/status`): 버튼 비활성 + `<항목> · <코드>`. 이미 요청 중인 작업은 그대로 보인다
 */
export const ProtocolExplanationCard = ({ request, itemLabel, buttonLabel, headingLevel = 2 }: {
  request: ProtocolExplanationRequest; itemLabel: string; buttonLabel: string; headingLevel?: 2 | 3;
}) => {
  const { jobId, lookup, job, current, request: send, mutation } = useProtocolExplanation(request);
  const llm = useLlmStatus();
  const { refresh } = llm;
  // 상태 조회가 늦어 접수에서 거절됐으면(503) 가용성을 바로 다시 읽어 버튼 상태를 맞춘다(판정 설명과 같음).
  useEffect(() => {
    if (mutation.error instanceof HttpError && mutation.error.code === GENERATION_SERVICE_UNAVAILABLE) void refresh();
  }, [mutation.error, refresh]);
  const running = mutation.isPending || explanationPending(current) || (Boolean(jobId) && !current && job.isPending);
  const failed = current?.status === "failed";
  const result = current?.status === "succeeded" ? readExplanationResult(current.resultSummary) : null;
  const unreadable = current?.status === "succeeded" && !result;
  // 기존 작업을 아직 찾지 못했으면(목록 읽는 중) 새 요청을 막는다. 목록 읽기 실패는 막지 않는다 — 같은 대상이면 서버가 기존 작업을 돌려준다.
  const lookupPending = lookup.isPending && !jobId;
  const canRequest = !current || failed || unreadable;
  const failureCode = failed ? [current.errorCode ?? "job_failed", ...checkFailures(current.resultSummary)].join(" · ") : unreadable ? RESULT_UNREADABLE : null;
  return (
    <SectionCard
      title={EXPLANATION_TITLE}
      headingLevel={headingLevel}
      aria-label={EXPLANATION_TITLE}
      action={canRequest ? (
        <Button size="sm" variant="outline" disabled={running || lookupPending || Boolean(llm.unavailableCode)} onClick={send}>{buttonLabel}</Button>
      ) : undefined}
    >
      <div className="grid gap-3 text-sm" data-protocol-explanation>
        {lookupPending && <LoadingState />}
        {lookup.isError && !jobId && (
          <ErrorState title={`${itemLabel} 조회 실패 · ${errorCode(lookup.error)}`} onRetry={() => void lookup.refetch()} retryLabel="다시 조회" />
        )}
        {job.isError && !current && (
          <ErrorState title={`${itemLabel} 조회 실패 · ${errorCode(job.error)}`} onRetry={() => void job.refetch()} retryLabel="다시 조회" />
        )}
        {running && (
          <>
            <div className="flex flex-wrap items-center gap-2" role="status" data-explanation-running>
              <Badge variant="info">
                <IconLoader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                {EXPLANATION_RUNNING}
              </Badge>
            </div>
            <div data-slot="explain-progress" className="h-1.5 w-full overflow-hidden rounded-full bg-info-soft" aria-hidden="true">
              <span className="block h-full w-1/3 animate-progress-slide rounded-full bg-info-text motion-reduce:w-full motion-reduce:animate-none" />
            </div>
          </>
        )}
        {current && !running && !failed && current.status !== "succeeded" && (
          <p className="text-muted-foreground" role="status" data-explanation-status>{itemLabel} · {current.status}</p>
        )}
        {failureCode && <ErrorState title={`${itemLabel} 실패 · ${failureCode}`} />}
        {mutation.isError && <ErrorState title={`${itemLabel} 실패 · ${errorCode(mutation.error)}`} />}
        {result && <ExplanationResult result={result} headingLevel={headingLevel} />}
        {llm.unavailableCode && !running && canRequest && (
          <p data-llm-unavailable="" className="text-destructive-text">{itemLabel} · {llm.unavailableCode}</p>
        )}
      </div>
    </SectionCard>
  );
};
