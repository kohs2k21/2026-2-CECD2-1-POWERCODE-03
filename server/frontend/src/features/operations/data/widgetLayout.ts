/**
 * 운영 현황 개요 위젯 배치(와이어프레임 #93 운영 현황 위젯).
 * 배치는 계정별로 저장한다. 지금은 브라우저 저장소(layoutStore 기본 구현)이고,
 * 서버 계정별 저장(하위 Issue 8)은 같은 WidgetLayoutStore 인터페이스로 교체한다.
 */
import { getStored, setStored } from "@/lib/storage";

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
/** 기본 배치: 저장공간은 추가 목록에만 둔다. */
export const defaultWidgetLayout: readonly WidgetId[] = ["flow", "recent", "trend", "active", "queue", "collect"];

const isWidgetId = (value: unknown): value is WidgetId => typeof value === "string" && (widgetIds as readonly string[]).includes(value);

/** 저장된 값 검증: 알 수 없는 ID·중복 제거. 배열이 아니면 null(기본 배치 사용). */
export const sanitizeLayout = (value: unknown): WidgetId[] | null => {
  if (!Array.isArray(value)) return null;
  return [...new Set(value.filter(isWidgetId))];
};

export const availableWidgets = (layout: readonly WidgetId[]) => widgetIds.filter((id) => !layout.includes(id));

export type LayoutAction =
  | { type: "move"; id: WidgetId; direction: -1 | 1 }
  | { type: "remove"; id: WidgetId }
  | { type: "add"; id: WidgetId }
  | { type: "drop"; id: WidgetId; before: WidgetId | null }
  | { type: "reset" };

/** 편집 중 배치 변경(와이어프레임 widget-move/remove/add/drop/reset과 같은 규칙). 바뀌지 않으면 같은 배열을 돌려준다. */
export const layoutReducer = (layout: readonly WidgetId[], action: LayoutAction): readonly WidgetId[] => {
  switch (action.type) {
    case "move": {
      const from = layout.indexOf(action.id);
      const to = from + action.direction;
      if (from < 0 || to < 0 || to >= layout.length) return layout;
      const next = [...layout];
      [next[from], next[to]] = [next[to], next[from]];
      return next;
    }
    case "remove":
      return layout.includes(action.id) ? layout.filter((id) => id !== action.id) : layout;
    case "add":
      return layout.includes(action.id) ? layout : [...layout, action.id];
    case "drop": {
      if (!layout.includes(action.id) || action.id === action.before) return layout;
      const next = layout.filter((id) => id !== action.id);
      const at = action.before ? next.indexOf(action.before) : -1;
      next.splice(at < 0 ? next.length : at, 0, action.id);
      return next.every((id, index) => id === layout[index]) ? layout : next;
    }
    case "reset":
      return [...defaultWidgetLayout];
  }
};

export const sameLayout = (a: readonly WidgetId[], b: readonly WidgetId[]) =>
  a.length === b.length && a.every((id, index) => id === b[index]);

/** 계정별 배치 저장소 경계. 서버 저장(하위 Issue 8)으로 바꿀 때 이 인터페이스만 구현한다. */
export interface WidgetLayoutStore {
  load(accountId: string): WidgetId[] | null;
  save(accountId: string, layout: readonly WidgetId[]): boolean;
}

export const widgetLayoutKey = (accountId: string) => `esb_operations_widgets_${accountId}`;

/** 브라우저 저장소 구현: 읽기·쓰기 실패는 현재 화면에서만 유지(예외 전파 없음). */
export const browserWidgetLayoutStore: WidgetLayoutStore = {
  load: (accountId) => sanitizeLayout(getStored<unknown>(widgetLayoutKey(accountId), null)),
  save: (accountId, layout) => {
    setStored(widgetLayoutKey(accountId), [...layout]);
    return sanitizeLayout(getStored<unknown>(widgetLayoutKey(accountId), null))?.join() === layout.join();
  },
};

/** 저장 결과 문구(와이어프레임 widgets-save). */
export const savedLayoutMessage = (role: "admin" | "user", count: number) =>
  `배치 저장됨 · ${role === "admin" ? "관리자" : "일반 사용자"} 계정 · 위젯 ${count}개`;
export const cancelledLayoutMessage = "편집 취소 · 이전 배치 유지";
