import { useEffect } from "react";
import { useBlocker, useLocation } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
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
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) cancel();
      }}
    >
      <DialogContent showCloseButton={false} className="ui-modal--sm">
        <DialogTitle>저장하지 않은 변경 내용</DialogTitle>
        <DialogDescription>
          계속 편집하거나 변경 내용을 버리고 이동할 수 있습니다.
        </DialogDescription>
        <div className="guard-actions">
          <Button variant="outline" onClick={cancel}>
            계속 편집
          </Button>
          <Button onClick={discard}>변경 내용 버리고 이동</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};
