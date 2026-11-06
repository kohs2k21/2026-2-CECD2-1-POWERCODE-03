import { DescriptionList } from "@/components/layout/DescriptionList";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { useAccountQuery } from "@/lib/query";
import { cn } from "@/lib/utils";
import { readFeatureSets } from "./modelsApi";

export const useFeatureSets = (sourceId: string) =>
  useAccountQuery({ name: "train-feature-sets", params: [sourceId], adminOnly: true, enabled: Boolean(sourceId), read: (signal) => readFeatureSets(sourceId, signal) });

/**
 * 2단계 입력 변수: 학습 요청은 `featureSetId` 하나를 받으므로 등록된 입력 변수 세트에서 고른다(GET /api/jobs/feature-sets).
 * `supported`·`reason`은 서버가 정의를 검사한 값이고 그대로 표시한다.
 */
export const FeatureStep = ({ sourceId, selected, onSelect, catalogVersion, periodText }: {
  sourceId: string;
  selected: string | null;
  onSelect: (id: string) => void;
  catalogVersion: string;
  periodText: string;
}) => {
  const sets = useFeatureSets(sourceId);
  return (
    <div className="grid gap-4">
      <DescriptionList items={[{ label: "기간", value: periodText }, { label: "카탈로그 버전", value: catalogVersion }, { label: "분석 단위", value: "PROCESS · 완료 관측" }]} />
      {sets.isPending && <LoadingState>입력 변수 조회 중…</LoadingState>}
      {sets.isError && <ErrorState title={`입력 변수 조회 실패 · ${errorCode(sets.error)}`} onRetry={() => void sets.refetch()} retryLabel="다시 조회" />}
      {sets.data?.items.length === 0 && <EmptyState>등록된 입력 변수가 없습니다.</EmptyState>}
      {Boolean(sets.data?.items.length) && (
        <RadioGroup value={selected ?? ""} onValueChange={onSelect} aria-label="입력 변수 세트">
          <Table>
            <TableHeader>
              <TableRow>{["선택", "입력 변수 세트", "변수 수", "사용 가능 상태", "변수"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
            </TableHeader>
            <TableBody>
              {sets.data!.items.map((set) => (
                <TableRow key={set.id} className={cn(!set.supported && "text-muted-foreground")}>
                  <TableCell><RadioGroupItem value={set.id} disabled={!set.supported} aria-label={`${set.id.slice(0, 8)} 선택`} /></TableCell>
                  <TableCell className="font-mono text-xs">{set.id.slice(0, 8)}</TableCell>
                  <TableCell>{set.featureNames.length}개</TableCell>
                  <TableCell>{set.supported ? <Badge variant="success">사용 가능</Badge> : <Badge variant="warning">{set.reason ?? "사용 불가"}</Badge>}</TableCell>
                  <TableCell className="whitespace-normal">{set.featureNames.join(", ")}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </RadioGroup>
      )}
      {sets.data?.hasMore && <p className="text-sm text-muted-foreground">최근 등록 200개만 표시합니다.</p>}
    </div>
  );
};
