import { create } from "zustand";

/** `leaveFree`: 앱 안 이동은 확인 없이 보낸다(값은 보관되므로 잃지 않는다). 새로고침·창 닫기·로그아웃 확인은 `dirty`를 따른다. */
type Draft = { value: unknown; dirty: boolean; leaveFree?: boolean };
type DraftState = {
  drafts: Record<string, Draft>;
  edit: (path: string, value: unknown, options?: { leaveFree?: boolean }) => void;
  markSaved: (path: string) => void;
  discard: (path: string) => void;
  reset: () => void;
};
// Only real editors register drafts. Empty route frames do not create one.
export const useDraftStore = create<DraftState>((set) => ({
  drafts: {},
  edit: (path, value, options) =>
    set((state) => ({
      drafts: { ...state.drafts, [path]: { value, dirty: true, ...(options?.leaveFree ? { leaveFree: true } : {}) } },
    })),
  markSaved: (path) =>
    set((state) => ({
      drafts: state.drafts[path]
        ? { ...state.drafts, [path]: { ...state.drafts[path], dirty: false } }
        : state.drafts,
    })),
  discard: (path) =>
    set((state) => {
      const drafts = { ...state.drafts };
      delete drafts[path];
      return { drafts };
    }),
  reset: () => set({ drafts: {} }),
}));
export const shouldBlockDraftNavigation = (
  drafts: DraftState["drafts"],
  currentPath: string,
  nextPath: string,
) => Boolean(drafts[currentPath]?.dirty) && !drafts[currentPath]?.leaveFree && currentPath !== nextPath;
