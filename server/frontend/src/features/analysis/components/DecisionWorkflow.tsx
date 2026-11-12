import { useState } from "react";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  FEEDBACK_REASON_MAX,
  analysisWriteErrorTitle,
  feedbackKinds,
  type DecisionDetail,
  type FeedbackKind,
  type WorkflowState,
} from "../data/analysisApi";
import { useFeedbackSave, useWorkflowChange } from "../data/useAnalysis";
import { classifyIcon, workflowIcons } from "../icons";
import { classifyLabel, feedbackKindLabels, workflowActions } from "../labels";

/**
 * 대화상자 안 처리 버튼: 현재 상태에서 허용된 보류·완료(감지됨 = 처리 보류·처리 완료, 보류 = 처리 완료만). 주 버튼 = 처리 완료(workflowActions 주 버튼).
 * 원래 상태로 복원은 대화상자 없이 바로 바꾼다.
 */
const recordActions = (state: WorkflowState) => workflowActions[state].filter(([to]) => to !== "detected");

export type WorkflowRecordBodyProps = {
  state: WorkflowState;
  kind: FeedbackKind;
  reason: string;
  onKind: (kind: FeedbackKind) => void;
  onReason: (reason: string) => void;
  onSubmit: (to: WorkflowState) => void;
  pending: boolean;
  saveError: unknown;
  changeError: unknown;
};

/**
 * 로그 분류하기 대화상자 본문(사용자 11/13, 와이어프레임 feedbackBox 항목을 옮김): 기록 유형·조사·조치 사유(선택, 최대 2000자) + 처리 보류·처리 완료.
 * 처리 버튼은 사유가 있으면 먼저 기록을 저장한 뒤 처리 상태를 바꾼다. 저장된 기록은 상세 판정 기록에 읽기 전용으로 보인다. 닫기는 X·Esc(취소 버튼 없음).
 */
export const WorkflowRecordBody = ({ state, kind, reason, onKind, onReason, onSubmit, pending, saveError, changeError }: WorkflowRecordBodyProps) => (
  <>
    <div className="grid gap-2">
      <Label htmlFor="analysis-feedback-kind">기록 유형</Label>
      <NativeSelect id="analysis-feedback-kind" value={kind} disabled={pending} onChange={(event) => onKind(event.target.value as FeedbackKind)}>
        {feedbackKinds.map((value) => (
          <NativeSelectOption key={value} value={value}>
            {feedbackKindLabels[value]}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
    <div className="grid gap-2">
      <Label htmlFor="analysis-feedback-reason">조사·조치 사유</Label>
      <Textarea
        id="analysis-feedback-reason"
        rows={3}
        className="max-h-60"
        maxLength={FEEDBACK_REASON_MAX}
        placeholder="확인한 사실과 조치 사유를 입력하세요."
        disabled={pending}
        value={reason}
        onChange={(event) => onReason(event.target.value)}
      />
    </div>
    {Boolean(saveError) && <ErrorState title={analysisWriteErrorTitle(saveError, "피드백 저장")} />}
    {Boolean(changeError) && (
      <span role="alert" className="text-sm text-destructive-text">
        {analysisWriteErrorTitle(changeError, "업무 분류")}
      </span>
    )}
    <DialogFooter>
      {recordActions(state).map(([to, label, primary]) => {
        const Icon = workflowIcons[to];
        return (
          <Button key={to} variant={primary ? "default" : "outline"} disabled={pending} data-workflow-to={to} onClick={() => onSubmit(to)}>
            <Icon aria-hidden="true" />
            {label}
          </Button>
        );
      })}
    </DialogFooter>
  </>
);

/** 대화상자 껍데기(가운데 Modal, Esc·바깥 클릭·X = 닫기, 포커스는 대화상자 안). 처리 중에는 닫지 않는다. */
export const WorkflowRecordDialog = ({
  open,
  onOpenChange,
  ...body
}: WorkflowRecordBodyProps & { open: boolean; onOpenChange: (open: boolean) => void }) => (
  <Modal
    isOpen={open}
    onOpenChange={onOpenChange}
    size="md"
    title="업무 분류"
    description="선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다."
  >
    <WorkflowRecordBody {...body} />
  </Modal>
);

/**
 * 처리 상태 버튼(와이어프레임 dWorkflow, 아이콘은 #100 이전 상세 화면과 같은 `icons.ts` workflowIcons).
 * - 관리자(사용자 11/13): 감지됨 = [로그 분류하기], 보류 = [원래 상태로 복원][로그 분류하기], 완료 = [원래 상태로 복원].
 *   로그 분류하기는 가운데 대화상자를 열고, 안에서 처리 보류·처리 완료를 누르면 사유가 있을 때 기록을 먼저 저장하고(성공하면 입력을 비움 →
 *   다시 눌러도 두 번 저장하지 않음) 처리 상태를 바꾼 뒤 닫는다. 기록 저장이 실패하면 상태를 바꾸지 않는다. 실패는 대화상자를 연 채 오류 그대로.
 * - 일반 사용자: 조사·조치 기록 권한이 없어(admin_required) 감지됨 = [처리 보류][처리 완료], 보류 = [원래 상태로 복원][처리 완료]를 바로 바꾼다.
 * - 원래 상태로 복원은 누구나 바로 바꾼다.
 * 서버가 현재 상태(expectedState)를 확인한다. 실패(다른 사용자가 먼저 바꿈 409 등)는 `업무 분류 실패 · <코드>`(와이어프레임 항목 이름 업무 분류)로 그대로 보이고 상세를 다시 읽는다.
 */
export const WorkflowActions = ({ detail, canRecord }: { detail: DecisionDetail; canRecord: boolean }) => {
  const change = useWorkflowChange(detail);
  const save = useFeedbackSave(detail.id);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<FeedbackKind>("action");
  const [reason, setReason] = useState("");
  const pending = change.isPending || save.isPending;
  const state = detail.workflow.state;
  const openDialog = () => {
    change.reset();
    save.reset();
    setOpen(true);
  };
  const submit = async (to: WorkflowState) => {
    const trimmed = reason.trim();
    try {
      if (trimmed) {
        await save.mutateAsync({ kind, reason: trimmed });
        setReason("");
      }
      await change.mutateAsync(to);
      setOpen(false);
    } catch {
      // 실패는 각 변경의 오류 상태로 대화상자 안에 그대로 보인다.
    }
  };
  const restore = state !== "detected" && (
    <Button size="sm" variant="outline" disabled={pending} data-workflow-to="detected" onClick={() => change.mutate("detected")}>
      <workflowIcons.detected aria-hidden="true" />
      원래 상태로 복원
    </Button>
  );
  const ClassifyIcon = classifyIcon;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {canRecord ? (
        <>
          {restore}
          {recordActions(state).length > 0 && (
            <Button size="sm" disabled={pending} data-workflow-classify="true" onClick={openDialog}>
              <ClassifyIcon aria-hidden="true" />
              {classifyLabel}
            </Button>
          )}
        </>
      ) : (
        workflowActions[state].map(([to, label, primary]) => {
          const Icon = workflowIcons[to];
          return (
            <Button key={to} size="sm" variant={primary ? "default" : "outline"} disabled={pending} data-workflow-to={to} onClick={() => change.mutate(to)}>
              <Icon aria-hidden="true" />
              {label}
            </Button>
          );
        })
      )}
      {change.isError && !open && (
        <span role="alert" className="text-sm text-destructive-text">
          {analysisWriteErrorTitle(change.error, "업무 분류")}
        </span>
      )}
      {canRecord && (
        <WorkflowRecordDialog
          open={open}
          onOpenChange={(next) => {
            if (!next && !pending) setOpen(false);
          }}
          state={state}
          kind={kind}
          reason={reason}
          onKind={setKind}
          onReason={setReason}
          onSubmit={(to) => void submit(to)}
          pending={pending}
          saveError={save.error}
          changeError={change.error}
        />
      )}
    </div>
  );
};
