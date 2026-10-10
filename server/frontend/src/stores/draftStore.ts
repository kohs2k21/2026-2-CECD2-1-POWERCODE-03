import { create } from "zustand";

type Draft = { value: unknown; dirty: boolean };
type DraftState = {
  drafts: Record<string, Draft>;
  edit: (path: string, value: unknown) => void;
  markSaved: (path: string) => void;
  discard: (path: string) => void;
  reset: () => void;
};
// Only real editors register drafts. Empty route frames do not create one.
export const useDraftStore = create<DraftState>((set) => ({
  drafts: {},
  edit: (path, value) =>
    set((state) => ({
      drafts: { ...state.drafts, [path]: { value, dirty: true } },
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
) => Boolean(drafts[currentPath]?.dirty) && currentPath !== nextPath;
