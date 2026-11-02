import { useState } from "react";
import { LinkTabs } from "@/components/layout/LinkTabs";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { urlOptions } from "@/app/routes";
import { useUrlOption } from "@/hooks/useUrlState";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { OperationsObservation } from "./components/OperationsObservation";
import { InputControls } from "./components/InputControls";
import { inputErrorText, useSourcesQuery } from "./data/inputs";

const tabLabels = { overview: "개요", collect: "수집" } as const;

/** 수집 탭: 입력 선택(관리자, 기존 /api/inputs 연결 유지). 그래프·건수·지연 등은 하위 Issue(운영 현황). */
const CollectTab = () => {
  const { user } = useAuthSession();
  const sources = useSourcesQuery();
  const [selected, setSelected] = useState("");
  const sourceId = sources.data?.find((source) => source.id === selected)?.id ?? sources.data?.[0]?.id ?? "";
  return (
    <div className="grid gap-6">
      {user?.userType === "admin" && (sources.data?.length ?? 0) > 1 && (
        <div className="grid max-w-sm gap-2">
          <Label htmlFor="collect-source">출처</Label>
          <NativeSelect id="collect-source" value={sourceId} onChange={(e) => setSelected(e.target.value)}>
            {sources.data?.map((source) => (
              <NativeSelectOption key={source.id} value={source.id}>
                {source.displayName} ({source.datasetKey})
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
      )}
      {user?.userType === "admin" &&
        (sources.isPending ? (
          <LoadingState />
        ) : sources.isError ? (
          <ErrorState title={inputErrorText(sources.error)} onRetry={() => void sources.refetch()} />
        ) : sourceId ? (
          <InputControls key={sourceId} sourceId={sourceId} />
        ) : (
          <p className="text-sm text-muted-foreground">저장된 데이터 출처가 없습니다. 먼저 Collector로 로그를 저장하세요.</p>
        ))}
    </div>
  );
};

/** 운영 현황: 개요 / 수집 URL 탭(tab). */
export const OperationsPage = () => {
  const tab = useUrlOption("tab", urlOptions.operationsTab);
  return (
    <div className="grid gap-6">
      <ScreenTitle>운영 현황</ScreenTitle>
      <LinkTabs
        label="운영 현황 보기"
        activeId={tab.value}
        tabs={urlOptions.operationsTab.map((id) => ({ id, label: tabLabels[id], to: tab.hrefFor(id) }))}
      />
      {tab.value === "overview" ? <OperationsObservation /> : <CollectTab />}
    </div>
  );
};
