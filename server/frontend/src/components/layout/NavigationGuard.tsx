import { useEffect } from "react";
import { useBlocker, useLocation } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
          {logoutRequested
            ? "로그아웃하면 현재 세션의 편집 내용이 삭제됩니다."
            : "저장 후 이동하면 현재 세션에 초안을 보관합니다. 서버에 저장하거나 운영에 적용하지 않습니다."}
        </DialogDescription>
        <DialogFooter className="guard-actions">
          {logoutRequested && (
            <Button variant="outline" onClick={cancel}>
              계속 편집
            </Button>
          )}
          {!logoutRequested && blocker.state === "blocked" && (
            <Button onClick={() => blocker.proceed()}>
              변경 사항 저장 후 이동
            </Button>
          )}
          <Button variant="outline" onClick={discard}>
            버리고 이동
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
