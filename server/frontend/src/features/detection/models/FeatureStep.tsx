import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { errorCode } from "@/features/detection/rules/ruleDraft";
import { accountQueryKey, useAccountQuery } from "@/lib/query";
import { useAuthSession } from "@/services/auth/AuthSessionProvider";
import { cn } from "@/lib/utils";
import { readFeatureFields, readFeatureSets, registerFeatureSet, type RegisteredFeatureSet } from "./modelsApi";

const SETS_QUERY = "train-feature-sets";
export const useFeatureSets = (sourceId: string) =>
  useAccountQuery({ name: SETS_QUERY, params: [sourceId], adminOnly: true, enabled: Boolean(sourceId), read: (signal) => readFeatureSets(sourceId, signal) });
const useFeatureFields = () => useAccountQuery({ name: "train-feature-fields", adminOnly: true, staleTime: Infinity, read: readFeatureFields });

type Registration = { status: "idle" } | { status: "pending" } | { status: "done"; result: RegisteredFeatureSet } | { status: "failed"; code: string };

/**
 * 변수 선택: 필드 목록(`GET /api/jobs/feature-sets/fields`)에서 고르고 `POST /api/jobs/feature-sets`로 세트를 받는다.
 * 같은 조합은 서버가 기존 세트를 돌려준다(trainer-service.md 입력 변수 세트 등록 규칙). 받은 세트를 목록에서 다시 읽은 뒤 선택한다.
 */
const FieldPicker = ({ sourceId, onSelect }: { sourceId: string; onSelect: (id: string) => void }) => {
  const fields = useFeatureFields();
  const queryClient = useQueryClient();
  const { user } = useAuthSession();
  const [chosen, setChosen] = useState<string[]>([]);
  const [registration, setRegistration] = useState<Registration>({ status: "idle" });
  if (fields.isPending) return <LoadingState>입력 변수 조회 중…</LoadingState>;
  if (fields.isError) return <ErrorState title={`입력 변수 조회 실패 · ${errorCode(fields.error)}`} onRetry={() => void fields.refetch()} retryLabel="다시 조회" />;
  const items = fields.data.fields;
  const toggle = (key: string, checked: boolean) => setChosen((current) => (checked ? [...current, key] : current.filter((item) => item !== key)));
  const register = async () => {
    setRegistration({ status: "pending" });
    try {
      // 요청은 목록 순서로 보낸다(서버도 목록 순서로 정렬한다).
      const result = await registerFeatureSet(items.map((item) => item.key).filter((key) => chosen.includes(key)));
      await queryClient.invalidateQueries({ queryKey: accountQueryKey(SETS_QUERY, user?.id, sourceId) });
      setRegistration({ status: "done", result });
      onSelect(result.id);
    } catch (error) {
      setRegistration({ status: "failed", code: errorCode(error) });
    }
  };
  return (
    <div className="grid gap-2">
      <h3 className="text-sm font-medium">입력 변수 선택</h3>
      <Table aria-label="입력 변수 선택">
        <TableHeader>
          <TableRow>{["선택", "변수명", "키", "단위"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
        </TableHeader>
        <TableBody>
          {items.map((field) => (
            <TableRow key={field.key}>
              <TableCell>
                <Checkbox checked={chosen.includes(field.key)} onCheckedChange={(checked) => toggle(field.key, checked === true)} aria-label={`${field.displayName} 선택`} />
              </TableCell>
              <TableCell>{field.displayName}</TableCell>
              <TableCell className="font-mono text-xs">{field.key}</TableCell>
              <TableCell>{field.unit}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {/* 사용자 승인 문구(11/13). 처리 시간 필드가 목록에 있을 때만 보인다(결측 행 학습 제외 규칙, trainer-service.md 등록 규칙). */}
      {items.some((field) => field.key === "elapsed_ms") && (
        <p className="text-xs text-muted-foreground" data-feature-note="elapsed_ms">처리 시간: 종료 시각(END_TIME)이 없는 행은 학습에서 제외됩니다.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm" data-feature-summary>선택 변수 {chosen.length}개</span>
        <Button size="sm" variant="outline" onClick={() => setChosen(items.map((item) => item.key))}>전체 선택</Button>
        <Button size="sm" variant="outline" disabled={!chosen.length} onClick={() => setChosen([])}>전체 해제</Button>
        <Button size="sm" disabled={!chosen.length || chosen.length > fields.data.maxFields || registration.status === "pending"} onClick={() => void register()}>
          선택한 변수로 세트 등록
        </Button>
      </div>
      {registration.status === "done" && (
        <p role="status" className="text-sm" data-feature-registration={registration.result.reused ? "reused" : "created"}>
          {registration.result.reused ? "같은 조합의 기존 세트" : "새 세트"} <span className="font-mono text-xs">{registration.result.id.slice(0, 8)}</span> 선택
        </p>
      )}
      {registration.status === "failed" && <Alert variant="destructive" data-feature-registration="failed"><AlertDescription>세트 등록 실패 · {registration.code}</AlertDescription></Alert>}
    </div>
  );
};

/**
 * 2단계 입력 변수: 학습 요청은 `featureSetId` 하나를 받는다. 필드를 골라 세트를 등록하거나, 등록된 세트(GET /api/jobs/feature-sets)에서 고른다.
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
      <FieldPicker sourceId={sourceId} onSelect={onSelect} />
      <h3 className="text-sm font-medium">입력 변수 세트</h3>
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
