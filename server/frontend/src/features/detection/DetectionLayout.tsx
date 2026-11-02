import { IconArrowLeft } from "@tabler/icons-react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { SideNav } from "@/components/layout/SideNav";
import { Button } from "@/components/ui/button";
import { activeDetectionMenu, detectionMenu, paths } from "@/app/routes";

export type DetectionReturnState = { returnTo?: string };

/** 하위 화면(상세·모델 교체)에서 돌아갈 주소: 같은 탐지 설정 안에서 넘겨받은 주소, 없으면 해당 메뉴 첫 화면. */
export const detectionBackTarget = (pathname: string, state: unknown) => {
  const menu = detectionMenu.find((item) => pathname.startsWith(item.to + "/"));
  if (!menu) return null;
  const returnTo = (state as DetectionReturnState | null)?.returnTo;
  const target =
    typeof returnTo === "string" &&
    returnTo.startsWith(paths.detection + "/") &&
    !returnTo.startsWith("//") &&
    new URL(returnTo, "https://local.invalid").pathname !== pathname
      ? returnTo
      : menu.to;
  return { to: target, label: detectionMenu.find((item) => target.startsWith(item.to))?.label ?? menu.label };
};

/** 탐지 설정: 사이드바 4메뉴(운영 설정·룰 설정·머신러닝 모델·평가 결과) + 하위 화면 상단 뒤로가기 아이콘. */
export const DetectionLayout = () => {
  const location = useLocation();
  const back = detectionBackTarget(location.pathname, location.state);
  return (
    <div className="flex flex-col gap-6 md:flex-row md:items-start">
      <SideNav
        label="탐지 설정 메뉴"
        heading="탐지 설정"
        menuId="detection-menu"
        activeId={activeDetectionMenu(location.pathname)}
        groups={[{ items: detectionMenu.map((item) => ({ id: item.id, label: item.label, to: item.to })) }]}
      />
      <div className="grid min-w-0 flex-1 gap-6">
        {back && (
          <div>
            <Button asChild variant="outline" size="icon" title={`뒤로 · ${back.label}`}>
              <Link to={back.to} aria-label={`뒤로 · ${back.label}`}>
                <IconArrowLeft aria-hidden="true" />
              </Link>
            </Button>
          </div>
        )}
        <Outlet />
      </div>
    </div>
  );
};
