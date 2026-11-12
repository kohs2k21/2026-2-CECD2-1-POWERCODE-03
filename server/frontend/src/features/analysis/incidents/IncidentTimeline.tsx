import { Link } from "react-router-dom";
import { stampText } from "../labels";
import type { IncidentItem } from "./incidentApi";
import {
  combinedTimeBasis,
  criteriaKindLabel,
  criteriaText,
  criteriaTone,
  durationText,
  timeBasisKind,
  timeBasisMarks,
  timeBasisTitle,
  timelineBasisNotes,
} from "./incidentLabels";

/** 막대 최소 폭(%): 구성원 1건·1분 미만 사건도 보이게. */
const MIN_WIDTH = 0.6;
const TICKS = 5;

/** 시간축 범위: 보이는 사건(이 쪽)의 가장 이른 시작 ~ 가장 늦은 끝, 양쪽에 범위의 5%(최소 5분) 여백. 기간 전체가 아니라 사건에 맞춘다(짧은 사건도 막대로 보이게). */
export const timelineRange = (items: IncidentItem[]) => {
  const low = Math.min(...items.map((i) => Date.parse(i.startAt))), high = Math.max(...items.map((i) => Date.parse(i.endAt)));
  const pad = Math.max((high - low) * 0.05, 5 * 60_000);
  return { low: low - pad, high: high + pad };
};
export const barGeometry = (item: IncidentItem, range: { low: number; high: number }) => {
  const span = range.high - range.low;
  const left = Math.max(0, ((Date.parse(item.startAt) - range.low) / span) * 100);
  const width = Math.max(MIN_WIDTH, ((Date.parse(item.endAt) - Math.max(range.low, Date.parse(item.startAt))) / span) * 100);
  return { left: Math.min(left, 100 - MIN_WIDTH), width: Math.min(width, 100 - Math.min(left, 100 - MIN_WIDTH)) };
};

/**
 * 사건 시간축(목록 위): 흐름마다 막대 1개(길이 = 기간, 색 = 기준 종류). 표와 같은 순서·같은 항목. 막대를 누르면 상세.
 * 거래 사이 연결선은 그리지 않는다(상관관계만, contracts/incidents-ui.md).
 */
export const IncidentTimeline = ({
  items,
  detailHref,
}: {
  items: IncidentItem[];
  detailHref: (id: string) => string;
}) => {
  if (!items.length) return null;
  const range = timelineRange(items);
  const ticks = Array.from({ length: TICKS }, (_, index) => range.low + ((range.high - range.low) * index) / (TICKS - 1));
  const kinds = [...new Set(items.map((i) => i.criteria.kind))];
  const basisNote = timelineBasisNotes[combinedTimeBasis(items.map((i) => i.occurredBasis))];
  return (
    <figure className="grid gap-2" aria-label="사건 시간축">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span data-timeline-basis title={basisNote ? timeBasisTitle : undefined}>
          {basisNote}
        </span>
        <div className="flex flex-wrap items-center gap-3" data-incident-legend>
          {kinds.map((kind) => (
            <span key={kind ?? "none"} className="flex items-center gap-1.5">
              <span aria-hidden="true" className={`inline-block h-2 w-3 rounded-sm ${criteriaTone(kind)}`} />
              {criteriaKindLabel(kind)}
            </span>
          ))}
        </div>
      </div>
      <div className="relative grid gap-1 rounded-md border bg-card px-2 py-2">
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-2 inset-y-0">
          {ticks.map((tick, index) => (
            <span key={tick} className="absolute inset-y-0 border-l border-(--chart-grid)" style={{ left: `${(index / (TICKS - 1)) * 100}%` }} />
          ))}
        </div>
        {items.map((item) => {
          const { left, width } = barGeometry(item, range);
          const mark = timeBasisMarks[timeBasisKind(item.occurredBasis)];
          const label = `${criteriaText(item.criteria)} · ${stampText(item.startAt)} ~ ${stampText(item.endAt)}${mark ? `(${mark})` : ""} · ${durationText(item.durationMs)} · 거래 ${item.counts.transactions}건`;
          return (
            <div key={item.id} className="relative h-3">
              <Link
                to={detailHref(item.id)}
                aria-label={label}
                title={label}
                data-incident-bar={item.criteria.kind ?? ""}
                className={`absolute inset-y-0 rounded-sm opacity-85 hover:opacity-100 focus-visible:outline-2 focus-visible:outline-ring ${criteriaTone(item.criteria.kind)}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </div>
          );
        })}
      </div>
      <div className="relative mx-[9px] h-4 text-[length:var(--chart-axis-font-size)] text-(--chart-axis)" aria-hidden="true">
        {ticks.map((tick, index) => (
          <span
            key={tick}
            className="absolute whitespace-nowrap"
            style={{ left: `${(index / (TICKS - 1)) * 100}%`, transform: index === 0 ? "none" : index === TICKS - 1 ? "translateX(-100%)" : "translateX(-50%)" }}
          >
            {stampText(new Date(tick).toISOString()).slice(0, 11)}
          </span>
        ))}
      </div>
    </figure>
  );
};
