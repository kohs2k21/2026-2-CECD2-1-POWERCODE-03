import { Link } from "react-router-dom";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { paths } from "@/app/routes";
import type { ProcessDecision } from "@/services/api/pipeline.api";
import { DecisionDiagnostics } from "./components/DecisionDiagnostics";
import { jobPending, useExplainJob, useSavedDecisions } from "./data/savedDecisions";

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

const DecisionCard = ({ decision }: { decision: ProcessDecision }) => {
  const { job, request } = useExplainJob(decision);
  const current = job.data;
  return (
    <SectionCard title={decision.decisionCode ?? "판정 코드 없음"} headingLevel={2}>
      <div className="grid gap-1 text-sm">
        {decision.evidence?.input && (
          <p>
            {decision.evidence.input.mode === "csv" ? "CSV 재생" : "API 수집"} ·{" "}
            {decision.evidence.input.configurationScope === "input-validation" ? "선택한 후보 구성으로 검증" : "운영 구성"}
          </p>
        )}
        {decision.evidence?.demo && <p>과거 데이터 재생 데모 · {decision.evidence.demo.runKey}</p>}
        <p>
          {decision.status} · Rule {decision.ruleStatus} · ML {decision.mlStatus}
        </p>
        <DecisionDiagnostics decision={decision} />
        <p>
          {decision.evidence?.anomalous === true
            ? "이상 확인"
            : decision.evidence?.anomalous === false
              ? "선택 경로 정상"
              : "정상 여부 미확인"}{" "}
          · 모드 {decision.evidence?.requestedMode ?? "이전 구성"}
        </p>
        <p className="text-muted-foreground">{new Date(decision.decidedAt).toLocaleString()}</p>
      </div>
      <div>
        <Button
          variant="outline"
          size="sm"
          disabled={!!decision.evidence?.demo || request.isPending || jobPending(current)}
          onClick={() => request.mutate()}
        >
          저장된 근거 설명 요청
        </Button>
      </div>
      {decision.evidence?.demo && (
        <p className="text-sm text-muted-foreground">CPU 모델 판정입니다. 이 데모에서는 LLM 설명을 실행하지 않습니다.</p>
      )}
      {request.isError && (
        <Alert variant="destructive">
          <AlertDescription>{errorText(request.error)}</AlertDescription>
        </Alert>
      )}
      {job.isError && (
        <ErrorState title={errorText(job.error)} onRetry={() => void job.refetch()} />
      )}
      {current && (
        <div className="grid gap-1 text-sm">
          <p>
            설명 작업: {current.status} {current.errorCode}
          </p>
          {current.resultSummary?.facts?.map((fact) => (
            <p key={fact.id}>
              {fact.name}: {JSON.stringify(fact.value)}
            </p>
          ))}
          {current.resultSummary?.nextChecks?.map((check) => (
            <p key={check.code}>{check.text}</p>
          ))}
        </div>
      )}
    </SectionCard>
  );
};

/** 저장된 탐지 판정·근거 설명(기존 연결 유지). 이상 분석 DB 판정 화면으로 합칠 대상(하위 Issue 이상 분석). */
export const SavedDecisionsPage = () => {
  const { query, streamError } = useSavedDecisions();
  const decisions = query.data ?? [];
  return (
    <div className="grid gap-6">
      <PageHeader
        title="저장된 탐지 판정"
        description="Rule·ML 판정의 저장 결과입니다. 설명은 요청할 때 생성하며, 원인은 확정하지 않습니다."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link to={paths.analysis}>이상 분석으로</Link>
          </Button>
        }
      />
      {streamError && (
        <Alert variant="destructive">
          <AlertDescription>{streamError}</AlertDescription>
        </Alert>
      )}
      {query.isPending ? (
        <LoadingState />
      ) : query.isError && decisions.length === 0 ? (
        // 조회 실패는 빈 결과로 숨기지 않는다(이전 데이터가 빈 목록이어도 오류 표시).
        <ErrorState title={errorText(query.error)} onRetry={() => void query.refetch()} />
      ) : decisions.length === 0 ? (
        <EmptyState>저장된 판정이 없습니다.</EmptyState>
      ) : (
        <div className="grid gap-4">
          {query.isError && (
            <ErrorState title={errorText(query.error)} onRetry={() => void query.refetch()} />
          )}
          {decisions.map((decision) => (
            <DecisionCard key={decision.id} decision={decision} />
          ))}
        </div>
      )}
    </div>
  );
};
