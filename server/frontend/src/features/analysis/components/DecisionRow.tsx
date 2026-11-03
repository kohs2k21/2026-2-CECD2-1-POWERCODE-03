import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { DecisionItem } from "../data/analysisApi";
import { readEvidence } from "../data/evidence";
import { DASH, completenessText, resultText, resultVariant, runLabels, stageLabels, stampText } from "../labels";

/** 경로 상태(Rule·ML) 한 줄: 저장된 rule_status·ml_status 그대로. 최종 판정 전이면 생략. */
const pathsText = (item: DecisionItem) => {
  if (!item.ruleStatus && !item.mlStatus) return null;
  const label = (status: string | null) => (status ? (runLabels[status] ?? status) : DASH);
  return `Rule ${label(item.ruleStatus)} · ML ${label(item.mlStatus)}`;
};
const modelsText = (item: DecisionItem) => {
  const keys = readEvidence(item.evidence).models.flatMap((model) => (model.modelKey ? [model.modelKey] : []));
  return keys.length ? `모델 ${keys.join(", ")}` : null;
};
const inputText = (item: DecisionItem) => {
  const evidence = readEvidence(item.evidence);
  if (evidence.demo) return "과거 데이터 재생 데모";
  return evidence.input?.mode === "csv" ? "CSV 재생" : evidence.input?.mode === "api" ? "API 수집" : null;
};

/**
 * 판정 목록 한 줄(와이어프레임 inbox-row): 왼쪽 = 거래·인터페이스·경로 상태, 오른쪽 = 판정 결과·단계·검사 완료와 시각·판정 ID와 프로세스.
 * 행 전체가 상세로 가는 링크(키보드 순서 = 화면 순서).
 */
export const DecisionRow = ({ item, href }: { item: DecisionItem; href: string }) => {
  const sub = [pathsText(item), modelsText(item), inputText(item)].filter(Boolean).join(" · ");
  return (
    <Link
      to={href}
      data-decision={item.id}
      className={cn(
        "grid gap-3 rounded-lg border bg-card px-4 py-3 text-sm transition-colors outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 sm:grid-cols-[minmax(0,1fr)_auto]",
      )}
    >
      <span className="grid min-w-0 gap-1">
        <strong className="truncate font-semibold">{item.transactionId ?? item.id}</strong>
        <span className="truncate text-muted-foreground">
          인터페이스 {item.interfaceId ?? DASH} · 채널 {item.channelId ?? DASH}
        </span>
        {sub && <span className="truncate text-muted-foreground">{sub}</span>}
      </span>
      <span className="grid content-start gap-1 text-xs text-muted-foreground sm:justify-items-end">
        <span className="flex flex-wrap items-center gap-1.5">
          <Badge variant={resultVariant(item)}>{resultText(item)}</Badge>
          {/* 와이어프레임: 단계 배지는 최종·판정 실패가 아닐 때만(판정 실패는 결과 배지와 같은 뜻), 결과 문구와 같으면 생략 */}
          {item.stage !== "final" && item.stage !== "failed" && stageLabels[item.stage] !== resultText(item) && (
            <Badge variant="info">{stageLabels[item.stage]}</Badge>
          )}
        </span>
        <span>
          검사 {completenessText(item)} · {stampText(item.occurredAt)}
        </span>
        <span className="font-mono">
          {item.id.slice(0, 8)} · {item.processId ?? DASH}
        </span>
      </span>
    </Link>
  );
};
