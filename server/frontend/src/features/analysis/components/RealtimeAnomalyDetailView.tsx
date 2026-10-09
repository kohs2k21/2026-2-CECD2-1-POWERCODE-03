import {
  IconArrowLeft,
  IconArrowsMaximize,
  IconArrowsMinimize,
} from "@tabler/icons-react";
import { Link } from "react-router-dom";
import { AnimatedPanel } from "../../../components/layout/AnimatedPanel";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { useAuthSession } from "../../../services/auth/AuthSessionProvider";
import { HttpError } from "../../../services/api/client";
import type { RealtimeAnomalyEvent } from "../../../types/realtime";
import type { CategoryTheme } from "../types";
import type { DetectionEvidence } from "../data/anomalyEvidence";
import { useAnomalyEvidenceQuery } from "../data/useAnomalyEvidenceQuery";
import { AnomalyFeedback } from "./AnomalyFeedback";
const displayOptional = (value: string | undefined) =>
  value?.trim() || "— 확인 불가";
const executionLabels = {
  completed: "수행 완료",
  partial: "부분 수행",
  unavailable: "판정 불가",
  "not-run": "미수행",
};
const EvidenceSection = ({
  title,
  evidence,
}: {
  title: string;
  evidence?: DetectionEvidence;
}) => (
  <section className="analysis-section-card anomaly-evidence-section">
    <h3>{title}</h3>
    <p>
      수행 상태:{" "}
      {evidence ? executionLabels[evidence.execution] : "— 확인 불가"} · 버전:{" "}
      {displayOptional(evidence?.version)}
    </p>
    {evidence?.reason && <p>{evidence.reason}</p>}
    {evidence?.measurements.length ? (
      <div className="anomaly-evidence-table">
        <table>
          <thead>
            <tr>
              <th>판정 항목</th>
              <th>측정값</th>
              <th>당시 기준</th>
            </tr>
          </thead>
          <tbody>
            {evidence.measurements.map((item, index) => (
              <tr key={index}>
                <th scope="row">{item.name}</th>
                <td>
                  {item.value === undefined
                    ? "— 확인 불가"
                    : `${item.value}${item.unit ? ` ${item.unit}` : ""}`}
                </td>
                <td>
                  {item.threshold === undefined
                    ? "— 확인 불가"
                    : `${item.threshold}${item.unit ? ` ${item.unit}` : ""}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <p>측정값 · 당시 기준: — 확인 불가</p>
    )}
  </section>
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
    (event.workflowStatus === "Open" || event.workflowStatus === "Resolved"
      ? event.workflowStatus
      : undefined);
  const stateLabel =
    detail?.decisionState === "complete"
      ? "전체 판정 완료"
      : detail?.decisionState === "partial"
        ? "부분 판정"
        : detail?.decisionState === "unavailable"
          ? "판정 불가"
          : "판정 수행 범위 확인 불가";
  return (
    <AnimatedPanel
      className={`analysis-detail-view ${isWide ? "analysis-detail-view--wide" : ""}`}
    >
      <div className="analysis-detail-breadcrumb">
        <div className="analysis-detail-breadcrumb__left">
          <Button variant="ghost" size="icon" onClick={onBack}>
            <IconArrowLeft size={16} aria-hidden="true" />
            <span className="sr-only">목록으로 돌아가기</span>
          </Button>
          <span>이상 이벤트 상세</span>
          <span>/</span>
          <strong>{event.eventId}</strong>
        </div>
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => onToggleWide(!isWide)}
          aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
        >
          {isWide ? (
            <IconArrowsMinimize size={16} />
          ) : (
            <IconArrowsMaximize size={16} />
          )}
        </Button>
      </div>
      <section className="analysis-section-card" aria-label="이상 이벤트 요약">
        <div className="analysis-section-card__toolbar analysis-section-card__toolbar--compact">
          <h3>이상 이벤트 요약</h3>
          <Badge
            variant={
              event.severity === "Critical"
                ? "critical"
                : event.severity === "Warning"
                  ? "warning"
                  : "success"
            }
          >
            {theme.label}
          </Badge>
        </div>
        <p>
          {detail?.summary ??
            `${event.processName ?? "프로세스 정보 미제공"} · 응답 코드 ${event.responseCode} · 이상 점수 ${event.anomalyScore.toFixed(2)}`}
        </p>
        <dl className="realtime-anomaly-event__fields">
          <div>
            <dt>감지 시각</dt>
            <dd>{displayOptional(event.detectedAt)}</dd>
          </div>
          <div>
            <dt>당시 구성 버전</dt>
            <dd>{displayOptional(detail?.configurationVersion)}</dd>
          </div>
          <div>
            <dt>판정 수행 범위</dt>
            <dd>{stateLabel}</dd>
          </div>
          <div>
            <dt>업무 분류</dt>
            <dd>
              {workflow === "Open"
                ? "보류"
                : workflow === "Resolved"
                  ? "완료"
                  : "— 확인 불가"}
            </dd>
          </div>
          <div>
            <dt>거래 ID</dt>
            <dd>{displayOptional(event.transactionId)}</dd>
          </div>
          <div>
            <dt>프로세스</dt>
            <dd>{displayOptional(event.processName)}</dd>
          </div>
          <div>
            <dt>채널</dt>
            <dd>{displayOptional(event.channelName)}</dd>
          </div>
          <div>
            <dt>원천 상태</dt>
            <dd>{displayOptional(event.status)}</dd>
          </div>
          <div>
            <dt>응답 코드</dt>
            <dd>{event.responseCode}</dd>
          </div>
          <div>
            <dt>처리 시간</dt>
            <dd>{event.processTimeMs} ms</dd>
          </div>
          <div>
            <dt>이상 점수</dt>
            <dd>{event.anomalyScore}</dd>
          </div>
          <div>
            <dt>위험 점수 · 등급</dt>
            <dd>
              {event.riskScore} · {event.riskLevel}
            </dd>
          </div>
        </dl>
        {query.isPending && <p role="status">판정 근거 조회 중</p>}
        {query.isError && (
          <div className="anomaly-evidence-read-error" role="status">
            <p>
              {query.error instanceof HttpError &&
              [404, 503].includes(query.error.status)
                ? "판정 근거 미연결"
                : "판정 근거 조회 실패"}
              {detail ? " · 이전 조회 근거 표시" : " · 추가 근거 확인 불가"}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void query.refetch()}
              disabled={query.isFetching}
            >
              근거 다시 조회
            </Button>
          </div>
        )}
        {user?.userType === "admin" && (
          <div className="analysis-toolbar__actions">
            <Button asChild variant="outline" size="sm">
              <Link
                to={
                  detail?.configurationId
                    ? `/detection/versions?tab=configuration&version=${encodeURIComponent(detail.configurationId)}`
                    : "/detection/versions?tab=configuration"
                }
              >
                {detail?.configurationId ? "당시 구성 보기" : "운영 구성 목록"}
              </Link>
            </Button>
            {detail?.evaluationId && (
              <Button asChild variant="outline" size="sm">
                <Link
                  to={`/detection/evaluation?tab=candidates&result=${encodeURIComponent(detail.evaluationId)}`}
                >
                  관련 평가 보기
                </Link>
              </Button>
            )}
          </div>
        )}
      </section>
      <div className="analysis-detail-columns">
        <EvidenceSection title="Rule 판정 근거" evidence={detail?.rule} />
        <EvidenceSection title="ML 판정 근거" evidence={detail?.ml} />
      </div>
      <section className="analysis-section-card anomaly-evidence-section">
        <h3>원인 가설</h3>
        <p>아래 내용은 판정 근거와 구분된 원인 가설입니다.</p>
        {detail?.hypotheses.length ? (
          <ul>
            {detail.hypotheses.map((hypothesis, index) => (
              <li key={index}>{hypothesis}</li>
            ))}
          </ul>
        ) : (
          <p>원인 가설: — 확인 불가</p>
        )}
      </section>
      <AnomalyFeedback
        key={event.eventId}
        eventId={event.eventId}
        capability={!query.isError && detail?.canSaveFeedback === true}
      />
    </AnimatedPanel>
  );
};
