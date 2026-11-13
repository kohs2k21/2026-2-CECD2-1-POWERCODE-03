/**
 * 운영 현황 개요 위젯 배치(와이어프레임 #93 위젯 + #100 이전 홈 위젯의 격자 배치 방식).
 * 배치 = 격자 칸 위치·크기(x, y, w, h). 계정별로 서버에 저장한다(PUT /api/me/settings widgetLayout, contracts/notifications.md).
 * 서버는 형식(정수·이름·개수)만 검사하고, 위젯 이름·크기 제한은 여기서 읽을 때 다시 적용한다(parseStoredLayout).
 * 이 파일은 격자 라이브러리를 가져오지 않는 순수 함수만 둔다(시험·지연 로딩용).
 */
import { getStored } from "@/lib/storage";

export const widgetIds = ["flow", "recent", "trend", "active", "queue", "collect", "storage"] as const;
export type WidgetId = (typeof widgetIds)[number];
export const widgetLabels: Record<WidgetId, string> = {
  flow: "단계 흐름",
  recent: "최근 판정",
  trend: "판정 추이",
  active: "현재 운영 룰·모델",
  queue: "큐 적체",
  collect: "수집 요약",
  storage: "저장공간",
};

/** 격자: 6열. 기본 위젯은 2열(한 줄 3개) × 3행. */
export const GRID_COLUMNS = 6;
export type WidgetPlacement = { i: WidgetId; x: number; y: number; w: number; h: number };
type SizeRule = { w: number; h: number; minW: number; minH: number; maxW: number; maxH: number };
export const widgetSizes: Record<WidgetId, SizeRule> = {
  flow: { w: 2, h: 3, minW: 2, minH: 2, maxW: 6, maxH: 6 },
  recent: { w: 2, h: 3, minW: 2, minH: 2, maxW: 6, maxH: 6 },
  trend: { w: 2, h: 3, minW: 2, minH: 3, maxW: 6, maxH: 6 },
  active: { w: 2, h: 3, minW: 2, minH: 2, maxW: 6, maxH: 6 },
  queue: { w: 2, h: 3, minW: 2, minH: 3, maxW: 6, maxH: 6 },
  collect: { w: 2, h: 3, minW: 2, minH: 2, maxW: 6, maxH: 6 },
  storage: { w: 2, h: 2, minW: 2, minH: 2, maxW: 6, maxH: 6 },
};
/** 기본 배치: 저장공간은 추가 목록에만 둔다. */
export const defaultWidgetLayout: readonly WidgetPlacement[] = [
  { i: "flow", x: 0, y: 0, w: 2, h: 3 },
  { i: "recent", x: 2, y: 0, w: 2, h: 3 },
  { i: "trend", x: 4, y: 0, w: 2, h: 3 },
  { i: "active", x: 0, y: 3, w: 2, h: 3 },
  { i: "queue", x: 2, y: 3, w: 2, h: 3 },
  { i: "collect", x: 4, y: 3, w: 2, h: 3 },
];

const isWidgetId = (value: unknown): value is WidgetId => typeof value === "string" && (widgetIds as readonly string[]).includes(value);
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
export const collides = (a: WidgetPlacement, b: WidgetPlacement) =>
  a.i !== b.i && a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
const byPosition = (a: WidgetPlacement, b: WidgetPlacement) => a.y - b.y || a.x - b.x;

/** 읽는 순서(위→아래, 왼쪽→오른쪽). 좁은 화면 한 줄 배치·키보드 앞/뒤 이동 기준. */
export const readingOrder = (layout: readonly WidgetPlacement[]) => [...layout].sort(byPosition).map((item) => item.i);

/** 크기 제한·열 범위 적용(겹침 해소는 compactLayout). */
const fit = (item: WidgetPlacement): WidgetPlacement => {
  const rule = widgetSizes[item.i];
  const w = clamp(Math.round(item.w), rule.minW, Math.min(rule.maxW, GRID_COLUMNS));
  const h = clamp(Math.round(item.h), rule.minH, rule.maxH);
  return { i: item.i, w, h, x: clamp(Math.round(item.x), 0, GRID_COLUMNS - w), y: Math.max(0, Math.round(item.y)) };
};

/**
 * 겹침 해소 + 위로 붙이기. priority 위젯은 자리를 지키고, 나머지는 위치 순서대로 겹치지 않을 때까지 아래로 민 뒤
 * 빈 칸이 없게 위로 붙인다(격자 라이브러리의 세로 압축과 같은 결과).
 */
export const compactLayout = (layout: readonly WidgetPlacement[], priority?: WidgetId): WidgetPlacement[] => {
  const items = layout.map(fit);
  const first = items.find((item) => item.i === priority);
  const placed: WidgetPlacement[] = first ? [first] : [];
  for (const item of items.filter((entry) => entry !== first).sort(byPosition)) {
    const next = { ...item };
    while (placed.some((other) => collides(next, other))) next.y += 1;
    placed.push(next);
  }
  const compacted: WidgetPlacement[] = [];
  for (const item of placed.sort(byPosition)) {
    const next = { ...item };
    while (next.y > 0 && !compacted.some((other) => collides({ ...next, y: next.y - 1 }, other))) next.y -= 1;
    compacted.push(next);
  }
  return compacted.sort(byPosition);
};

/** 새 위젯 자리: 위에서부터 처음 들어가는 빈 칸. */
export const nextPosition = (layout: readonly WidgetPlacement[], id: WidgetId) => {
  const { w, h } = widgetSizes[id];
  for (let y = 0; ; y += 1) {
    for (let x = 0; x <= GRID_COLUMNS - w; x += 1) {
      if (!layout.some((other) => collides({ i: id, x, y, w, h }, other))) return { x, y };
    }
  }
};

/** 저장값·격자 결과 검증: 알 수 없는 위젯·잘못된 수치 제거, 중복 제거, 크기 제한. 배열이 아니면 null. */
export const sanitizeLayout = (value: unknown): WidgetPlacement[] | null => {
  if (!Array.isArray(value)) return null;
  const seen = new Set<WidgetId>();
  const result: WidgetPlacement[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const item = entry as Record<string, unknown>;
    if (!isWidgetId(item.i) || seen.has(item.i)) continue;
    if (!["x", "y", "w", "h"].every((key) => typeof item[key] === "number" && Number.isFinite(item[key]))) continue;
    seen.add(item.i);
    result.push(fit({ i: item.i, x: item.x as number, y: item.y as number, w: item.w as number, h: item.h as number }));
  }
  return result;
};

export const availableWidgets = (layout: readonly WidgetPlacement[]) => widgetIds.filter((id) => !layout.some((item) => item.i === id));

export type LayoutAction =
  | { type: "move"; id: WidgetId; direction: -1 | 1 }
  | { type: "remove"; id: WidgetId }
  | { type: "add"; id: WidgetId; at?: { x: number; y: number } }
  | { type: "change"; layout: readonly WidgetPlacement[] }
  | { type: "reset" };

/** 편집 중 배치 변경. 바뀌지 않으면 같은 배열을 돌려준다. */
export const layoutReducer = (layout: readonly WidgetPlacement[], action: LayoutAction): readonly WidgetPlacement[] => {
  switch (action.type) {
    case "move": {
      // 키보드 대안: 읽는 순서의 이웃과 자리(x, y)를 바꾼다.
      const order = readingOrder(layout);
      const from = order.indexOf(action.id);
      const neighbor = order[from + action.direction];
      if (from < 0 || !neighbor) return layout;
      const a = layout.find((item) => item.i === action.id)!;
      const b = layout.find((item) => item.i === neighbor)!;
      const swapped = layout.map((item) =>
        item.i === a.i ? { ...item, x: b.x, y: b.y } : item.i === b.i ? { ...item, x: a.x, y: a.y } : item,
      );
      return compactLayout(swapped, action.id);
    }
    case "remove":
      return layout.some((item) => item.i === action.id) ? compactLayout(layout.filter((item) => item.i !== action.id)) : layout;
    case "add": {
      if (layout.some((item) => item.i === action.id)) return layout;
      const { w, h } = widgetSizes[action.id];
      const at = action.at ?? nextPosition(layout, action.id);
      return compactLayout([...layout, { i: action.id, x: at.x, y: at.y, w, h }], action.id);
    }
    case "change": {
      // 격자 라이브러리가 이미 압축한 결과: 검증·크기 제한만(다시 압축하면 라이브러리와 결과가 달라질 수 있다).
      // 배열 순서는 읽는 순서(위→아래, 왼쪽→오른쪽)로 맞춘다: 화면 순서 = DOM·Tab 순서 = 키보드 앞/뒤 기준(WCAG 2.4.3).
      const next = (sanitizeLayout(action.layout) ?? []).sort(byPosition);
      return sameLayout(next, layout) && next.every((item, index) => item.i === layout[index]?.i) ? layout : next;
    }
    case "reset":
      return defaultWidgetLayout.map((item) => ({ ...item }));
  }
};

const key = (layout: readonly WidgetPlacement[]) =>
  [...layout].sort((a, b) => a.i.localeCompare(b.i)).map((item) => `${item.i}:${item.x},${item.y},${item.w},${item.h}`).join("|");
export const sameLayout = (a: readonly WidgetPlacement[], b: readonly WidgetPlacement[]) => key(a) === key(b);

/** 저장 형식 v2: { version: 2, items: 위치 배열 }. v1(순서 배열)은 그 순서대로 기본 크기로 다시 배치한다. */
export const LAYOUT_VERSION = 2;
export const parseStoredLayout = (value: unknown): WidgetPlacement[] | null => {
  if (Array.isArray(value)) {
    const ids = [...new Set(value.filter(isWidgetId))];
    if (!ids.length && value.length) return null;
    return ids.reduce<WidgetPlacement[]>((layout, id) => [...layoutReducer(layout, { type: "add", id })], []);
  }
  if (!value || typeof value !== "object" || (value as Record<string, unknown>).version !== LAYOUT_VERSION) return null;
  const items = sanitizeLayout((value as Record<string, unknown>).items);
  if (!items) return null;
  // 겹침이 있을 때만 다시 배치한다(격자에서 저장한 배치는 그대로 복원).
  return items.some((a) => items.some((b) => collides(a, b))) ? compactLayout(items) : items;
};

/**
 * 서버 저장 전 브라우저 저장소 배치(옛 키, 한 번만 옮김): 서버에 배치가 없고 이 값이 유효하면 화면이 이 배치를 쓰고 서버에 저장한 뒤 지운다.
 * 서버에 배치가 있으면 옛 값은 쓰지 않고 지운다.
 */
export const legacyLayoutKey = (accountId: string) => `esb_operations_widgets_${accountId}`;
export const readLegacyLayout = (accountId: string) => (accountId ? parseStoredLayout(getStored<unknown>(legacyLayoutKey(accountId), null)) : null);
export const removeLegacyLayout = (accountId: string) => {
  try {
    window.localStorage.removeItem(legacyLayoutKey(accountId));
  } catch {
    // 저장소를 쓸 수 없으면 다음에 다시 시도한다(서버 배치가 있으면 옛 값은 쓰지 않는다).
  }
};

/** 서버 저장 문서(v2). */
export const serializeLayout = (layout: readonly WidgetPlacement[]) => ({
  version: LAYOUT_VERSION,
  items: layout.map(({ i, x, y, w, h }) => ({ i, x, y, w, h })),
});

/** 저장 결과 문구(와이어프레임 widgets-save). */
export const savedLayoutMessage = (role: "admin" | "user", count: number) =>
  `배치 저장됨 · ${role === "admin" ? "관리자" : "일반 사용자"} 계정 · 위젯 ${count}개`;
export const cancelledLayoutMessage = "편집 취소 · 이전 배치 유지";
