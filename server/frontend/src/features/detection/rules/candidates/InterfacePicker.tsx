import { useId, useMemo } from "react";
import { Combobox } from "@/components/ui/combobox";
import type { CandidateList } from "./candidateApi";
import { INTERFACE_SEARCH, INTERFACE_SEARCH_EMPTY, n } from "./candidateLabels";

type Result = NonNullable<CandidateList["result"]>;

/** 인터페이스 선택(주소 `interface`, 두 탭 공통). 수백 개일 수 있어 검색 선택(rule-candidates-ui.md 8절). */
export const InterfacePicker = ({ result, onInterface }: { result: Result; onInterface: (id: string) => void }) => {
  const labelId = useId();
  const selected = result.selectedInterfaceId;
  const summary = result.interfaces.find((item) => item.interfaceId === selected);
  const options = useMemo(() => result.interfaces.map((item) => ({ value: item.interfaceId, label: item.interfaceId, detail: `승인 가능 ${n(item.approvable)}` })),
    [result.interfaces]);
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <span id={labelId}>인터페이스</span>
      <Combobox
        size="sm"
        aria-labelledby={labelId}
        className="min-w-56"
        value={selected}
        onValueChange={onInterface}
        options={options}
        valueText={summary ? `${summary.interfaceId} · 승인 가능 ${n(summary.approvable)}` : undefined}
        searchLabel={INTERFACE_SEARCH}
        emptyText={INTERFACE_SEARCH_EMPTY}
      />
      {summary && <span data-interface-summary>거래 {n(summary.transactions)} · 관계 후보 {n(summary.relationCandidates)} · 처리 시간 후보 {n(summary.thresholdCandidates)}</span>}
    </div>
  );
};

