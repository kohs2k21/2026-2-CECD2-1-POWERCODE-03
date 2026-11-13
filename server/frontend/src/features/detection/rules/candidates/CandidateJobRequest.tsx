import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { paths } from "@/app/routes";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { stampText } from "@/features/operations/format";
import type { CandidateJob } from "./candidateApi";
import { requestCandidateJob, type CandidateSnapshot } from "./candidateJobApi";
import {
  JOB_REQUEST, JOB_REQUEST_DESCRIPTION, JOB_REQUEST_RUNNING, JOB_REQUEST_SUBMIT, SNAPSHOTS_EMPTY, SNAPSHOTS_EMPTY_LINK, SNAPSHOT_CURRENT, SNAPSHOT_FIELD,
} from "./candidateLabels";
import { candidateJobRunning, candidateListQueryName, useCandidateSnapshots } from "./useCandidates";

/** 선택지 이름: 학습 기간 + 만든 시각(+ 지금 보이는 결과의 데이터셋이면 표시). */
export const snapshotText = (snapshot: CandidateSnapshot, currentId: string | null) =>
  `${stampText(snapshot.from)} ~ ${stampText(snapshot.until)} · 생성 ${stampText(snapshot.createdAt)}${snapshot.id === currentId ? ` · ${SNAPSHOT_CURRENT}` : ""}`;

/** 새 학습 화면(주소 step=1이면 그 영역으로 스크롤, models TrainArea). */
const NEW_TRAINING = `${paths.detectionModels}?step=1`;

/**
 * 확인창: 저장 데이터셋 하나를 골라 접수한다. 기본 선택은 가장 최근 데이터셋. 키는 데이터셋마다 확인창이 열린 동안 같아서
 * 응답을 놓친 재전송은 서버가 같은 작업을 돌려준다(rule-candidates-ui.md 6절 멱등). 접수되면 목록을 다시 읽고 닫는다 —
 * 진행은 목록 머리의 최근 작업 상태(5초 조회)로 보이고, 끝나면 그 결과가 목록이 된다.
 */
const RequestDialog = ({ currentSnapshotId, onClose }: { currentSnapshotId: string | null; onClose: () => void }) => {
  const queryClient = useQueryClient();
  const snapshots = useCandidateSnapshots(true);
  const [chosen, setChosen] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const keys = useRef(new Map<string, string>());
  const items = snapshots.data ?? [];
  const selected = items.find((item) => item.id === chosen) ?? items[0] ?? null;
  const submit = async () => {
    if (!selected) return;
    const key = keys.current.get(selected.id) ?? crypto.randomUUID();
    keys.current.set(selected.id, key);
    setRequesting(true);
    setRequestError(null);
    try {
      await requestCandidateJob(selected.id, key);
      await queryClient.invalidateQueries({ queryKey: [candidateListQueryName] });
      onClose();
    } catch (error) {
      setRequestError(errorCode(error));
    } finally {
      setRequesting(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !requesting) onClose(); }}>
      <DialogContent className="sm:max-w-lg" data-candidate-request>
        <DialogHeader>
          <DialogTitle>{JOB_REQUEST}</DialogTitle>
          <DialogDescription>{JOB_REQUEST_DESCRIPTION}</DialogDescription>
        </DialogHeader>
        {snapshots.isPending && <LoadingState>{SNAPSHOT_FIELD} 조회 중…</LoadingState>}
        {snapshots.isError && <ErrorState title={`${SNAPSHOT_FIELD} 조회 실패 · ${errorCode(snapshots.error)}`} onRetry={() => void snapshots.refetch()} retryLabel="다시 조회" />}
        {snapshots.data && !items.length && (
          <div className="grid gap-2" data-snapshots-empty>
            <EmptyState>{SNAPSHOTS_EMPTY}</EmptyState>
            <div><Button asChild size="sm" variant="outline"><Link to={NEW_TRAINING}>{SNAPSHOTS_EMPTY_LINK}</Link></Button></div>
          </div>
        )}
        {selected && (
          <div className="grid gap-1.5">
            <Label htmlFor="candidate-snapshot">{SNAPSHOT_FIELD}</Label>
            <NativeSelect id="candidate-snapshot" value={selected.id} disabled={requesting} onChange={(event) => setChosen(event.target.value)}>
              {items.map((item) => <NativeSelectOption key={item.id} value={item.id}>{snapshotText(item, currentSnapshotId)}</NativeSelectOption>)}
            </NativeSelect>
          </div>
        )}
        {requestError && <Alert variant="destructive" data-candidate-request-error><AlertDescription>{JOB_REQUEST} 실패 · {requestError}</AlertDescription></Alert>}
        <DialogFooter>
          <Button variant="outline" disabled={requesting} onClick={onClose}>취소</Button>
          <Button disabled={!selected || requesting} onClick={() => void submit()}>{JOB_REQUEST_SUBMIT}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

/** 목록 머리 명령: [후보 작업 요청]. 후보 작업이 대기·실행 중이면 끈다(같은 계산을 겹쳐 쌓지 않음 — 서버는 막지 않는다). */
export const CandidateJobRequest = ({ latestJob, currentSnapshotId }: { latestJob: CandidateJob | null | undefined; currentSnapshotId: string | null }) => {
  const [open, setOpen] = useState(false);
  const running = candidateJobRunning(latestJob?.status);
  return (
    <>
      {running && <span data-candidate-running className="text-sm text-muted-foreground">{JOB_REQUEST_RUNNING}</span>}
      <Button size="sm" disabled={running} onClick={() => setOpen(true)}>{JOB_REQUEST}</Button>
      {open && <RequestDialog currentSnapshotId={currentSnapshotId} onClose={() => setOpen(false)} />}
    </>
  );
};
