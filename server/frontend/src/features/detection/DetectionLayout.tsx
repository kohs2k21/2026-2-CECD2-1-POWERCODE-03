import { useEffect, useRef } from "react";
import { IconArrowLeft } from "@tabler/icons-react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { SideNav } from "@/components/layout/SideNav";
import { Button } from "@/components/ui/button";
import { activeDetectionMenu, detectionMenu, paths } from "@/app/routes";
import { observeHeight } from "@/lib/observeHeight";
import { detectionToc } from "./detectionToc";
import { hashId, useActiveSection, useDetectionScroll } from "./useDetectionScroll";

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

/**
 * 하위 화면 뒤로가기 줄: 스크롤해도 머리글 아래에 고정(sticky). 위 여백(page-block-padding)까지 바탕으로 덮어 고정 시 사이드바와 같은 높이에 오고
 * 아래로 지나가는 내용이 틈으로 비치지 않는다. 음수 여백으로 흐름 속 위치·간격은 그대로다.
 * 높이는 --sticky-block-height로 알려 입력 초점·해시 이동이 이 줄 밑에 가리지 않게 한다(룰 설정 저장 줄과 같은 변수 — 두 줄은 한 화면에 함께 있지 않다).
 * 부모가 grid면 sticky가 자기 행 안에서만 움직이므로 본문 열은 flex다.
 */
const DetectionBackBar = ({ to, label }: { to: string; label: string }) => {
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => observeHeight(bar.current, "--sticky-block-height"), []);
  return (
    <div
      ref={bar}
      data-detection-back
      className="sticky top-(--top-nav-offset) z-(--z-sticky) mt-[calc(-1*var(--page-block-padding))] -mb-2 bg-background pt-(--page-block-padding) pb-2"
    >
      <Button asChild variant="outline" size="icon" title={`뒤로 · ${label}`}>
        <Link to={to} aria-label={`뒤로 · ${label}`}>
          <IconArrowLeft aria-hidden="true" />
        </Link>
      </Button>
    </div>
  );
};

/**
 * 탐지 설정: 사이드바 4메뉴(운영 설정·룰 설정·머신러닝 모델·평가 결과) + 현재 메뉴 아래 화면 영역 목차 + 하위 화면 상단 고정 뒤로가기 아이콘.
 * 목차 항목은 메뉴 첫 화면의 영역(#id)으로 이동한다. 첫 화면에서는 같은 화면 안 이동(쿼리 유지·replace), 하위 화면에서는 첫 화면으로 이동 후 스크롤.
 */
export const DetectionLayout = () => {
  const location = useLocation();
  useDetectionScroll();
  const back = detectionBackTarget(location.pathname, location.state);
  const activeMenu = activeDetectionMenu(location.pathname);
  const onMenuPage = detectionMenu.some((item) => item.id === activeMenu && item.to === location.pathname);
  const activeSection = useActiveSection(onMenuPage && activeMenu ? detectionToc[activeMenu] : null, hashId(location.hash));
  return (
    <div className="flex flex-col gap-6 md:flex-row md:items-start">
      <SideNav
        label="탐지 설정 메뉴"
        heading="탐지 설정"
        menuId="detection-menu"
        activeId={activeMenu}
        groups={[{
          items: detectionMenu.map((item) => ({
            id: item.id,
            label: item.label,
            to: item.to,
            sub: item.id === activeMenu
              ? detectionToc[item.id].map((section) => ({
                  id: section.id,
                  label: section.label,
                  to: { pathname: item.to, search: onMenuPage ? location.search : "", hash: section.id },
                  replace: onMenuPage,
                  current: onMenuPage && activeSection === section.id,
                }))
              : undefined,
          })),
        }]}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {back && <DetectionBackBar to={back.to} label={back.label} />}
        <Outlet />
      </div>
    </div>
  );
};
