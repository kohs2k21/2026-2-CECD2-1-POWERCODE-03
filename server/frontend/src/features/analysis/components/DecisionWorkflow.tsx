import { useState } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  FEEDBACK_REASON_MAX,
  analysisErrorTitle,
  analysisWriteErrorTitle,
  feedbackKinds,
  type DecisionDetail,
  type FeedbackKind,
} from "../data/analysisApi";
import { useDecisionFeedback, useFeedbackSave, useWorkflowChange } from "../data/useAnalysis";
import { feedbackKindLabels, stampText, workflowActions } from "../labels";

/**
 * 처리 상태 버튼(와이어프레임 dWorkflow): 감지됨 = [처리 보류][처리 완료], 보류 = [원래 상태로 복원][처리 완료], 완료 = [원래 상태로 복원].
 * 서버가 현재 상태(expectedState)를 확인한다. 실패(다른 사용자가 먼저 바꿈 409 등)는 `업무 분류 실패 · <코드>`(와이어프레임 항목 이름 업무 분류)로 그대로 보이고 상세를 다시 읽는다.
 */
export const WorkflowActions = ({ detail }: { detail: DecisionDetail }) => {
  const change = useWorkflowChange(detail);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {workflowActions[detail.workflow.state].map(([to, label, primary]) => (
        <Button
          key={to}
          size="sm"
          variant={primary ? "default" : "outline"}
          disabled={change.isPending}
          data-workflow-to={to}
          onClick={() => change.mutate(to)}
        >
          {label}
        </Button>
      ))}
      {change.isError && (
        <span role="alert" className="text-sm text-destructive-text">
          {analysisWriteErrorTitle(change.error, "업무 분류")}
        </span>
      )}
    </div>
  );
};

/**
 * 조사·조치 기록(와이어프레임 feedbackBox, 관리자에게만 보임): 기록 유형·사유(최대 2000자) 저장 + 저장된 기록(최신순).
 * 기록은 판정 값·확정 라벨·자동 학습에 반영되지 않는다(서버 014 권한으로도 분리).
 */
export const FeedbackCard = ({ detail }: { detail: DecisionDetail }) => {
  const records = useDecisionFeedback(detail.id, true);
  const save = useFeedbackSave(detail.id);
  const [kind, setKind] = useState<FeedbackKind>("action");
  const [reason, setReason] = useState("");
  const [status, setStatus] = useState("");
  const submit = () => {
    const trimmed = reason.trim();
    if (!trimmed) {
      setStatus("조사·조치 사유를 입력하세요.");
      return;
    }
    setStatus("");
    save.mutate({ kind, reason: trimmed }, { onSuccess: () => setReason("") });
  };
  return (
    <SectionCard title="조사·조치 기록" headingLevel={2}>
      <div className="grid gap-3">
        <div className="grid gap-2">
          <Label htmlFor="analysis-feedback-kind">기록 유형</Label>
          <NativeSelect id="analysis-feedback-kind" value={kind} onChange={(event) => setKind(event.target.value as FeedbackKind)}>
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
            maxLength={FEEDBACK_REASON_MAX}
            placeholder="확인한 사실과 조치 사유를 입력하세요."
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        <p className="text-sm text-muted-foreground">선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다.</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" disabled={save.isPending} onClick={submit}>
            피드백 저장
          </Button>
          <p role="status" className="text-sm text-muted-foreground">
            {status}
          </p>
        </div>
        {save.isError && <ErrorState title={analysisWriteErrorTitle(save.error, "피드백 저장")} />}
        {records.isPending && !records.data ? (
          <LoadingState />
        ) : records.isError && !records.data ? (
          <ErrorState title={analysisErrorTitle(records.error, "조사·조치 기록")} onRetry={() => void records.refetch()} />
        ) : records.data?.items.length ? (
          <ol className="grid gap-2 border-l pl-4 text-sm" aria-label="조사·조치 기록">
            {records.data.items.map((item) => (
              <li key={item.id} className="grid gap-0.5">
                <span className="text-xs text-muted-foreground tabular-nums">
                  {stampText(item.createdAt)} · {feedbackKindLabels[item.kind]} · {item.actorEmail ?? item.actorId ?? "—"}
                </span>
                <span className="whitespace-pre-wrap break-words">{item.reason}</span>
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </SectionCard>
  );
};
