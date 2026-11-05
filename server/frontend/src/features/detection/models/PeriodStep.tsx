import { useEffect, useState } from "react";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { numberText } from "@/features/operations/format";
import { useAccountQuery } from "@/lib/query";
import { cn } from "@/lib/utils";
import { readVolume, type TimeBasis, type VolumeReport } from "./modelsApi";
import {
  MAX_EXCLUSIONS, TIMEZONE, UNIT_LABELS, barsOf, clipRanges, durationText, emptyPrecise, fromUtc, mergeRanges, minuteDay, panView, preciseOf, preciseRange, queryRange, rangeText, toUtc, unitFor, zoomView,
  DAY_MINUTES, type PreciseInput, type Range,
} from "./period";
import { PeriodChart } from "./PeriodChart";
import { EXCLUSION_UNSUPPORTED, type PeriodDraft } from "./trainDraft";
import { TIME_BASIS_LABELS } from "./VersionList";

const SETTLE_MS = 200;
/** 학습 후보는 상태 S이고 train 분할은 그중 약 70%다(trainer-service.md 선정 정책). */
const TRAIN_SPLIT_RATIO = 0.7;

const statusS = (report: VolumeReport) => report.buckets.reduce((sum, bucket) => sum + bucket.statusCounts.S, 0);
const sameRange = (left: Range | null, right: Range | null) => Boolean(left && right && left[0] === right[0] && left[1] === right[1]);

/** 휠 한 칸마다 조회하지 않게 표시 범위가 멈춘 뒤 읽는다. */
const useSettled = (view: Range | null) => {
  const [settled, setSettled] = useState(view);
  useEffect(() => {
    if (sameRange(view, settled) || (!view && !settled)) return;
    if (!view || !settled) { setSettled(view); return; }
    const timer = setTimeout(() => setSettled(view), SETTLE_MS);
    return () => clearTimeout(timer);
  }, [view, settled]);
  return settled;
};

const useVolume = (sourceId: string, timeBasis: TimeBasis, range: Range | null, bucket: "month" | ReturnType<typeof unitFor>, name: string) =>
  useAccountQuery({
    name, params: [sourceId, timeBasis, range?.[0], range?.[1], bucket], adminOnly: true, enabled: Boolean(range), keepPrevious: true,
    read: (signal) => readVolume({ sourceId, from: toUtc(range![0]), until: toUtc(range![1]), bucket, timeBasis, timezone: TIMEZONE }, signal),
  });

/** 구간별 건수. 구간 안 표본 수는 서버 집계로만 안다. */
const useExcludedVolumes = (sourceId: string, timeBasis: TimeBasis, excluded: Range[]) =>
  useAccountQuery({
    name: "train-volume-excluded", params: [sourceId, timeBasis, excluded], adminOnly: true, enabled: excluded.length > 0,
    read: (signal) => Promise.all(excluded.map(([from, until]) =>
      readVolume({ sourceId, from: toUtc(from), until: toUtc(until), bucket: "month", timeBasis, timezone: TIMEZONE }, signal))),
  });

const Field = ({ id, label, children }: { id: string; label: string; children: React.ReactNode }) => (
  <div className="grid min-w-36 flex-1 gap-1"><Label htmlFor={id}>{label}</Label>{children}</div>
);

const ExclusionForm = ({ period, editing, onApply, onCancel, input, setInput }: {
  period: PeriodDraft;
  editing: Range | null;
  onApply: (range: Range) => void;
  onCancel: () => void;
  input: PreciseInput;
  setInput: (value: PreciseInput) => void;
}) => {
  const [error, setError] = useState<string | null>(null);
  const apply = () => {
    const result = preciseRange(input, period.query!);
    if ("error" in result) { setError(result.error); return; }
    setError(null);
    onApply(result.range);
  };
  const field = (key: keyof PreciseInput, id: string, label: string, type: "date" | "time") => (
    <Field id={id} label={label}>
      <Input id={id} type={type} step={type === "time" ? 60 : undefined} value={input[key]} onChange={(event) => setInput({ ...input, [key]: event.target.value })} />
    </Field>
  );
  return (
    <fieldset className="grid gap-2 rounded-lg border p-3">
      <legend className="px-1 text-sm font-medium">제외 구간 정확히 입력</legend>
      <div className="flex flex-wrap items-end gap-3">
        {field("fromDate", "excl-from", "시작 날짜", "date")}
        {field("fromTime", "excl-from-time", "시작 시각", "time")}
        {field("toDate", "excl-to", "끝 날짜", "date")}
        {field("toTime", "excl-to-time", "끝 시각", "time")}
        <div className="flex gap-2">
          <Button variant="outline" onClick={apply}>{editing ? "수정 적용" : "추가"}</Button>
          {editing && <Button variant="ghost" onClick={() => { setError(null); onCancel(); }}>수정 취소</Button>}
        </div>
      </div>
      <p className="text-sm text-muted-foreground">시각을 비우면 시작은 그날 00:00, 끝은 다음 날 00:00입니다. 끝 시각은 포함하지 않습니다.</p>
      {error && <p role="alert" className="text-sm text-destructive-text">{error}</p>}
    </fieldset>
  );
};

/** 1단계 기간: 조회 범위 → 거래량 그래프 → 제외 구간(그래프·정확히 입력이 같은 목록을 쓴다). */
export const PeriodStep = ({ period, update, sourceId, timeBasis, exclusionSupported }: {
  period: PeriodDraft;
  update: (next: PeriodDraft) => void;
  sourceId: string;
  timeBasis: TimeBasis;
  exclusionSupported: boolean;
}) => {
  const [queryError, setQueryError] = useState<string | null>(null);
  const [limitError, setLimitError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Range | null>(null);
  const [input, setInput] = useState<PreciseInput>(emptyPrecise);
  const { query, view, excluded, pending } = period;
  const settled = useSettled(view);
  const volume = useVolume(sourceId, timeBasis, settled, settled ? unitFor(settled[1] - settled[0]) : "month", "train-volume");
  const total = useVolume(sourceId, timeBasis, query, "month", "train-volume-total");
  const excludedVolumes = useExcludedVolumes(sourceId, timeBasis, excluded);

  const runQuery = () => {
    const result = queryRange(period.fromDate, period.toDate);
    if ("error" in result) {
      setQueryError(result.error);
      update({ ...period, query: null, view: null, pending: null });
      return;
    }
    setQueryError(null);
    setEditing(null);
    setInput(emptyPrecise);
    update({ ...period, query: result.range, view: result.range, pending: null, excluded: clipRanges(excluded, result.range) });
  };
  const addRange = (range: Range) => {
    const next = mergeRanges([...excluded.filter((item) => !sameRange(item, editing)), range]);
    if (next.length > MAX_EXCLUSIONS) { setLimitError(`제외 구간은 최대 ${MAX_EXCLUSIONS}개입니다.`); return; }
    setLimitError(null);
    setEditing(null);
    setInput(emptyPrecise);
    update({ ...period, excluded: next, pending: null });
  };
  const removeRange = (range: Range) => {
    if (sameRange(range, editing)) { setEditing(null); setInput(emptyPrecise); }
    update({ ...period, excluded: excluded.filter((item) => !sameRange(item, range)) });
  };

  const report = volume.data;
  const reportView: Range | null = report ? [fromUtc(report.from), fromUtc(report.until)] : null;
  const unit = report?.bucket;
  // 구간 건수를 모르면(조회 중·실패) 0으로 두지 않는다. 뺄 수를 모르면 학습 건수도 모른다.
  const counts = excluded.length === 0 ? [] : excludedVolumes.isError ? undefined : excludedVolumes.data;
  const excludedTotal = counts?.reduce((sum, item) => sum + item.totalCount, 0) ?? null;
  const excludedS = counts?.reduce((sum, item) => sum + statusS(item), 0) ?? null;
  const totalReport = total.isPlaceholderData ? undefined : total.data;
  const excludedMinutes = excluded.reduce((sum, [from, until]) => sum + until - from, 0);
  const totalMinutes = query ? query[1] - query[0] : 0;
  const trainCandidates = totalReport && excludedS !== null ? statusS(totalReport) - excludedS : null;
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field id="period-from" label="조회 시작">
          <Input id="period-from" type="date" value={period.fromDate} onChange={(event) => update({ ...period, fromDate: event.target.value })} />
        </Field>
        <Field id="period-to" label="조회 종료">
          <Input id="period-to" type="date" value={period.toDate} onChange={(event) => update({ ...period, toDate: event.target.value })} />
        </Field>
        <Button variant="outline" onClick={runQuery}>조회</Button>
      </div>
      {queryError && <Alert variant="destructive"><AlertDescription>{queryError}</AlertDescription></Alert>}
      {query && view && (
        <div className="grid gap-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-sm font-medium">
              거래량 · {TIME_BASIS_LABELS[timeBasis]}
              {unit && <Badge variant="info">단위 {UNIT_LABELS[unit]}</Badge>}
            </h3>
            <Button size="sm" variant="outline" onClick={() => update({ ...period, view: query })}>전체 보기</Button>
          </div>
          {volume.isPending && <LoadingState>거래량 조회 중…</LoadingState>}
          {volume.isError && <ErrorState title={`거래량 조회 실패 · ${errorCode(volume.error)}`} onRetry={() => void volume.refetch()} retryLabel="다시 조회" />}
          {report && reportView && (
            <PeriodChart
              view={reportView}
              bars={barsOf(report)}
              excluded={excluded}
              pending={pending}
              label={`거래량 ${minuteDay(query[0])}~${minuteDay(query[1] - DAY_MINUTES)} · 단위 ${UNIT_LABELS[report.bucket]} · 제외 ${excluded.length}구간`}
              onSelect={addRange}
              onPick={(bar) => update({ ...period, pending: [bar.s, bar.e] })}
              onZoom={(factor, anchor) => update({ ...period, view: zoomView(view, query, factor, anchor) })}
              onPan={(direction) => update({ ...period, view: panView(view, query, direction) })}
            />
          )}
          {totalReport?.totalCount === 0 && <EmptyState>조회 기간에 거래량이 없습니다.</EmptyState>}
          <p role="status" className="text-sm text-muted-foreground">
            {pending ? `끝을 클릭하세요 · 시작 ${rangeText(pending[0], pending[1])}` : "드래그하거나 시작·끝 막대를 차례로 클릭해 제외 구간을 지정합니다"}
            {` · 마우스 휠로 확대·축소(월 → 일 → 시간 → 분) · 가로 스크롤·Shift+휠로 이동 · 표시 ${rangeText(view[0], view[1])}`}
          </p>
          <ExclusionForm period={period} editing={editing} input={input} setInput={setInput} onApply={addRange} onCancel={() => { setEditing(null); setInput(emptyPrecise); }} />
          {limitError && <p role="alert" className="text-sm text-destructive-text">{limitError}</p>}
          <div className="grid gap-2">
            <h3 className="text-sm font-medium">제외 구간</h3>
            {excluded.length === 0 && <p className="text-sm text-muted-foreground">제외 구간 없음</p>}
            {excluded.length > 0 && (
              <ul className="grid gap-1">
                {excluded.map((range, index) => {
                  const text = rangeText(range[0], range[1]);
                  const count = counts?.[index]?.totalCount;
                  return (
                    <li key={`${range[0]}-${range[1]}`} data-excluded={`${range[0]}-${range[1]}`} className={cn("flex items-center justify-between gap-2 rounded-md px-2 py-1 text-sm", sameRange(range, editing) && "bg-warning-soft")}>
                      <Button variant="link" size="sm" aria-label={`${text} 입력칸에서 수정`} onClick={() => { setEditing(range); setInput(preciseOf(range)); }}>
                        {`${text} · ${durationText(range[1] - range[0])} · ${count === undefined ? "—" : `${numberText(count)}건`}`}
                      </Button>
                      <Button variant="ghost" size="sm" aria-label={`${text} 제외 구간 삭제`} onClick={() => removeRange(range)}>삭제</Button>
                    </li>
                  );
                })}
              </ul>
            )}
            {excludedVolumes.isError && <ErrorState title={`제외 구간 건수 조회 실패 · ${errorCode(excludedVolumes.error)}`} onRetry={() => void excludedVolumes.refetch()} retryLabel="다시 조회" />}
          </div>
          {total.isError && <ErrorState title={`거래량 조회 실패 · ${errorCode(total.error)}`} onRetry={() => void total.refetch()} retryLabel="다시 조회" />}
          <div className="grid gap-2">
            <h3 className="text-sm font-medium">요약</h3>
            <DescriptionList
              items={[
                { label: "조회 기간", value: `${minuteDay(query[0])} ~ ${minuteDay(query[1] - DAY_MINUTES)} · ${durationText(totalMinutes)}` },
                { label: "제외", value: `${durationText(excludedMinutes)} · ${excludedTotal === null ? "—" : `${numberText(excludedTotal)}건`} · ${excluded.length}구간` },
                { label: "전체 건수", value: totalReport ? `${numberText(totalReport.totalCount)}건` : "—" },
                {
                  label: "학습에 쓰는 건수",
                  // 구간을 뺀 학습이 불가능하면 뺀 건수를 보이지 않는다.
                  value: excluded.length > 0 && !exclusionSupported ? `확인 필요 · ${EXCLUSION_UNSUPPORTED}`
                    : trainCandidates === null ? "—" : `${numberText(trainCandidates)}건 · 상태 S · train 분할은 이 중 약 ${TRAIN_SPLIT_RATIO * 100}%`,
                },
                ...(totalReport?.timeUnknownCount ? [{ label: "시각 없음", value: `${numberText(totalReport.timeUnknownCount)}건` }] : []),
              ]}
            />
          </div>
        </div>
      )}
    </div>
  );
};
