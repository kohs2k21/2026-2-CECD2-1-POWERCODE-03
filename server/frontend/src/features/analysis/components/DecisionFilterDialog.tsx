import { useState } from "react";
import { IconChevronDown, IconFilter } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { completenessOptions, signals, type CompletenessFilter, type SignalFilter } from "../data/analysisApi";
import { completenessFilterLabels, signalFilterLabels } from "../labels";

/** 검색 필터(와이어프레임 filterDialog): 이상 신호·검사 완료. 바꾸면 바로 목록에 반영하고, 조건 초기화로 둘 다 전체. */
export const DecisionFilterDialog = ({
  signal,
  completeness,
  onChange,
}: {
  signal: SignalFilter;
  completeness: CompletenessFilter;
  onChange: (next: { signal: SignalFilter; completeness: CompletenessFilter }) => void;
}) => {
  const [open, setOpen] = useState(false);
  const active = (signal !== "all" ? 1 : 0) + (completeness !== "all" ? 1 : 0);
  return (
    <Modal
      isOpen={open}
      onOpenChange={setOpen}
      size="sm"
      title="검색 필터 설정"
      description="목록에 표시할 판정의 조건을 설정합니다."
      trigger={
        <Button size="sm" variant="outline">
          <IconFilter aria-hidden="true" />
          검색 필터
          <IconChevronDown aria-hidden="true" />
        </Button>
      }
    >
      <div className="grid gap-2">
        <Label htmlFor="analysis-filter-signal">이상 신호</Label>
        <NativeSelect
          id="analysis-filter-signal"
          value={signal}
          onChange={(event) => onChange({ signal: event.target.value as SignalFilter, completeness })}
        >
          {signals.map((value) => (
            <NativeSelectOption key={value} value={value}>
              {signalFilterLabels[value]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>
      <div className="grid gap-2">
        <Label htmlFor="analysis-filter-completeness">검사 완료</Label>
        <NativeSelect
          id="analysis-filter-completeness"
          value={completeness}
          onChange={(event) => onChange({ signal, completeness: event.target.value as CompletenessFilter })}
        >
          {completenessOptions.map((value) => (
            <NativeSelectOption key={value} value={value}>
              {completenessFilterLabels[value]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" disabled={!active} onClick={() => onChange({ signal: "all", completeness: "all" })}>
          조건 초기화
        </Button>
        <Button size="sm" onClick={() => setOpen(false)}>
          닫기
        </Button>
      </div>
    </Modal>
  );
};
