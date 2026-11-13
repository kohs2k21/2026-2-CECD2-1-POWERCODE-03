import { useEffect, useRef } from "react";
import { useBlocker, useLocation } from "react-router-dom";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { shouldBlockDraftNavigation, useDraftStore } from "@/stores/draftStore";

export const useDraftNavigationGuard = () => {
  const drafts = useDraftStore((state) => state.drafts);
  const location = useLocation();
  // 이동 시점의 저장소 값으로 판단한다(같은 처리에서 초안을 저장 상태로 바꾼 뒤 바로 떠나는 화면이 있다 — 비교 평가 접수).
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    shouldBlockDraftNavigation(useDraftStore.getState().drafts, currentLocation.pathname, nextLocation.pathname),
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

/** 미저장 편집 이탈 확인(navigation.md): 저장 후 이동(primary) / 버리고 이동(outline), Escape·외부 클릭은 이동 취소. */
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
  // 버린 초안은 이동이 끝난 뒤 지운다. 먼저 지우면 편집 화면이 빈 초안으로 다시 그려져 주소를 고치며 이 이동을 덮을 수 있다.
  const discardAfterLeave = useRef<string | null>(null);
  useEffect(() => {
    if (!discardAfterLeave.current || discardAfterLeave.current === currentPath) return;
    useDraftStore.getState().discard(discardAfterLeave.current);
    discardAfterLeave.current = null;
  }, [currentPath]);
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
      discardAfterLeave.current = currentPath;
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
      <DialogContent showCloseButton={false} className="sm:max-w-sm">
        <DialogTitle>저장하지 않은 변경 내용</DialogTitle>
        <DialogDescription>
          {logoutRequested
            ? "로그아웃하면 현재 세션의 편집 내용이 삭제됩니다."
            : "저장 후 이동하면 현재 세션에 초안을 보관합니다. 서버에 저장하거나 운영에 적용하지 않습니다."}
        </DialogDescription>
        <DialogFooter>
          {logoutRequested && (
            <Button variant="outline" onClick={cancel}>
              계속 편집
            </Button>
          )}
          {!logoutRequested && blocker.state === "blocked" && (
            <Button onClick={() => blocker.proceed()}>변경 사항 저장 후 이동</Button>
          )}
          <Button variant="outline" onClick={discard}>
            버리고 이동
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
