import { useDeferredValue, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import type { DetectionReturnState } from "../../DetectionLayout";
import { errorCode, severityBadge, severityLabels } from "../ruleDraft";
import {
  DICTIONARY_EMPTY, NOT_IN_DICTIONARY, RESPONSE_CODE_LIST_LEAD, RESPONSE_CODE_TERM, RULE_OFF_NOTE,
} from "./responseCodeLabels";
import { readResponseCodes, type ResponseCodeItem } from "./responseCodesApi";
import { useResponseCodes } from "./useResponseCodes";

const count = (value: number) => value.toLocaleString("ko-KR");

/** 룰 설정 › 응답 코드 목록(contracts/response-codes-ui.md 4절): 검색(코드·코드명·메시지)·유형 필터, 위험도(코드별 또는 기본값), 행 → 상세. */
export const ResponseCodeListPage = () => {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const q = useDeferredValue(search);
  const list = useResponseCodes({ q, type });
  // 다음 쪽(200개 넘을 때만): 조건이 바뀌면 버린다.
  const [more, setMore] = useState<{ items: ResponseCodeItem[]; cursor: string | null; error: string | null }>({ items: [], cursor: null, error: null });
  const [loadingMore, setLoadingMore] = useState(false);
  useEffect(() => setMore({ items: [], cursor: null, error: null }), [q, type, list.data]);
  const data = list.data;
  const cursor = more.cursor ?? data?.pagination.nextCursor ?? null;
  const hasMore = more.cursor !== null || (more.items.length === 0 && Boolean(data?.pagination.hasMore));
  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const next = await readResponseCodes({ q, type, cursor });
      setMore((current) => ({ items: [...current.items, ...next.items], cursor: next.pagination.nextCursor, error: null }));
    } catch (error) {
      setMore((current) => ({ ...current, error: errorCode(error) }));
    } finally {
      setLoadingMore(false);
    }
  };
  const items = data ? [...data.items, ...more.items] : [];
  const returnState: DetectionReturnState = { returnTo: paths.detectionRuleResponseCodes };
  const filtered = Boolean(q.trim() || type);

  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{RESPONSE_CODE_TERM}</ScreenTitle>
      <p className="text-sm text-muted-foreground">{RESPONSE_CODE_LIST_LEAD}</p>
      {list.isPending && <LoadingState>응답 코드 조회 중…</LoadingState>}
      {list.isError && <ErrorState title={`응답 코드 조회 실패 · ${errorCode(list.error)}`} onRetry={() => void list.refetch()} retryLabel="다시 조회" />}
      {data && (
        <SectionCard
          title={`${RESPONSE_CODE_TERM} 목록`}
          aria-label={`${RESPONSE_CODE_TERM} 목록`}
          description={
            <span className="flex flex-wrap gap-x-4 gap-y-1">
              <span data-response-code-head>
                현재 운영 버전 {data.ruleSet.revision} · 사전 {count(data.counts.dictionary)}개 · 코드별 위험도 {count(data.ruleSet.registeredCodes)}개 ·
                그 밖의 코드 {severityLabels[data.ruleSet.defaultSeverity]}
                {data.counts.missingFromDictionary > 0 && ` · ${NOT_IN_DICTIONARY} ${count(data.counts.missingFromDictionary)}개`}
              </span>
              {!data.ruleSet.enabled && <span data-rule-off className="text-warning-text">{RULE_OFF_NOTE}</span>}
            </span>
          }
        >
          <div className="flex flex-wrap gap-3">
            <div className="w-full max-w-sm">
              <Input type="search" aria-label="응답 코드 검색" placeholder="코드·코드명·메시지 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
            <div className="w-44">
              <NativeSelect aria-label="유형" value={type} onChange={(event) => setType(event.target.value)}>
                <NativeSelectOption value="">전체 유형</NativeSelectOption>
                {data.types.map((item) => <NativeSelectOption key={item.codeType} value={item.codeType}>{item.codeType} ({count(item.count)})</NativeSelectOption>)}
              </NativeSelect>
            </div>
          </div>
          {items.length === 0 ? (
            <EmptyState>{filtered ? "검색 결과가 없습니다." : DICTIONARY_EMPTY}</EmptyState>
          ) : (
            <Table aria-label={`${RESPONSE_CODE_TERM} 목록 표`}>
              <TableHeader>
                <TableRow>{["코드", "유형", "위험도", "메시지"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.code} data-response-code-row={item.code} className="cursor-pointer"
                    onClick={() => navigate(paths.detectionRuleResponseCode(item.code), { state: returnState })}>
                    <TableCell className="font-mono text-sm">
                      <span className="flex flex-wrap items-center gap-2">
                        <Link to={paths.detectionRuleResponseCode(item.code)} state={returnState} className="text-link hover:underline" onClick={(event) => event.stopPropagation()}>{item.code}</Link>
                        {!item.inDictionary && <Badge variant="warning" data-not-in-dictionary>{NOT_IN_DICTIONARY}</Badge>}
                      </span>
                    </TableCell>
                    <TableCell>{item.codeType ?? "—"}</TableCell>
                    <TableCell>
                      <span className="flex items-center gap-1">
                        <Badge variant={severityBadge[item.severity.level]}>{severityLabels[item.severity.level]}</Badge>
                        {item.severity.source === "default" && <span className="text-xs text-muted-foreground">기본값</span>}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-normal">{item.message ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {hasMore && (
            <div className="flex items-center gap-3 text-sm">
              <Button size="sm" variant="outline" disabled={loadingMore} onClick={() => void loadMore()}>더 보기</Button>
              {more.error && <span role="alert" className="text-warning-text">다음 목록 조회 실패 · {more.error}</span>}
            </div>
          )}
        </SectionCard>
      )}
    </div>
  );
};
