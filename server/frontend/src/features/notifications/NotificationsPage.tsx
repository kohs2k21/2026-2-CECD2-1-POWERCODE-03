import { useLocation, useNavigate } from "react-router-dom";
import { urlOptions } from "@/app/routes";
import { useUrlOption } from "@/hooks/useUrlState";
import { NotificationPanels, type NotificationReturnState } from "./NotificationPanels";

/**
 * 알림 페이지(`/notifications`, contracts/notifications.md 화면): 휴대폰 첫 화면. 본문은 상단 알림 버튼의 패널과 같은 부품
 * (같은 조회·읽음 흐름, 새 연결 없음). 보기(읽지 않음/전체)는 주소 `view`(replace)라 상세에서 돌아와도 유지된다.
 * 바로가기는 이 주소를 `state.returnTo`로 넘겨 판정·사건 상세의 돌아가기가 `알림으로`가 된다.
 */
export const NotificationsPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const view = useUrlOption("view", urlOptions.notificationsView);
  const linkState: NotificationReturnState = { returnTo: location.pathname + location.search };
  return (
    <div className="mx-auto grid w-full max-w-2xl gap-4">
      <h1 className="text-section-title">알림</h1>
      <NotificationPanels
        variant="page"
        view={view.value}
        onViewChange={(next) => navigate(view.hrefFor(next), { replace: true })}
        linkState={linkState}
      />
    </div>
  );
};
