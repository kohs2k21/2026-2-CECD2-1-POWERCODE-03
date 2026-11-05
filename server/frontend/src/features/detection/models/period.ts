import type { VolumeReport, VolumeUnit } from "./modelsApi";

/**
 * 새 학습 기간 단계의 시각 계산(와이어프레임 #93 기간 절). 값은 한국 시각 벽시계 분(分)이다: 고객사가 한국이고 거래량 집계도 `Asia/Seoul` 경계로 받는다.
 * 제외 구간은 [시작, 끝) 분 범위라 어느 단위에서 지정해도 같은 기준으로 병합·집계된다.
 */
export const TIMEZONE = "Asia/Seoul";
const OFFSET_MINUTES = 540;
const MINUTE_MS = 60_000;
export const DAY_MINUTES = 1440;
const MIN_SPAN = 30;
export const MAX_QUERY_DAYS = 366;
export const MAX_EXCLUSIONS = 32;

export type Range = [number, number];
export type Bar = { s: number; e: number; n: number; label: string };

const minuteDate = (minute: number) => new Date(minute * MINUTE_MS);
const pad = (value: number) => String(value).padStart(2, "0");
const dayOf = (minute: number) => Math.floor(minute / DAY_MINUTES) * DAY_MINUTES;

export const dateMinute = (date: string) => Date.parse(`${date}T00:00:00Z`) / MINUTE_MS;
export const minuteDay = (minute: number) => minuteDate(minute).toISOString().slice(0, 10);
export const toUtc = (minute: number) => new Date((minute - OFFSET_MINUTES) * MINUTE_MS).toISOString();
export const fromUtc = (iso: string) => Math.round(Date.parse(iso) / MINUTE_MS) + OFFSET_MINUTES;

const monthDay = (minute: number) => {
  const date = minuteDate(minute);
  return `${date.getUTCMonth() + 1}/${date.getUTCDate()}`;
};
export const clock = (minute: number) => {
  const date = minuteDate(minute);
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
};
export const rangeText = (start: number, end: number) => {
  if (start % DAY_MINUTES === 0 && end % DAY_MINUTES === 0) {
    const last = end - DAY_MINUTES;
    return monthDay(start) + (last === start ? "" : ` ~ ${monthDay(last)}`);
  }
  return `${monthDay(start)} ${clock(start)} ~ ${dayOf(end - 1) === dayOf(start) ? clock(end) : `${monthDay(end)} ${clock(end)}`}`;
};
export const durationText = (minutes: number) => {
  const days = Math.floor(minutes / DAY_MINUTES), hours = Math.floor((minutes % DAY_MINUTES) / 60), rest = minutes % 60;
  return [days ? `${days}일` : "", hours ? `${hours}시간` : "", rest ? `${rest}분` : ""].filter(Boolean).join(" ") || "0분";
};
/** 저장된 UTC 시각의 한국 시각 표시(YYYY-MM-DD HH:mm). */
export const fullText = (iso: string) => {
  const minute = fromUtc(iso);
  return `${minuteDay(minute)} ${clock(minute)}`;
};

export const UNIT_LABELS: Record<VolumeUnit, string> = { month: "월", day: "일", hour: "시간", minute: "분" };
/** 사용자 11/02: 100일 초과 월, 3일 초과 일, 4시간 초과 시간, 그 이하 분. */
export const unitFor = (span: number): VolumeUnit =>
  span > 100 * DAY_MINUTES ? "month" : span > 3 * DAY_MINUTES ? "day" : span > 240 ? "hour" : "minute";
export const nextUnit = (minute: number, unit: VolumeUnit) => {
  if (unit === "minute") return minute + 1;
  if (unit === "hour") return minute + 60;
  if (unit === "day") return minute + DAY_MINUTES;
  const date = minuteDate(minute);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / MINUTE_MS;
};
const unitLabel = (minute: number, unit: VolumeUnit) => {
  const date = minuteDate(minute);
  if (unit === "month") return `${date.getUTCFullYear()}.${date.getUTCMonth() + 1}`;
  if (unit === "day") return monthDay(minute);
  if (unit === "hour") return date.getUTCHours() === 0 ? monthDay(minute) : `${pad(date.getUTCHours())}시`;
  return clock(minute);
};

/** 조회 응답 → 막대. 첫·끝 버킷은 조회 범위로 잘린다(서버 건수도 범위 안 건수다). */
export const barsOf = (report: VolumeReport): Bar[] => {
  const start = fromUtc(report.from), end = fromUtc(report.until);
  return report.buckets.map((bucket) => {
    const minute = fromUtc(bucket.bucketStart);
    return { s: Math.max(minute, start), e: Math.min(nextUnit(minute, report.bucket), end), n: bucket.processCount, label: unitLabel(minute, report.bucket) };
  });
};

export const queryRange = (from: string, to: string): { range: Range } | { error: string } => {
  if (!from || !to) return { error: "조회 시작·종료 날짜를 입력해 주세요." };
  if (from > to) return { error: "시작 날짜가 종료 날짜보다 늦습니다." };
  const range: Range = [dateMinute(from), dateMinute(to) + DAY_MINUTES];
  if ((range[1] - range[0]) / DAY_MINUTES > MAX_QUERY_DAYS) return { error: `조회 범위는 최대 ${MAX_QUERY_DAYS}일입니다.` };
  return { range };
};

/** 겹치거나 맞닿은 구간은 하나로 합친다(서버는 겹침을 거절하므로 보여 준 구간과 보낸 구간이 같다). */
export const mergeRanges = (list: Range[]) => {
  const merged: Range[] = [];
  [...list].sort((left, right) => left[0] - right[0]).forEach(([start, end]) => {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  });
  return merged;
};
/** 조회 범위가 바뀌면 그 안으로 잘라 둔다. */
export const clipRanges = (list: Range[], [start, end]: Range) =>
  mergeRanges(list.map(([from, until]): Range => [Math.max(from, start), Math.min(until, end)]).filter(([from, until]) => from < until));

export type PreciseInput = { fromDate: string; fromTime: string; toDate: string; toTime: string };
export const emptyPrecise: PreciseInput = { fromDate: "", fromTime: "", toDate: "", toTime: "" };
const timeMinutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3, 5));
/** 날짜+시각(분). 시각을 비우면 시작 00:00, 끝은 다음 날 00:00. 끝은 포함하지 않는다. */
export const preciseRange = (input: PreciseInput, [start, end]: Range): { range: Range } | { error: string } => {
  if (!input.fromDate || !input.toDate) return { error: "시작·끝 날짜를 입력해 주세요." };
  const from = dateMinute(input.fromDate) + (input.fromTime ? timeMinutes(input.fromTime) : 0);
  const until = input.toTime ? dateMinute(input.toDate) + timeMinutes(input.toTime) : dateMinute(input.toDate) + DAY_MINUTES;
  if (from >= until) return { error: "끝이 시작보다 늦어야 합니다." };
  if (from < start || until > end) return { error: `조회 기간(${minuteDay(start)} ~ ${minuteDay(end - DAY_MINUTES)}) 안에서 입력해 주세요.` };
  return { range: [from, until] };
};
export const preciseOf = ([from, until]: Range): PreciseInput => ({
  fromDate: minuteDay(from),
  fromTime: from % DAY_MINUTES === 0 ? "" : clock(from),
  toDate: until % DAY_MINUTES === 0 ? minuteDay(until - DAY_MINUTES) : minuteDay(until),
  toTime: until % DAY_MINUTES === 0 ? "" : clock(until),
});

export const zoomView = ([start, end]: Range, [low, high]: Range, factor: number, anchor: number): Range => {
  const span = end - start, total = high - low;
  const next = Math.max(Math.min(MIN_SPAN, total), Math.min(total, Math.round(span * factor)));
  const moved = Math.round(anchor - ((anchor - start) / span) * next);
  const from = Math.max(low, Math.min(high - next, moved));
  return [from, from + next];
};
export const panView = ([start, end]: Range, [low, high]: Range, direction: number): Range => {
  const span = end - start;
  const from = Math.max(low, Math.min(high - span, start + direction * Math.max(1, Math.round(span * 0.15))));
  return [from, from + span];
};
export const barAt = (bars: Bar[], minute: number) =>
  bars.find((bar) => minute >= bar.s && minute < bar.e) ?? (bars.length ? (minute < bars[0].s ? bars[0] : bars[bars.length - 1]) : null);
