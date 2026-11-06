import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { hourLabel, numberText } from "../format";

type Hour = { hourStart: string; count: number };
/** 축 글자: 색·크기 모두 토큰(tokens.css --chart-axis, --chart-axis-font-size). */
const axisTick = { fill: "var(--chart-axis)", fontSize: "var(--chart-axis-font-size)" };

/** 툴팁: 시간·건수(토큰 색, 인라인 style 없음). */
const HourTooltip = ({ active, payload }: { active?: boolean; payload?: { payload: Hour }[] }) => {
  const item = active ? payload?.[0]?.payload : undefined;
  if (!item) return null;
  return (
    <div className="rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-toast">
      {hourLabel(item.hourStart)} · {numberText(item.count)}건
    </div>
  );
};

/**
 * 최근 24시간 시간별 트랜잭션(T) 저장 건수 막대(저장 시각 기준). 마지막 막대 = 현재 시간(강조, 진행 중 시간).
 * 단일 계열이라 범례 없음, 값 요약은 aria-label로 제공한다.
 */
export const HourlyTransactionsChart = ({ items }: { items: Hour[] }) => {
  const max = items.reduce((value, item) => Math.max(value, item.count), 0);
  const current = items.at(-1);
  const summary = `최근 24시간 시간별 트랜잭션 양 · 최대 ${numberText(max)}건 · 현재 시간 ${numberText(current?.count ?? 0)}건`;
  return (
    <div className="h-chart w-full" role="img" aria-label={summary}>
      {/* 측정 전 첫 렌더 크기(-1) 경고 방지: 측정되면 실제 크기로 바뀐다. */}
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 1, height: 1 }}>
        <BarChart data={items} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="hourStart"
            tickFormatter={hourLabel}
            interval={3}
            tickLine={false}
            axisLine={{ stroke: "var(--chart-grid)" }}
            tick={axisTick}
          />
          <YAxis
            allowDecimals={false}
            width={40}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value: number) => numberText(value)}
            tick={axisTick}
          />
          <Tooltip cursor={{ fill: "var(--chart-grid)" }} content={<HourTooltip />} isAnimationActive={false} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {items.map((item, index) => (
              <Cell key={item.hourStart} fill={index === items.length - 1 ? "var(--chart-bar-current)" : "var(--chart-bar)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
