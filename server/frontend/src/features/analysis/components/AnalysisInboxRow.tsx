import { Badge } from "../../../components/ui/badge";
import type { AnalysisInboxItem } from "../utils/realtimeAdapter";
import { categoryThemeMap, severityToneMap } from "../constants";

const displayOptional = (value: string | undefined): string =>
  value === undefined || value === "" ? "정보 없음" : value;

export const AnalysisInboxRow = ({
  detail,
  onOpen,
}: {
  detail: AnalysisInboxItem;
  onOpen: () => void;
}) => {
  const { log } = detail;
  const severityTheme = categoryThemeMap[log.severity];
  const SeverityIcon = severityTheme.icon;

  return (
    <button className="analysis-inbox-row" type="button" onClick={onOpen}>
      <span className={`analysis-inbox-row__icon ${severityTheme.className}`}>
        <SeverityIcon size={18} aria-hidden="true" />
      </span>
      <span className="analysis-inbox-row__body">
        <span className="analysis-inbox-row__meta">
          <Badge variant={severityToneMap[log.severity]}>
            {severityTheme.label}
          </Badge>
          <Badge variant="default">
            {displayOptional(log.status)}
          </Badge>
          <span>{displayOptional(log.detectedAt)}</span>
        </span>
        <strong className="analysis-inbox-row__title">
          {displayOptional(log.transactionId) === "정보 없음"
            ? log.logId
            : log.transactionId}
        </strong>
        <span className="analysis-inbox-row__process">
          프로세스: {displayOptional(log.processName)}
        </span>
        <span>
          응답 코드: {log.responseCode} · 채널: {displayOptional(log.channelName)}
        </span>
      </span>
      <span className="analysis-inbox-row__score">
        <span>Score</span>
        <strong>{log.anomalyScore.toFixed(2)}</strong>
      </span>
    </button>
  );
};
