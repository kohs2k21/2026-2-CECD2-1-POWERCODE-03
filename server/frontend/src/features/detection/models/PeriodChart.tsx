import { useEffect, useRef, useState, type PointerEvent } from "react";
import { numberText } from "@/features/operations/format";
import { cn } from "@/lib/utils";
import { barAt, rangeText, type Bar, type Range } from "./period";

const W = 720, H = 230, LEFT = 52, RIGHT = 8, TOP = 14, BOTTOM = 34;
const PLOT_WIDTH = W - LEFT - RIGHT, PLOT_HEIGHT = H - TOP - BOTTOM;
const ZOOM_IN = 0.7;
/** 오른쪽 끝 눈금 글자가 잘리지 않는 폭. */
const LABEL_ROOM = 40;

type Drag = { start: Bar; current: Bar; moved: boolean };

/**
 * 거래량 막대(와이어프레임 #93): 드래그·막대 두 번 클릭으로 구간 지정, 휠 확대·축소, Shift+휠·가로 스크롤 이동.
 * 표시 범위는 응답의 범위다. 재조회 동안 직전 막대가 어긋나지 않는다.
 */
export const PeriodChart = ({ view, bars, excluded, pending, label, onSelect, onPick, onZoom, onPan }: {
  view: Range;
  bars: Bar[];
  excluded: Range[];
  pending: Range | null;
  label: string;
  onSelect: (range: Range) => void;
  onPick: (bar: Bar) => void;
  onZoom: (factor: number, anchor: number) => void;
  onPan: (direction: number) => void;
}) => {
  const box = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [start, end] = view;
  const span = end - start;
  const xOf = (minute: number) => LEFT + ((minute - start) / span) * PLOT_WIDTH;
  const chartX = (clientX: number) => {
    const rect = svg.current?.getBoundingClientRect();
    return rect ? (clientX - rect.left) * (W / rect.width) : LEFT;
  };
  const minuteAt = (clientX: number) => start + ((chartX(clientX) - LEFT) / PLOT_WIDTH) * span;

  const handlers = useRef({ onZoom, onPan, minuteAt });
  handlers.current = { onZoom, onPan, minuteAt };
  // React의 onWheel은 수동 수신이라 페이지 스크롤을 막을 수 없다.
  useEffect(() => {
    const element = box.current;
    if (!element?.addEventListener) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const { deltaX, deltaY, shiftKey, clientX } = event;
      if (shiftKey || Math.abs(deltaX) > Math.abs(deltaY)) {
        const delta = deltaX || deltaY;
        if (delta) handlers.current.onPan(delta > 0 ? 1 : -1);
      } else if (deltaY) {
        handlers.current.onZoom(deltaY < 0 ? ZOOM_IN : 1 / ZOOM_IN, handlers.current.minuteAt(clientX));
      }
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, []);

  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const x = chartX(event.clientX);
    if (x < LEFT || x > W - RIGHT) return;
    const bar = barAt(bars, minuteAt(event.clientX));
    if (!bar) return;
    event.preventDefault();
    try { event.currentTarget.setPointerCapture?.(event.pointerId); } catch { /* 캡처는 없어도 동작한다. */ }
    setDrag({ start: bar, current: bar, moved: false });
  };
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const bar = barAt(bars, minuteAt(event.clientX));
    if (bar && bar.s !== drag.current.s) setDrag({ ...drag, current: bar, moved: drag.moved || bar.s !== drag.start.s });
  };
  const pointerUp = () => {
    if (!drag) return;
    setDrag(null);
    if (drag.moved) onSelect([Math.min(drag.start.s, drag.current.s), Math.max(drag.start.e, drag.current.e)]);
    else if (pending) onSelect([Math.min(pending[0], drag.start.s), Math.max(pending[1], drag.start.e)]);
    else onPick(drag.start);
  };

  const max = Math.max(1, ...bars.map((bar) => bar.n));
  const covered = (bar: Bar) => excluded.some(([from, until]) => from <= bar.s && until >= bar.e);
  const step = Math.max(1, Math.ceil(bars.length / 8));
  const dragFrom = drag?.moved ? Math.max(Math.min(drag.start.s, drag.current.s), start) : null;
  const dragTo = drag?.moved ? Math.min(Math.max(drag.start.e, drag.current.e), end) : null;
  return (
    <div
      ref={box}
      data-testid="period-chart"
      className="cursor-crosshair touch-none select-none"
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={pointerUp}
      onPointerCancel={() => setDrag(null)}
    >
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block h-auto w-full">
        <line x1={LEFT} y1={H - BOTTOM} x2={W - RIGHT} y2={H - BOTTOM} className="stroke-border" />
        <text x={0} y={TOP + 8} className="fill-muted-foreground text-xs">{numberText(max)}</text>
        <text x={0} y={H - BOTTOM} className="fill-muted-foreground text-xs">0</text>
        {excluded.map(([from, until]) => {
          const left = Math.max(from, start), right = Math.min(until, end);
          if (left >= right) return null;
          return <rect key={`${from}-${until}`} data-band={`${from}-${until}`} x={xOf(left)} y={TOP} width={Math.max(0.8, xOf(right) - xOf(left))} height={PLOT_HEIGHT} className="fill-destructive/15" />;
        })}
        {bars.map((bar) => {
          const x0 = xOf(bar.s), width = xOf(bar.e) - x0, gap = width > 3 ? 1 : 0, height = bar.n ? Math.max(1, (bar.n / max) * PLOT_HEIGHT) : 0;
          return (
            <rect key={bar.s} data-bar={bar.s} x={x0 + gap / 2} y={H - BOTTOM - height} width={Math.max(0.6, width - gap)} height={height} className={cn(covered(bar) ? "fill-destructive-soft" : "fill-link")}>
              <title>{`${rangeText(bar.s, bar.e)} · ${numberText(bar.n)}건`}</title>
            </rect>
          );
        })}
        {bars.map((bar, index) => index % step === 0 && xOf(bar.s) < W - LABEL_ROOM && (
          <text key={bar.s} x={xOf(bar.s) + 1} y={H - 12} className="fill-muted-foreground text-xs">{bar.label}</text>
        ))}
        {dragFrom !== null && dragTo !== null && <rect x={xOf(dragFrom)} y={TOP} width={xOf(dragTo) - xOf(dragFrom)} height={PLOT_HEIGHT} className="fill-link/20 stroke-link" />}
        {pending && pending[1] > start && pending[0] < end && (
          <line x1={xOf((Math.max(pending[0], start) + Math.min(pending[1], end)) / 2)} y1={TOP} x2={xOf((Math.max(pending[0], start) + Math.min(pending[1], end)) / 2)} y2={H - BOTTOM} strokeDasharray="4 3" className="stroke-destructive stroke-2" />
        )}
      </svg>
    </div>
  );
};
