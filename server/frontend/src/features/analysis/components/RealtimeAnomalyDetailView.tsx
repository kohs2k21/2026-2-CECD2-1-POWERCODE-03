import { IconArrowLeft, IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { HttpError } from "@/services/api/client";
import type { RealtimeAnomalyEvent } from "@/types/realtime";
import type { CategoryTheme } from "../types";
import type { DetectionEvidence } from "../data/anomalyEvidence";
import { useAnomalyEvidenceQuery } from "../data/useAnomalyEvidenceQuery";
import { severityBadgeVariant } from "../constants";
import { AnomalyFeedback } from "./AnomalyFeedback";

const displayOptional = (value: string | undefined) => value?.trim() || "— 확인 불가";
const executionLabels = {
  completed: "수행 완료",
  partial: "부분 수행",
  unavailable: "판정 불가",
  "not-run": "미수행",
};
const withUnit = (value: number | string | undefined, unit?: string) =>
  value === undefined ? "— 확인 불가" : `${value}${unit ? ` ${unit}` : ""}`;

const EvidenceSection = ({ title, evidence }: { title: string; evidence?: DetectionEvidence }) => (
  <SectionCard title={title} headingLevel={3}>
    <p className="text-sm">
      수행 상태: {evidence ? executionLabels[evidence.execution] : "— 확인 불가"} · 버전:{" "}
      {displayOptional(evidence?.version)}
    </p>
    {evidence?.reason && <p className="text-sm text-muted-foreground">{evidence.reason}</p>}
    {evidence?.measurements.length ? (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>판정 항목</TableHead>
            <TableHead>측정값</TableHead>
            <TableHead>당시 기준</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {evidence.measurements.map((item, index) => (
            <TableRow key={index}>
              <TableHead scope="row" className="h-auto font-normal">
                {item.name}
              </TableHead>
              <TableCell>{withUnit(item.value, item.unit)}</TableCell>
              <TableCell>{withUnit(item.threshold, item.unit)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    ) : (
      <p className="text-sm text-muted-foreground">측정값 · 당시 기준: — 확인 불가</p>
    )}
  </SectionCard>
);

export const RealtimeAnomalyDetailView = ({
  event,
  theme,
  onBack,
  isWide,
  onToggleWide,
}: {
  event: RealtimeAnomalyEvent;
  theme: CategoryTheme;
  onBack: () => void;
  isWide: boolean;
  onToggleWide: (value: boolean) => void;
}) => {
  const { user } = useAuthSession();
  const query = useAnomalyEvidenceQuery(event.eventId);
  const detail = query.data;
  const workflow =
    detail?.workflowStatus ??
    (event.workflowStatus === "Open" || event.workflowStatus === "Resolved" ? event.workflowStatus : undefined);
  const stateLabel =
    detail?.decisionState === "complete"
      ? "전체 판정 완료"
      : detail?.decisionState === "partial"
        ? "부분 판정"
        : detail?.decisionState === "unavailable"
          ? "판정 불가"
          : "판정 수행 범위 확인 불가";
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <div className="flex min-w-0 items-center gap-2">
          <Button variant="ghost" size="icon-sm" onClick={onBack} aria-label="목록으로 돌아가기">
            <IconArrowLeft aria-hidden="true" />
          </Button>
          <span className="text-muted-foreground">이상 이벤트 상세</span>
          <span className="text-muted-foreground">/</span>
          <strong className="truncate">{event.eventId}</strong>
        </div>
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => onToggleWide(!isWide)}
          aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
        >
          {isWide ? <IconArrowsMinimize aria-hidden="true" /> : <IconArrowsMaximize aria-hidden="true" />}
        </Button>
      </div>
      <SectionCard
        title="이상 이벤트 요약"
        headingLevel={3}
        aria-label="이상 이벤트 요약"
        action={<Badge variant={severityBadgeVariant[event.severity]}>{theme.label}</Badge>}
      >
        <p className="text-sm">
          {detail?.summary ??
            `${event.processName ?? "프로세스 정보 미제공"} · 응답 코드 ${event.responseCode} · 이상 점수 ${event.anomalyScore.toFixed(2)}`}
        </p>
        <DescriptionList
          items={[
            { label: "감지 시각", value: displayOptional(event.detectedAt) },
            { label: "당시 구성 버전", value: displayOptional(detail?.configurationVersion) },
            { label: "판정 수행 범위", value: stateLabel },
            {
              label: "업무 분류",
              value: workflow === "Open" ? "보류" : workflow === "Resolved" ? "완료" : "— 확인 불가",
            },
            { label: "거래 ID", value: displayOptional(event.transactionId) },
            { label: "프로세스", value: displayOptional(event.processName) },
            { label: "채널", value: displayOptional(event.channelName) },
            { label: "로그 상태", value: displayOptional(event.status) },
            { label: "응답 코드", value: event.responseCode },
            { label: "처리 시간", value: `${event.processTimeMs} ms` },
            { label: "이상 점수", value: event.anomalyScore },
            { label: "위험 점수 · 등급", value: `${event.riskScore} · ${event.riskLevel}` },
          ]}
        />
        {query.isPending && (
          <p role="status" className="text-sm text-muted-foreground">
            판정 근거 조회 중
          </p>
        )}
        {query.isError && (
          <div role="status" className="flex flex-wrap items-center gap-3 text-sm">
            <p>
              {query.error instanceof HttpError && [404, 503].includes(query.error.status)
                ? "판정 근거 미연결"
                : "판정 근거 조회 실패"}
              {detail ? " · 이전 조회 근거 표시" : " · 추가 근거 확인 불가"}
            </p>
            <Button variant="outline" size="sm" onClick={() => void query.refetch()} disabled={query.isFetching}>
              근거 다시 조회
            </Button>
          </div>
        )}
        {user?.userType === "admin" && (
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline" size="sm">
              <Link to={paths.detectionOperations}>
                {detail?.configurationId ? "당시 구성 보기" : "운영 구성 목록"}
              </Link>
            </Button>
            {detail?.evaluationId && (
              <Button asChild variant="outline" size="sm">
                <Link to={paths.detectionEvaluation(detail.evaluationId)}>관련 평가 보기</Link>
              </Button>
            )}
          </div>
        )}
      </SectionCard>
      <div className="grid gap-4 xl:grid-cols-2">
        <EvidenceSection title="Rule 판정 근거" evidence={detail?.rule} />
        <EvidenceSection title="ML 판정 근거" evidence={detail?.ml} />
      </div>
      <SectionCard title="원인 가설" headingLevel={3}>
        <p className="text-sm text-muted-foreground">아래 내용은 판정 근거와 구분된 원인 가설입니다.</p>
        {detail?.hypotheses.length ? (
          <ul className="list-disc pl-5 text-sm">
            {detail.hypotheses.map((hypothesis, index) => (
              <li key={index}>{hypothesis}</li>
            ))}
          </ul>
        ) : (
          <p className="text-sm">원인 가설: — 확인 불가</p>
        )}
      </SectionCard>
      <AnomalyFeedback
        key={event.eventId}
        eventId={event.eventId}
        capability={!query.isError && detail?.canSaveFeedback === true}
      />
    </div>
  );
};
