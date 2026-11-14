import { useState } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { methodName, versionLabel } from "@/features/detection/models/VersionList";
import type { CatalogMethod } from "@/features/detection/models/modelsApi";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { DASH, stampText } from "@/features/operations/format";
import type { ActiveChange, ActiveHistoryItem, ActiveModel } from "./operationsApi";
import type { useActiveHistory } from "./useOperations";
import { detectionSections } from "../detectionToc";

const ML_TERM = "머신러닝 모델";
const KIND_LABELS: Record<ActiveChange["type"], string> = { activated: "모델 교체", replaced: "모델 교체", retired: "모델 운영 제외", restored: "모델 복원" };
const label = (id: string | undefined, name: string | null | undefined) => (id ? versionLabel({ id, displayName: name ?? null }) : "없음");
export const changeText = (change: ActiveChange) =>
  change.type === "retired" ? `${label(change.configVersionId, change.displayName)} → 제외` : `${label(change.previousConfigVersionId, change.previousDisplayName)} → ${label(change.configVersionId, change.displayName)}`;
export const modelsText = (models: ActiveModel[]) => (models.length ? models.map((model) => label(model.configVersionId, model.displayName)).join(", ") : "모델 없음");

/** 모델 교체 이력(활성 집합 리비전 이력, append-only). 한 리비전에 변경이 여러 개면 변경마다 한 줄. */
export const ActiveHistoryCard = ({ history, methods }: { history: ReturnType<typeof useActiveHistory>; methods: CatalogMethod[] | undefined }) => {
  const { first, items } = history;
  return (
    <SectionCard id={detectionSections.operationsHistory.id} title={detectionSections.operationsHistory.label}>
      {first.isPending && <LoadingState>모델 교체 이력 조회 중…</LoadingState>}
      {first.isError && <ErrorState title={`모델 교체 이력 조회 실패 · ${errorCode(first.error)}`} onRetry={() => void first.refetch()} retryLabel="다시 조회" />}
      {first.data && items.length === 0 && <EmptyState>모델 교체 이력이 없습니다.</EmptyState>}
      {items.length > 0 && (
        <div className="max-h-80 overflow-auto" tabIndex={0} aria-label="모델 교체 이력 목록">
          <Table>
            <TableHeader><TableRow>{["버전", "교체", "모델", "작업", "추천 수락", "변경 시각", "변경자"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader>
            <TableBody>
              {items.flatMap((item) => (item.changes.length ? item.changes : [null]).map((change, index) => (
                <TableRow key={`${item.revision}:${index}`} data-revision={item.revision}>
                  <TableCell>버전 {item.revision}</TableCell>
                  <TableCell className="whitespace-normal">{change ? changeText(change) : DASH}</TableCell>
                  <TableCell>{change ? methodName(methods, change.modelKey) : DASH}</TableCell>
                  <TableCell>{change ? KIND_LABELS[change.type] : DASH}{item.restoredFromRevision !== null && index === 0 ? ` · 버전 ${item.restoredFromRevision} 상태` : ""}</TableCell>
                  <TableCell>{change?.acceptedRecommendation ? `${change.acceptedRecommendation.comparisonJobId.slice(0, 8)} 추천 ${change.acceptedRecommendation.targetVersionId.slice(0, 8)}` : DASH}</TableCell>
                  <TableCell>{stampText(item.changedAt)}</TableCell>
                  <TableCell>{item.changedBy.displayName}</TableCell>
                </TableRow>
              )))}
            </TableBody>
          </Table>
        </div>
      )}
      {history.cursor && <div><Button size="sm" variant="outline" disabled={history.loading} onClick={() => void history.loadMore()}>더 보기</Button></div>}
      {history.moreError && <ErrorState title={`모델 교체 이력 조회 실패 · ${history.moreError}`} />}
    </SectionCard>
  );
};

/** 확인 체크(함수로 펼쳐 둔다: 확인창 본문이 한 요소 트리라 시험이 포털 밖에서 찾을 수 있다). */
const approve = (id: string, text: string, checked: boolean, onChange: (value: boolean) => void) => (
  <div className="flex items-start gap-2">
    <Checkbox id={id} checked={checked} onCheckedChange={(value) => onChange(value === true)} />
    <Label htmlFor={id}>{text}</Label>
  </div>
);

/** 운영에서 제외(와이어프레임 model-remove 확인창). 확인 체크 뒤에만 보낸다. */
export const RetireDialog = ({ model, methods, last, busy, onCancel, onConfirm }: {
  model: ActiveModel; methods: CatalogMethod[] | undefined; last: boolean; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) => {
  const [checked, setChecked] = useState(false);
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>운영에서 제외</DialogTitle>
          <DialogDescription>
            운영에서 뺄 모델: {label(model.configVersionId, model.displayName)} · {methodName(methods, model.modelKey)}. 이후 판정부터 이 모델을 쓰지 않습니다.
            {last ? " 운영 모델이 0개가 되어 룰만으로 판정합니다." : ""} 실패하면 기존 모델을 유지합니다.
          </DialogDescription>
        </DialogHeader>
        {approve("retire-approve", "제외할 모델과 영향을 확인했고 제외를 승인합니다", checked, setChecked)}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>돌아가기</Button>
          <Button variant="destructive" disabled={!checked || busy} onClick={onConfirm}>운영에서 제외</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const sameSet = (left: ActiveModel[], right: ActiveModel[]) =>
  left.length === right.length && left.every((model) => right.some((item) => item.configVersionId === model.configVersionId));

/** 복원 후보: 지금과 다른 모델 집합을 가진 이전 버전(모델 없는 상태는 운영에서 제외로 만든다). 최신순, 같은 집합은 한 번만. */
export const restoreCandidates = (items: ActiveHistoryItem[], current: { revision: number; models: ActiveModel[] }) => {
  const seen: ActiveModel[][] = [];
  return items.filter((item) => {
    const models = item.snapshot.models;
    if (item.revision >= current.revision || !models.length || sameSet(models, current.models) || seen.some((set) => sameSet(set, models))) return false;
    seen.push(models);
    return true;
  });
};

/**
 * 이전 운영 모델 복원(와이어프레임 open-restore): 이전 버전의 모델 집합을 새 버전으로 다시 적용한다(model-version.md restore).
 * 지금 운영 중인데 그 버전에 없는 모델은 빠진다. 확인 체크가 그 제외 확인(confirmRetire)을 겸한다.
 */
export const RestoreDialog = ({ candidates, current, methods, busy, onCancel, onConfirm }: {
  candidates: ActiveHistoryItem[]; current: ActiveModel[]; methods: CatalogMethod[] | undefined; busy: boolean;
  onCancel: () => void; onConfirm: (item: ActiveHistoryItem) => void;
}) => {
  const [picked, setPicked] = useState(candidates[0] ? String(candidates[0].revision) : "");
  const [checked, setChecked] = useState(false);
  const item = candidates.find((candidate) => String(candidate.revision) === picked);
  // 와이어프레임: 같은 종류 운영 모델이 있으면 '현재 X 자리', 없으면 '추가 운영'. 그 버전 집합에 같은 종류가 없는 지금 운영 모델은 빠지므로
  // 이력 표기와 같이 'X → 제외'로 보인다(확인 체크가 그 제외 확인을 겸한다).
  const slot = (models: ActiveModel[]) => {
    const replaced = current.filter((model) => models.some((other) => other.modelKey === model.modelKey && other.configVersionId !== model.configVersionId));
    const removed = current.filter((model) => !models.some((other) => other.modelKey === model.modelKey));
    return `${replaced.length ? ` · 현재 ${modelsText(replaced)} 자리` : " · 추가 운영"}${removed.length ? ` · ${modelsText(removed)} → 제외` : ""}`;
  };
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>이전 운영 모델 복원</DialogTitle>
          <DialogDescription>선택한 이전 운영 모델을 다시 적용합니다. 실패하면 기존 모델을 유지합니다.</DialogDescription>
        </DialogHeader>
        {candidates.length ? (
          <RadioGroup value={picked} onValueChange={setPicked} aria-label="복원할 이전 운영 모델">
            {candidates.map((candidate) => (
              <div key={candidate.revision} className="flex items-start gap-2">
                <RadioGroupItem id={`restore-${candidate.revision}`} value={String(candidate.revision)} />
                <Label htmlFor={`restore-${candidate.revision}`} className="font-normal">
                  {modelsText(candidate.snapshot.models)} · {candidate.snapshot.models.map((model) => methodName(methods, model.modelKey)).join(", ")}{slot(candidate.snapshot.models)} · 버전 {candidate.revision} · {stampText(candidate.changedAt)}
                </Label>
              </div>
            ))}
          </RadioGroup>
        ) : (
          <p className="text-sm text-muted-foreground">복원할 이전 운영 모델이 없습니다.</p>
        )}
        {candidates.length > 0 && approve("restore-approve", "복원할 모델과 평가 근거를 검토했습니다", checked, setChecked)}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>돌아가기</Button>
          {candidates.length > 0 && <Button disabled={!checked || !item || busy} onClick={() => item && onConfirm(item)}>복원 요청</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export { ML_TERM };
