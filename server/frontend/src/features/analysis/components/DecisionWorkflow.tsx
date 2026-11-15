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
  feedbackOutcomes,
  type DecisionDetail,
  type FeedbackKind,
  type FeedbackOutcome,
  type FeedbackRecord,
  type WorkflowState,
} from "../data/analysisApi";
import { useFeedbackEdit, useFeedbackSave, useWorkflowChange } from "../data/useAnalysis";
import { classifyIcon, workflowIcons } from "../icons";
import { DASH, classifyLabel, feedbackKindLabels, feedbackOutcomeLabels, feedbackOutcomeTitle, workflowActions } from "../labels";

/** 확인 결과를 골랐는데 사유가 비었을 때(기록은 사유 1~2000자라 저장할 수 없음). 문구는 와이어프레임 feedbackBox 빈 사유 안내 그대로. */
export const reasonRequiredText = "조사·조치 사유를 입력하세요.";

/**
 * 대화상자 안 처리 버튼: 현재 상태에서 허용된 보류·완료(감지됨 = 처리 보류·처리 완료, 보류 = 처리 완료만). 주 버튼 = 처리 완료(workflowActions 주 버튼).
 * 원래 상태로 복원은 대화상자 없이 바로 바꾼다.
 */
const recordActions = (state: WorkflowState) => workflowActions[state].filter(([to]) => to !== "detected");

export type WorkflowRecordBodyProps = {
  state: WorkflowState;
  kind: FeedbackKind;
  outcome: FeedbackOutcome | null;
  reason: string;
  onKind: (kind: FeedbackKind) => void;
  onOutcome: (outcome: FeedbackOutcome | null) => void;
  onReason: (reason: string) => void;
  /** 확인 결과를 골랐는데 사유가 비어 처리 버튼을 막았다(reasonRequiredText 표시). */
  reasonRequired: boolean;
  onSubmit: (to: WorkflowState) => void;
  pending: boolean;
  saveError: unknown;
  changeError: unknown;
};

type RecordFieldsProps = Pick<WorkflowRecordBodyProps, "kind" | "outcome" | "reason" | "onKind" | "onOutcome" | "onReason" | "reasonRequired" | "pending"> & {
  /** 입력 id 접두(로그 분류하기 `analysis-feedback`, 기록 수정 `analysis-feedback-edit`). */
  idPrefix?: string;
};
/** 기록 입력 칸(로그 분류하기·기록 수정 공통): 기록 유형·확인 결과(선택, 029)·조사·조치 사유(최대 2000자) + 빈 사유 안내. */
const RecordFields = ({ kind, outcome, reason, onKind, onOutcome, onReason, reasonRequired, pending, idPrefix = "analysis-feedback" }: RecordFieldsProps) => (
  <>
    <div className="grid gap-2">
      <Label htmlFor={`${idPrefix}-kind`}>기록 유형</Label>
      <NativeSelect id={`${idPrefix}-kind`} value={kind} disabled={pending} onChange={(event) => onKind(event.target.value as FeedbackKind)}>
        {feedbackKinds.map((value) => (
          <NativeSelectOption key={value} value={value}>
            {feedbackKindLabels[value]}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
    <div className="grid gap-2">
      <Label htmlFor={`${idPrefix}-outcome`}>{feedbackOutcomeTitle}</Label>
      <NativeSelect
        id={`${idPrefix}-outcome`}
        value={outcome ?? ""}
        disabled={pending}
        onChange={(event) => onOutcome((event.target.value || null) as FeedbackOutcome | null)}
      >
        <NativeSelectOption value="">{DASH}</NativeSelectOption>
        {feedbackOutcomes.map((value) => (
          <NativeSelectOption key={value} value={value}>
            {feedbackOutcomeLabels[value]}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </div>
    <div className="grid gap-2">
      <Label htmlFor={`${idPrefix}-reason`}>조사·조치 사유</Label>
      <Textarea
        id={`${idPrefix}-reason`}
        rows={3}
        className="max-h-60"
        maxLength={FEEDBACK_REASON_MAX}
        placeholder="확인한 사실과 조치 사유를 입력하세요."
        disabled={pending}
        value={reason}
        aria-invalid={reasonRequired || undefined}
        onChange={(event) => onReason(event.target.value)}
      />
      {reasonRequired && (
        <span role="alert" className="text-sm text-destructive-text">
          {reasonRequiredText}
        </span>
      )}
    </div>
  </>
);

/**
 * 로그 분류하기 대화상자 본문(사용자 11/13, 와이어프레임 feedbackBox 항목을 옮김): 기록 유형·확인 결과(선택, 029)·조사·조치 사유(선택, 최대 2000자) + 처리 보류·처리 완료.
 * 처리 버튼은 사유가 있으면 먼저 기록을 저장한 뒤 처리 상태를 바꾼다. 확인 결과는 기록에 붙으므로 고르면 사유가 필요하다.
 * 저장된 기록은 상세 판정 기록에 읽기 전용으로 보인다. 닫기는 X·Esc(취소 버튼 없음).
 */
export const WorkflowRecordBody = ({
  state,
  kind,
  outcome,
  reason,
  onKind,
  onOutcome,
  onReason,
  reasonRequired,
  onSubmit,
  pending,
  saveError,
  changeError,
}: WorkflowRecordBodyProps) => (
  <>
    <RecordFields kind={kind} outcome={outcome} reason={reason} onKind={onKind} onOutcome={onOutcome} onReason={onReason} reasonRequired={reasonRequired} pending={pending} />
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

/** 기록 수정 실패 제목(사용자 11/15 승인 문구): `피드백 수정 실패 · <오류 코드>`(피드백 저장 실패와 같은 형식). */
export const feedbackEditErrorTitle = (error: unknown) => analysisWriteErrorTitle(error, "피드백 수정");

export type FeedbackEditBodyProps = Pick<WorkflowRecordBodyProps, "kind" | "outcome" | "reason" | "onKind" | "onOutcome" | "onReason" | "reasonRequired" | "pending"> & {
  /** 값이 저장된 것과 달라 저장할 수 있다. */
  canSave: boolean;
  onSave: () => void;
  error: unknown;
};
/** 기록 수정 대화상자 본문: 같은 입력 칸 + `피드백 수정 실패 · <코드>` + [저장]. */
export const FeedbackEditBody = ({ canSave, onSave, error, ...fields }: FeedbackEditBodyProps) => (
  <>
    <RecordFields idPrefix="analysis-feedback-edit" {...fields} />
    {Boolean(error) && <ErrorState title={feedbackEditErrorTitle(error)} />}
    <DialogFooter>
      <Button disabled={fields.pending || !canSave} data-feedback-edit-save="true" onClick={onSave}>
        저장
      </Button>
    </DialogFooter>
  </>
);
/** 기록 수정 대화상자 껍데기: 로그 분류하기와 같은 `업무 분류` 제목·설명, Esc·바깥 클릭·X = 닫기(처리 중에는 닫지 않음). */
export const FeedbackEditModal = ({ onOpenChange, ...body }: FeedbackEditBodyProps & { onOpenChange: (open: boolean) => void }) => (
  <Modal isOpen onOpenChange={onOpenChange} size="md" title="업무 분류" description="선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다.">
    <FeedbackEditBody {...body} />
  </Modal>
);

/**
 * 조사·조치 기록 수정(사용자 11/15, 032): 판정 기록 줄의 [수정]이 같은 `업무 분류` 대화상자를 저장된 값으로 채워 연다.
 * 처리 상태 버튼 대신 [저장] 하나 — 처리 상태는 바꾸지 않는다. 수정은 기록을 고치는 것이라 사유가 비면 저장하지 않고 빈 사유 안내를 보인다.
 * 값이 저장된 것과 같으면 [저장]을 막는다. 저장에 성공하면 닫고, 실패(다른 판본이 먼저 저장됨 409 등)는 대화상자 안에 오류 그대로.
 * 서버는 기존 기록을 고치지 않고 정정 기록을 추가한다(삭제 없음).
 * 연 때의 판본을 고정해 보낸다(expectedId): 그 사이 다른 판본이 저장돼 409가 나면 다시 눌러도 덮어쓰지 않고, 닫고 다시 열면 새 판본 값으로 채워진다.
 */
export const FeedbackEditDialog = ({ decisionId, record: opened, onClose }: { decisionId: string; record: FeedbackRecord; onClose: () => void }) => {
  const edit = useFeedbackEdit(decisionId);
  const [record] = useState(opened);
  const [kind, setKind] = useState<FeedbackKind>(record.kind);
  const [outcome, setOutcome] = useState<FeedbackOutcome | null>(record.outcome);
  const [reason, setReason] = useState(record.reason);
  const [reasonRequired, setReasonRequired] = useState(false);
  const pending = edit.isPending;
  const trimmed = reason.trim();
  const save = async () => {
    if (!trimmed) {
      setReasonRequired(true);
      return;
    }
    try {
      await edit.mutateAsync({ record, kind, reason: trimmed, outcome });
      onClose();
    } catch {
      // 실패는 대화상자 안에 오류 그대로 보인다.
    }
  };
  return (
    <FeedbackEditModal
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
      kind={kind}
      outcome={outcome}
      reason={reason}
      onKind={setKind}
      onOutcome={setOutcome}
      onReason={(next) => {
        setReason(next);
        if (next.trim()) setReasonRequired(false);
      }}
      reasonRequired={reasonRequired}
      pending={pending}
      canSave={!(kind === record.kind && outcome === record.outcome && trimmed === record.reason)}
      onSave={() => void save()}
      error={edit.error}
    />
  );
};

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
  const [outcome, setOutcome] = useState<FeedbackOutcome | null>(null);
  const [reason, setReason] = useState("");
  const [reasonRequired, setReasonRequired] = useState(false);
  const pending = change.isPending || save.isPending;
  const state = detail.workflow.state;
  const openDialog = () => {
    change.reset();
    save.reset();
    setReasonRequired(false);
    setOpen(true);
  };
  const submit = async (to: WorkflowState) => {
    const trimmed = reason.trim();
    // 확인 결과는 기록(사유 필수)에 붙는다: 고른 채 사유가 비면 저장·상태 변경 없이 안내만(고른 값을 버리지 않는다).
    if (outcome !== null && !trimmed) {
      setReasonRequired(true);
      return;
    }
    try {
      if (trimmed) {
        await save.mutateAsync({ kind, reason: trimmed, outcome });
        setReason("");
        setOutcome(null);
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
          outcome={outcome}
          reason={reason}
          onKind={setKind}
          onOutcome={(next) => {
            setOutcome(next);
            if (next === null) setReasonRequired(false);
          }}
          onReason={(next) => {
            setReason(next);
            if (next.trim()) setReasonRequired(false);
          }}
          reasonRequired={reasonRequired}
          onSubmit={(to) => void submit(to)}
          pending={pending}
          saveError={save.error}
          changeError={change.error}
        />
      )}
    </div>
  );
};
