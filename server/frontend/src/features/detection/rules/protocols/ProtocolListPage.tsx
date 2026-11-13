import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { IconPlus } from "@tabler/icons-react";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DialogFooter } from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import { stampText } from "@/features/operations/format";
import type { DetectionReturnState } from "../../DetectionLayout";
import { PROTOCOL_ITEM_TERM, PROTOCOL_TERM, errorCode, requestFromState, severities, severityBadge, severityLabels } from "../ruleDraft";
import type { DraftInterfaceSummary, RuleSetState, Severity } from "../ruleSetApi";
import { useRuleSet } from "../useRuleSet";
import { useRuleSetSave } from "../useRuleSetSave";
import {
  PROTOCOL_LIST_LEAD, UNREGISTERED_WITHOUT_ENTRIES, count, draftUnavailableText, protocolFeatureOn, unregisteredRule,
} from "./protocolLabels";
import { CandidateJobRequest, LatestCandidateJob } from "../candidates/CandidateJobRequest";
import { candidateListQueryName } from "../candidates/useCandidates";
import { protocolDraftsQueryName, useProtocolDrafts } from "./useProtocolDrafts";

/** 후보 작업 접수 뒤 다시 읽을 조회: 관측 경로 초안 + 같은 작업 결과를 쓰는 규칙 추천 목록. */
const DRAFT_JOB_REFRESH = [protocolDraftsQueryName, candidateListQueryName] as const;

type Row = {
  interfaceId: string;
  entry: RuleSetState["protocolRules"][number] | null;
  draft: DraftInterfaceSummary | null;
};

/** 등록되지 않은 인터페이스 규칙(내장 하드룰). 켜기·위험도 변경은 바로 확인창 → 저장(룰 세트 새 버전). */
const UnregisteredCard = ({ state, save }: { state: RuleSetState; save: ReturnType<typeof useRuleSetSave> }) => {
  const rule = unregisteredRule(state);
  if (!rule) return null;
  const severity = rule.severity as Severity;
  const change = (patch: { enabled?: boolean; severity?: Severity }) => void save.open((confirm) => {
    const request = requestFromState(state, confirm);
    return { ...request, hardRules: request.hardRules.map((item) => (item.id === rule.id ? { ...item, ...patch } : item)) };
  }, { title: rule.displayName, actionText: "저장하고 적용" });
  return (
    <SectionCard title={rule.displayName} aria-label={rule.displayName} headingLevel={3} description={rule.conditionText}>
      <div className="flex flex-wrap items-center gap-6 text-sm" data-unregistered-rule>
        <label className="flex items-center gap-2">
          <Switch checked={rule.enabled} disabled={save.busy} aria-label={`${rule.displayName} 켜기`} onCheckedChange={(enabled) => change({ enabled })} />
          {rule.enabled ? "켜짐" : "꺼짐"}
        </label>
        <label className="flex items-center gap-2">
          위험도
          <span className="w-24">
            <NativeSelect size="sm" aria-label={`${rule.displayName} 위험도`} value={severity} disabled={save.busy} onChange={(event) => change({ severity: event.target.value as Severity })}>
              {severities.map((item) => <NativeSelectOption key={item} value={item}>{severityLabels[item]}</NativeSelectOption>)}
            </NativeSelect>
          </span>
        </label>
      </div>
      {rule.enabled && !protocolFeatureOn(state) && <p data-unregistered-idle className="text-sm text-warning-text">{UNREGISTERED_WITHOUT_ENTRIES}</p>}
    </SectionCard>
  );
};

/** 인터페이스 추가: 학습 기간에 관측되지 않은 인터페이스도 ID를 직접 입력해 항목을 만든다(상세에서 경로 입력 후 저장). */
const AddInterfaceDialog = ({ taken, onAdd, onCancel }: { taken: Set<string>; onAdd: (interfaceId: string) => void; onCancel: () => void }) => {
  const [value, setValue] = useState("");
  const exists = taken.has(value);
  return (
    <Modal isOpen onOpenChange={(open) => { if (!open) onCancel(); }} title="인터페이스 추가">
      <div className="grid gap-2">
        <Label htmlFor="protocol-interface-id">인터페이스 ID</Label>
        <Input id="protocol-interface-id" maxLength={512} value={value} onChange={(event) => setValue(event.target.value)} />
        {exists && <p role="alert" className="text-sm text-warning-text">이미 등록된 인터페이스입니다.</p>}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>돌아가기</Button>
        <Button disabled={!value.trim() || exists} onClick={() => onAdd(value)}>다음</Button>
      </DialogFooter>
    </Modal>
  );
};

/** 룰 설정 › 프로토콜 설정 목록(contracts/protocol-settings-ui.md 3절): 등록 항목 + 학습 기간에 관측된 미등록 인터페이스, 검색, 행 → 상세. */
export const ProtocolListPage = () => {
  const rules = useRuleSet();
  const drafts = useProtocolDrafts(null);
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const reload = async () => {
    const result = await rules.refetch();
    return result.isError ? undefined : result.data;
  };
  const save = useRuleSetSave({ state: rules.data, reload });
  const state = rules.data;
  const available = drafts.data?.state === "available" ? drafts.data : null;
  /** 초안 결과 작업과 다른 최근 후보 작업(대기·실행 중·실패). 초안 결과가 없으면 최근 작업 그대로. */
  const newerJob = drafts.data?.latestJob && drafts.data.latestJob.id !== drafts.data.job?.id ? drafts.data.latestJob : null;
  const rows = useMemo<Row[]>(() => {
    if (!state) return [];
    const entries = state.protocolRules ?? [];
    const draftOf = (interfaceId: string) => available?.interfaces.find((item) => item.interfaceId === interfaceId) ?? null;
    const registered = entries.map((entry) => ({ interfaceId: entry.interfaceId, entry, draft: draftOf(entry.interfaceId) }));
    const observed = (available?.interfaces ?? []).filter((item) => !entries.some((entry) => entry.interfaceId === item.interfaceId))
      .map((draft) => ({ interfaceId: draft.interfaceId, entry: null, draft }));
    return [...registered, ...observed];
  }, [state, available]);
  const needle = search.trim().toLowerCase();
  const shown = needle ? rows.filter((row) => row.interfaceId.toLowerCase().includes(needle)) : rows;
  const returnState: DetectionReturnState = { returnTo: paths.detectionRuleProtocols };

  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{PROTOCOL_TERM}</ScreenTitle>
      <p className="text-sm text-muted-foreground">{PROTOCOL_LIST_LEAD}</p>
      {rules.isPending && <LoadingState>현재 운영 룰 조회 중…</LoadingState>}
      {rules.isError && <ErrorState title={`현재 운영 룰 조회 실패 · ${errorCode(rules.error)}`} onRetry={() => void rules.refetch()} retryLabel="다시 조회" />}
      {save.banner}
      {state && (
        <>
          <UnregisteredCard state={state} save={save} />
          <SectionCard
            title={`${PROTOCOL_ITEM_TERM} 목록`}
            aria-label={`${PROTOCOL_ITEM_TERM} 목록`}
            description={
              <span className="flex flex-wrap gap-x-4 gap-y-1">
                <span>현재 운영 버전 {state.revision} · 등록 {count((state.protocolRules ?? []).length)}개 · 켜짐 {count((state.protocolRules ?? []).filter((rule) => rule.enabled).length)}개</span>
                {drafts.isPending && <span>관측 경로 초안 조회 중…</span>}
                {drafts.isError && <span data-drafts-error>관측 경로 초안 조회 실패 · {errorCode(drafts.error)}</span>}
                {drafts.data?.state === "protocol_drafts_unavailable" && <span data-drafts-unavailable>{draftUnavailableText(drafts.data)}</span>}
                {available && <span data-drafts-head>관측 경로 초안 · 후보 작업 완료 {stampText(available.job.finishedAt)} · 기준 기간 {stampText(available.period.from)} ~ {stampText(available.period.until)}</span>}
                {newerJob && <LatestCandidateJob job={newerJob} returnTo={paths.detectionRuleProtocols} />}
              </span>
            }
            action={
              <>
                {drafts.data && (
                  <CandidateJobRequest latestJob={drafts.data.latestJob} currentSnapshotId={available?.snapshotId ?? null} refreshQueryNames={DRAFT_JOB_REFRESH} />
                )}
                <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
                  <IconPlus aria-hidden="true" />
                  인터페이스 추가
                </Button>
              </>
            }
          >
            <div className="max-w-sm">
              <Input type="search" aria-label="인터페이스 검색" placeholder="인터페이스 ID 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
            {shown.length === 0 ? (
              <EmptyState>{rows.length ? "검색 결과가 없습니다." : `${PROTOCOL_ITEM_TERM}: 표시할 항목이 없습니다.`}</EmptyState>
            ) : (
              <Table aria-label={`${PROTOCOL_ITEM_TERM} 목록 표`}>
                <TableHeader>
                  <TableRow>
                    {["인터페이스", "허용 경로", "위험도", "상태", "학습 기간 관측"].map((title) => <TableHead key={title}>{title}</TableHead>)}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {shown.map((row) => (
                    <TableRow key={row.interfaceId} data-protocol-row={row.interfaceId} className="cursor-pointer" onClick={() => navigate(paths.detectionRuleProtocol(row.interfaceId), { state: returnState })}>
                      <TableCell className="font-mono text-sm whitespace-pre">
                        <Link to={paths.detectionRuleProtocol(row.interfaceId)} state={returnState} className="text-link hover:underline" onClick={(event) => event.stopPropagation()}>{row.interfaceId}</Link>
                      </TableCell>
                      <TableCell>{row.entry ? `${count(row.entry.paths.length)}개` : "—"}</TableCell>
                      <TableCell>{row.entry ? <Badge variant={severityBadge[row.entry.severity]}>{severityLabels[row.entry.severity]}</Badge> : "—"}</TableCell>
                      <TableCell>
                        {row.entry
                          ? <Badge variant={row.entry.enabled ? "success" : "outline"}>{row.entry.enabled ? "켜짐" : "꺼짐"}</Badge>
                          : <Badge variant="warning" data-unregistered>미등록</Badge>}
                      </TableCell>
                      <TableCell>
                        {row.draft
                          ? `경로 ${count(row.draft.distinctPaths)}개 · 거래 ${count(row.draft.pathTransactions)}건${row.draft.status === "insufficient_evidence" ? " · 근거 부족" : ""}`
                          : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </SectionCard>
        </>
      )}
      {adding && state && (
        <AddInterfaceDialog
          taken={new Set((state.protocolRules ?? []).map((rule) => rule.interfaceId))}
          onCancel={() => setAdding(false)}
          onAdd={(interfaceId) => navigate(paths.detectionRuleProtocol(interfaceId), { state: returnState })}
        />
      )}
      {save.dialog}
    </div>
  );
};
