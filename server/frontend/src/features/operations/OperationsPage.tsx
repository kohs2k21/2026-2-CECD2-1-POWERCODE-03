import { Suspense, lazy } from "react";
import { LinkTabs } from "@/components/layout/LinkTabs";
import { LoadingState } from "@/components/ui/feedback";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { urlOptions } from "@/app/routes";
import { useUrlOption } from "@/hooks/useUrlState";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { CollectTab } from "./components/CollectTab";

/** 위젯 격자(react-grid-layout)·애니메이션 묶음은 개요 탭에서만 쓰므로 따로 불러온다. */
const OverviewTab = lazy(() => import("./components/OverviewTab").then((module) => ({ default: module.OverviewTab })));

const tabLabels = { overview: "개요", collect: "수집" } as const;

/** 운영 현황: 개요(위젯) / 수집 URL 탭(tab). user·admin 공용, 입력 모드 변경만 관리자. */
export const OperationsPage = () => {
  const tab = useUrlOption("tab", urlOptions.operationsTab);
  const { user } = useAuthSession();
  return (
    <div className="grid gap-6">
      <ScreenTitle>운영 현황</ScreenTitle>
      <LinkTabs
        label="운영 현황 보기"
        activeId={tab.value}
        tabs={urlOptions.operationsTab.map((id) => ({ id, label: tabLabels[id], to: tab.hrefFor(id) }))}
      />
      {tab.value === "overview" ? (
        <Suspense fallback={<LoadingState />}>
          <OverviewTab key={user?.id ?? ""} collectHref={tab.hrefFor("collect")} />
        </Suspense>
      ) : (
        <CollectTab />
      )}
    </div>
  );
};
