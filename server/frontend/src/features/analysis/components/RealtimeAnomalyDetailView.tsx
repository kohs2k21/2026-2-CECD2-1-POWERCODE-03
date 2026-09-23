import { IconArrowLeft, IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react";
import { AnimatedPanel } from "../../../components/layout/AnimatedPanel";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import type { RealtimeAnomalyEvent } from "../../../types/realtime";
import type { CategoryTheme } from "../types";

const displayOptional = (value: string | undefined): string =>
  value === undefined || value === "" ? "정보 없음" : value;

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
}) => (
  <AnimatedPanel
    className={`analysis-detail-view ${isWide ? "analysis-detail-view--wide" : ""}`}
  >
    <div className="analysis-detail-breadcrumb">
      <div className="analysis-detail-breadcrumb__left">
        <Button variant="ghost" size="icon" onClick={onBack}>
          <IconArrowLeft size={16} aria-hidden="true" />
          <span className="sr-only">목록으로 돌아가기</span>
        </Button>
        <span>실시간 이벤트 상세</span>
        <span>/</span>
        <strong>{event.eventId}</strong>
      </div>
      <div className="analysis-status-actions analysis-status-actions--inline">
        <Button
          variant="outline"
          size="icon-sm"
          onClick={() => onToggleWide(!isWide)}
          aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
        >
          {isWide ? <IconArrowsMinimize size={16} /> : <IconArrowsMaximize size={16} />}
        </Button>
        <span className="realtime-anomaly-panel__message">
          이벤트 상태 변경은 현재 지원되지 않습니다.
        </span>
      </div>
    </div>

    <section className="analysis-section-card" aria-label="수신 이벤트 필드">
      <div className="analysis-section-card__toolbar analysis-section-card__toolbar--compact">
        <h3>수신 이벤트</h3>
        <Badge variant={event.severity === "Critical" ? "critical" : event.severity === "Warning" ? "warning" : "success"}>
          {theme.label}
        </Badge>
      </div>
      <dl className="realtime-anomaly-event__fields">
        <div><dt>eventId</dt><dd>{event.eventId}</dd></div>
        <div><dt>schemaVersion</dt><dd>{event.schemaVersion}</dd></div>
        <div><dt>provenance</dt><dd>{event.provenance}</dd></div>
        <div><dt>detectedAt</dt><dd>{displayOptional(event.detectedAt)}</dd></div>
        <div><dt>severity</dt><dd>{event.severity}</dd></div>
        <div><dt>원천 상태</dt><dd>{displayOptional(event.status)}</dd></div>
        <div><dt>transactionId</dt><dd>{displayOptional(event.transactionId)}</dd></div>
        <div><dt>processName</dt><dd>{displayOptional(event.processName)}</dd></div>
        <div><dt>channelName</dt><dd>{displayOptional(event.channelName)}</dd></div>
        <div><dt>responseCode</dt><dd>{event.responseCode}</dd></div>
        <div><dt>anomalyScore</dt><dd>{event.anomalyScore}</dd></div>
        <div><dt>processTimeMs</dt><dd>{event.processTimeMs}</dd></div>
        <div><dt>riskScore</dt><dd>{event.riskScore}</dd></div>
        <div><dt>riskLevel</dt><dd>{event.riskLevel}</dd></div>
      </dl>
    </section>

    <div className="analysis-detail-columns">
      <section className="analysis-section-card analysis-detail-columns__main">
        <div className="analysis-section-card__toolbar analysis-section-card__toolbar--compact">
          <h3>거래·프로세스 상세</h3>
        </div>
        <p className="realtime-anomaly-panel__empty">
          거래 스냅샷: 정보 없음 · 프로세스 계층: 정보 없음
        </p>
      </section>
      <section className="analysis-section-card analysis-detail-columns__side">
        <div className="analysis-section-card__toolbar analysis-section-card__toolbar--compact">
          <h3>메시지·본문·LLM 분석</h3>
        </div>
        <p className="realtime-anomaly-panel__empty">
          메시지: 정보 없음 · 본문: 정보 없음 · LLM 분석: 정보 없음
        </p>
      </section>
    </div>
  </AnimatedPanel>
);
