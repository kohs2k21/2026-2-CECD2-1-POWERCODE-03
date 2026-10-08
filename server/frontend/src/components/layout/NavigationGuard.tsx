import { useEffect } from "react";
import { useBlocker, useLocation } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import {
  shouldBlockDraftNavigation,
  useDraftStore,
} from "../../stores/draftStore";

export const useDraftNavigationGuard = () => {
  const drafts = useDraftStore((state) => state.drafts);
  const location = useLocation();
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    shouldBlockDraftNavigation(
      drafts,
      currentLocation.pathname,
      nextLocation.pathname,
    ),
  );
  const dirty = Object.values(drafts).some((draft) => draft.dirty);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  return { blocker, drafts, currentPath: location.pathname };
};
export const NavigationGuard = ({
  logoutRequested,
  onCancelLogout,
  onLogout,
}: {
  logoutRequested: boolean;
  onCancelLogout: () => void;
  onLogout: () => void;
}) => {
  const { blocker, currentPath } = useDraftNavigationGuard();
  const open = blocker.state === "blocked" || logoutRequested;
  const cancel = () => {
    if (blocker.state === "blocked") blocker.reset();
    onCancelLogout();
  };
  const discard = () => {
    if (logoutRequested) {
      useDraftStore.getState().reset();
      onLogout();
      return;
    }
    if (blocker.state === "blocked") {
      // The blocked transition leaves the current editor; internal tab switches never reach this branch.
      useDraftStore.getState().discard(currentPath);
      blocker.proceed();
    }
  };
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(value) => {
        if (!value) cancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="guard-overlay" />
        <Dialog.Content className="guard-dialog">
          <Dialog.Title>저장하지 않은 변경 내용</Dialog.Title>
          <Dialog.Description>
            계속 편집하거나 변경 내용을 버리고 이동할 수 있습니다.
          </Dialog.Description>
          <div className="guard-actions">
            <button type="button" onClick={cancel}>
              계속 편집
            </button>
            <button type="button" onClick={discard}>
              변경 내용 버리고 이동
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
