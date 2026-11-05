import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { useSourcesQuery } from "@/features/operations/data/inputs";
import { numberText } from "@/features/operations/format";
import { errorCode, operatorLabels, type DraftSoftRule } from "./ruleDraft";
import { readThresholdStats, type PeriodMode, type SoftRuleField, type ThresholdStats } from "./ruleSetApi";

const PERIODS: { mode: PeriodMode; label: string }[] = [
  { mode: "active_model_training_start", label: "운영 머신러닝 모델 학습 기간 시작 ~ 현재" },
  { mode: "recent_1m", label: "최근 1개월" },
  { mode: "recent_3m", label: "최근 3개월" },
  { mode: "custom", label: "직접 지정" },
];
/**
 * 최근·직접 지정은 저장 시각 기준으로 보낸다. 원천 시각(source_log_occurred_at)을 채우는 코드가 아직 없어 계약 기본값이면 표본이 항상 0건이고,
 * 학습 스냅샷도 persisted_at 기준이다. 활성 모델 기간은 서버가 학습 기간 투영의 기준을 쓴다. Collector가 원천 시각을 채우면 esb_occurred_at으로 바꾼다.
 */
const periodRequest = (mode: PeriodMode, from: string, until: string) => {
  if (mode === "active_model_training_start") return { mode };
  const range = mode === "custom" ? { from: new Date(from).toISOString(), until: new Date(until).toISOString() } : {};
  return { mode, ...range, timeBasis: "persisted_at" as const };
};
const periodLabel = (mode: PeriodMode) => PERIODS.find((item) => item.mode === mode)?.label ?? mode;
const day = (iso: string) => {
  const date = new Date(iso);
  return `${date.getMonth() + 1}/${date.getDate()}`;
};
const statusLabel = { no_applicable: "해당 없음", rejected: "거부" } as const;
/** 표본 부족·분산 없음(llm-recommendation-job.md `no_applicable` 사유). */
const noApplicableReason = ({ n, min, max }: ThresholdStats["evidence"]) => (n < 20 ? "최소 기준 미달" : min === max ? "분산 없음" : null);

const Evidence = ({ stats }: { stats: ThresholdStats }) => {
  const { n, period } = stats.evidence;
  return (
    <span className="text-sm text-muted-foreground">
      표본 {numberText(n)}건 · 사용 기간 {day(period.from)}~{day(period.until)}({periodLabel(period.mode)}) · LLM 미사용
    </span>
  );
};

/** 규칙 행 수식 추천(와이어프레임 formulaBody): 기간 선택 → 계산 → 적용은 입력칸만. 서버 저장은 하지 않는다. */
export const FormulaPanel = ({
  rule, field, onApply, onClose,
}: {
  rule: DraftSoftRule; field: SoftRuleField | undefined; onApply: (threshold: string) => void; onClose: () => void;
}) => {
  const sources = useSourcesQuery();
  const [mode, setMode] = useState<PeriodMode>("active_model_training_start");
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [selectedSource, setSelectedSource] = useState("");
  const sourceId = sources.data?.find((source) => source.id === selectedSource)?.id ?? sources.data?.[0]?.id ?? "";
  const calculate = useMutation({
    mutationFn: () => readThresholdStats({
      field: rule.field, statistic: "p95", sourceId, period: periodRequest(mode, from, until),
    }),
  });
  const close = <Button size="sm" variant="outline" onClick={onClose}>닫기</Button>;
  const row = "flex flex-wrap items-center gap-2";

  if (calculate.isPending) return <div className={row}><Badge variant="info">계산 중</Badge>{close}</div>;
  if (calculate.isError) {
    return (
      <div className={row}>
        <span role="alert" className="text-sm text-destructive-text">수식 추천 실패 · {errorCode(calculate.error)} · 입력칸은 그대로입니다.</span>
        <Button size="sm" variant="outline" onClick={() => calculate.reset()}>다시 시도</Button>
        {close}
      </div>
    );
  }
  const stats = calculate.data;
  if (stats?.status === "proposed" && stats.threshold !== null) {
    const threshold = stats.threshold;
    return (
      <div className={row}>
        <strong className="text-sm font-medium" title={`원값 ${threshold}`}>수식 추천 · 데이터 p95 {stats.displayThreshold} {stats.unit}</strong>
        <span className="text-sm text-muted-foreground">조건 {stats.operator ? operatorLabels[stats.operator] : ""}</span>
        <Evidence stats={stats} />
        <Button size="sm" onClick={() => onApply(threshold)}>적용</Button>
        {close}
      </div>
    );
  }
  if (stats) {
    const reason = stats.status === "no_applicable" ? noApplicableReason(stats.evidence) : stats.rejectedReason;
    return (
      <div className={row}>
        <Badge variant="secondary">{statusLabel[stats.status as keyof typeof statusLabel]}</Badge>
        {reason && <span className="text-sm text-muted-foreground">{reason}</span>}
        <Evidence stats={stats} />
        <Button size="sm" variant="outline" onClick={() => calculate.reset()}>다시 시도</Button>
        {close}
      </div>
    );
  }
  const customReady = mode !== "custom" || (from !== "" && until !== "");
  const label = field?.displayName ?? rule.field;
  return (
    <div className={row}>
      <Label htmlFor={`formula-period-${rule.key}`}>수식 추천 기준 기간</Label>
      <div className="w-full sm:w-80">
        <NativeSelect
          id={`formula-period-${rule.key}`} aria-label={`${rule.name || label} 수식 추천 기준 기간`} value={mode}
          onChange={(event) => setMode(event.target.value as PeriodMode)}
        >
          {PERIODS.map((item) => <NativeSelectOption key={item.mode} value={item.mode}>{item.label}</NativeSelectOption>)}
        </NativeSelect>
      </div>
      {mode === "custom" && (
        <>
          <Input className="w-full sm:w-52" type="datetime-local" aria-label="시작" value={from} onChange={(event) => setFrom(event.target.value)} />
          <Input className="w-full sm:w-52" type="datetime-local" aria-label="종료" value={until} onChange={(event) => setUntil(event.target.value)} />
        </>
      )}
      {(sources.data?.length ?? 0) > 1 && (
        <>
          <Label htmlFor={`formula-source-${rule.key}`}>출처</Label>
          <div className="w-full sm:w-64">
            <NativeSelect id={`formula-source-${rule.key}`} value={sourceId} onChange={(event) => setSelectedSource(event.target.value)}>
              {sources.data?.map((source) => (
                <NativeSelectOption key={source.id} value={source.id}>{source.displayName} ({source.datasetKey})</NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
        </>
      )}
      {sources.isError && <span role="alert" className="text-sm text-destructive-text">{errorCode(sources.error)}</span>}
      <Button size="sm" disabled={!sourceId || !customReady} onClick={() => calculate.mutate()}>계산</Button>
      {close}
    </div>
  );
};
