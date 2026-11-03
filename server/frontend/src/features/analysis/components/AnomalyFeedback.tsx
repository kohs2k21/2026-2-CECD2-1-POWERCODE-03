import { useEffect, useRef, useState } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { getStoredToken } from "@/services/auth/session";
import { useDraftStore } from "@/stores/draftStore";
import {
  feedbackWriteAllowed,
  saveAnomalyFeedback,
  validFeedbackDraft,
  type FeedbackDraft,
} from "../data/anomalyEvidence";
type AnalysisDraft = { feedbackByEvent?: Record<string, FeedbackDraft> };
export const AnomalyFeedback = ({
  eventId,
  capability,
}: {
  eventId: string;
  capability: boolean;
}) => {
  const { user } = useAuthSession();
  const draftState = useDraftStore((state) => state.drafts["/analysis"]);
  const current = (draftState?.value ?? {}) as AnalysisDraft;
  const draft = current.feedbackByEvent?.[eventId] ?? {
    kind: "action",
    reason: "",
  };
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const token = getStoredToken();
  useEffect(
    () => () => controller.current?.abort(),
    [eventId, user?.id, token],
  );
  if (user?.userType !== "admin") return null;
  const edit = (next: FeedbackDraft) => {
    const store = useDraftStore.getState();
    const latest = (store.drafts["/analysis"]?.value ?? {}) as AnalysisDraft;
    store.edit("/analysis", {
      ...latest,
      feedbackByEvent: { ...latest.feedbackByEvent, [eventId]: next },
    });
    setMessage(null);
  };
  const submit = async () => {
    if (
      pending ||
      !feedbackWriteAllowed(user.userType, capability) ||
      !validFeedbackDraft(draft)
    )
      return;
    const request = new AbortController();
    controller.current = request;
    setPending(true);
    setMessage(null);
    try {
      const receipt = await saveAnomalyFeedback(eventId, draft, request.signal);
      if (request.signal.aborted || token !== getStoredToken()) return;
      const store = useDraftStore.getState();
      const latest = (store.drafts["/analysis"]?.value ?? {}) as AnalysisDraft;
      const remaining = { ...latest.feedbackByEvent };
      delete remaining[eventId];
      const next = { ...latest };
      if (Object.keys(remaining).length) next.feedbackByEvent = remaining;
      else delete next.feedbackByEvent;
      if (Object.keys(next).length) store.edit("/analysis", next);
      else store.discard("/analysis");
      setMessage(`피드백 서버 저장 완료 · ${receipt.savedAt}`);
    } catch (error) {
      if (!request.signal.aborted && token === getStoredToken())
        setMessage(
          error instanceof Error
            ? error.message
            : "피드백 저장에 실패했습니다.",
        );
    } finally {
      if (!request.signal.aborted && token === getStoredToken())
        setPending(false);
    }
  };
  return (
    <SectionCard title="조사·조치 기록" headingLevel={3} aria-label="이상 이벤트 조사·조치">
      <div className="grid gap-2">
        <Label htmlFor={`feedback-kind-${eventId}`}>기록 유형</Label>
        <NativeSelect
          id={`feedback-kind-${eventId}`}
          value={draft.kind}
          disabled={pending}
          onChange={(e) => edit({ ...draft, kind: e.target.value as FeedbackDraft["kind"] })}
        >
          <NativeSelectOption value="action">조치 기록</NativeSelectOption>
          <NativeSelectOption value="suspected-false-positive">오탐 의심</NativeSelectOption>
          <NativeSelectOption value="insufficient-evidence">근거 부족</NativeSelectOption>
        </NativeSelect>
      </div>
      <div className="grid gap-2">
        <Label htmlFor={`feedback-reason-${eventId}`}>조사·조치 사유</Label>
        <Textarea
          id={`feedback-reason-${eventId}`}
          value={draft.reason}
          disabled={pending}
          maxLength={2000}
          rows={4}
          onChange={(e) => edit({ ...draft, reason: e.target.value })}
          placeholder="확인한 사실과 조치 사유를 입력하세요."
        />
      </div>
      <p className="text-sm text-muted-foreground">선택은 확정 라벨이나 자동 학습으로 반영되지 않습니다.</p>
      {!capability && (
        <p className="text-sm text-muted-foreground">피드백 저장 미연결 · 입력 내용은 현재 세션의 초안입니다.</p>
      )}
      <div>
        <Button
          type="button"
          onClick={() => void submit()}
          disabled={!feedbackWriteAllowed(user.userType, capability) || !validFeedbackDraft(draft) || pending}
        >
          {pending ? "서버 저장 중" : "서버에 피드백 저장"}
        </Button>
      </div>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </SectionCard>
  );
};
