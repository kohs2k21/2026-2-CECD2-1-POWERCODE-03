import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AnalysisInboxItem } from "../utils/realtimeAdapter";
import { categoryThemeMap, severityBadgeVariant } from "../constants";

const displayOptional = (value: string | undefined): string =>
  value === undefined || value === "" ? "정보 없음" : value;

export const AnalysisInboxRow = ({ detail, onOpen }: { detail: AnalysisInboxItem; onOpen: () => void }) => {
  const { log } = detail;
  const theme = categoryThemeMap[log.severity];
  const SeverityIcon = theme.icon;
  return (
    <Button
      variant="outline"
      onClick={onOpen}
      className="grid h-auto w-full grid-cols-[auto_minmax(0,1fr)_auto] items-start justify-stretch gap-3 whitespace-normal px-4 py-3 text-left font-normal has-[>svg]:px-4"
    >
      <SeverityIcon className="mt-0.5 size-5 text-muted-foreground" aria-hidden="true" />
      <span className="grid min-w-0 gap-1 text-sm">
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant={severityBadgeVariant[log.severity]}>{theme.label}</Badge>
          <Badge variant="secondary">{displayOptional(log.status)}</Badge>
          <span>{displayOptional(log.detectedAt)}</span>
        </span>
        <strong className="truncate font-semibold">
          {displayOptional(log.transactionId) === "정보 없음" ? log.logId : log.transactionId}
        </strong>
        <span className="truncate text-muted-foreground">프로세스: {displayOptional(log.processName)}</span>
        <span className="text-muted-foreground">
          응답 코드: {log.responseCode} · 채널: {displayOptional(log.channelName)}
        </span>
      </span>
      <span className="grid justify-items-end text-xs text-muted-foreground">
        <span>Score</span>
        <strong className="text-base text-foreground tabular-nums">{log.anomalyScore.toFixed(2)}</strong>
      </span>
    </Button>
  );
};
