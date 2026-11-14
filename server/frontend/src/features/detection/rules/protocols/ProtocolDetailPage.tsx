import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { IconPlus } from "@tabler/icons-react";
import { DescriptionList } from "@/components/layout/DescriptionList";
import { ScreenTitle } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DialogFooter } from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { paths } from "@/app/routes";
import { stampText } from "@/features/operations/format";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "../RuleDialogs";
import { PROTOCOL_ITEM_TERM, PROTOCOL_TERM, errorCode, requestFromState, severities, severityLabels } from "../ruleDraft";
import type { DraftPath, ProtocolDrafts, ProtocolPath, ProtocolRuleRequest, RuleSetRequest, RuleSetState, Severity } from "../ruleSetApi";
import { useRuleSet } from "../useRuleSet";
import { useRuleSetSave } from "../useRuleSetSave";
import { ChannelChain } from "./ChannelChain";
import { ProtocolExplanationCard } from "./ProtocolExplanationCard";
import { EXPLAIN_PATHS_BUTTON, observedPathsRequest } from "./protocolExplanation";
import {
  APPLIES_FROM, CHANNEL_INPUT_HELP, DRAFT_BUTTON, DRAFT_LEAD, DRAFT_NOT_OBSERVED, FIRST_ENTRY_UNREGISTERED, PATHS_EMPTY_ISSUE, PATHS_MAX, STEPS_MAX,
  UNREGISTERED_RULE_ID, count, draftUnavailableText, parseChannelLines, pathIndex, pathReasonText, protocolEntry, ratioText, unregisteredRule,
} from "./protocolLabels";
import { useProtocolDrafts } from "./useProtocolDrafts";

/** 편집 초안. 경로 source는 새로 더한 경로에만(초안에서 고른 경로 = draft). 기존 경로는 서버가 저장값을 유지한다. */
type Edit = { revision: number; paths: ProtocolPath[]; enabled: boolean; severity: Severity; turnOnUnregistered: boolean };
const editOf = (state: RuleSetState, interfaceId: string): Edit => {
  const entry = protocolEntry(state, interfaceId);
  const first = !(state.protocolRules ?? []).length;
  return {
    revision: state.revision,
    paths: entry ? entry.paths.map(({ channels }) => ({ channels: [...channels] })) : [],
    enabled: entry?.enabled ?? true,
    // 항목 기본 위험도 = 위험(protocol-rules.md 4절 severity 기본 critical).
    severity: entry?.severity ?? "critical",
    // 첫 프로토콜 항목 저장 때 미등록 인터페이스 규칙을 켬으로 미리 선택(rule-set.md v3.5, 사용자가 끌 수 있음).
    turnOnUnregistered: first && !unregisteredRule(state)?.enabled,
  };
};
const pathSet = (list: readonly ProtocolPath[]) => new Set(list.map((path) => JSON.stringify(path.channels)));
const samePaths = (left: readonly ProtocolPath[], right: readonly ProtocolPath[]) => {
  const [a, b] = [pathSet(left), pathSet(right)];
  return a.size === b.size && [...a].every((key) => b.has(key));
};

/** 경로 추가: 채널 한 줄에 하나(값 그대로). 없음 값 채널·중복 경로·상한은 여기서 먼저 막고, 그 밖은 서버 검증(422)이 정한다. */
export const AddPathBody = ({ existing, onAdd, onCancel }: { existing: string[][]; onAdd: (channels: string[]) => void; onCancel: () => void }) => {
  const [text, setText] = useState("");
  const channels = parseChannelLines(text);
  const absent = channels.some((channel) => /^[ \t\r\n]*(\\N)?[ \t\r\n]*$/.test(channel));
  const duplicate = channels.length > 0 && pathIndex(existing, channels) >= 0;
  const tooLong = channels.length > STEPS_MAX;
  const problem = absent ? "빈 값·\\N인 채널은 저장할 수 없습니다." : duplicate ? "이미 있는 허용 경로입니다." : tooLong ? `경로 하나는 ${STEPS_MAX}단계까지입니다.` : null;
  return (
    <>
      <div className="grid gap-2">
        <Label htmlFor="protocol-path-channels">채널 ID(한 줄에 하나)</Label>
        <Textarea id="protocol-path-channels" rows={6} className="font-mono" value={text} onChange={(event) => setText(event.target.value)} />
        <p className="text-sm text-muted-foreground">{CHANNEL_INPUT_HELP}</p>
        {channels.length > 0 && <ChannelChain channels={channels} />}
        {problem && <p role="alert" className="text-sm text-destructive-text">{problem}</p>}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>돌아가기</Button>
        <Button disabled={!channels.length || problem !== null} onClick={() => onAdd(channels)}>추가</Button>
      </DialogFooter>
    </>
  );
};
const AddPathDialog = (props: Parameters<typeof AddPathBody>[0]) => (
  <Modal isOpen onOpenChange={(open) => { if (!open) props.onCancel(); }} title="허용 경로 추가">
    <AddPathBody {...props} />
  </Modal>
);

/** 관측 경로 초안: Trainer 집계 경로(거래 수·비율·드묾·갈래)를 보여 주고 사용자가 고른 경로만 허용 경로에 더한다(자동 채택 없음). */
const DraftPanel = ({ drafts, current, onAdd }: {
  drafts: Extract<ProtocolDrafts, { state: "available" }>; current: string[][]; onAdd: (paths: DraftPath[]) => void;
}) => {
  const [picked, setPicked] = useState<string[]>([]);
  const item = drafts.interface ?? null;
  if (!item) return <EmptyState>{DRAFT_NOT_OBSERVED}</EmptyState>;
  const fresh = item.paths.filter((path) => pathIndex(current, path.channels) < 0);
  const room = PATHS_MAX - current.length;
  const chosen = fresh.filter((path) => picked.includes(path.pathKey));
  const toggle = (key: string, on: boolean) => setPicked((keys) => (on ? [...keys.filter((value) => value !== key), key] : keys.filter((value) => value !== key)));
  const reasons = Object.entries(item.excludedReasons);
  return (
    <div className="grid gap-3" data-draft-panel>
      <p className="text-sm text-muted-foreground">{DRAFT_LEAD}</p>
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span>후보 작업 완료 {stampText(drafts.job.finishedAt)}</span>
        <span>기준 기간 {stampText(drafts.period.from)} ~ {stampText(drafts.period.until)}</span>
        <span>경로를 만든 거래 {count(item.pathTransactions)}건 / 이 인터페이스 거래 {count(item.transactions)}건</span>
        {item.branchingTransactions > 0 && <span>갈라진 거래 {count(item.branchingTransactions)}건</span>}
        {item.status === "insufficient_evidence" && (
          <Badge variant="warning" data-draft-insufficient>근거 부족 · 경로를 만든 거래 {drafts.parameters.minTransactions}건 미만</Badge>
        )}
      </div>
      {reasons.length > 0 && (
        <p className="text-sm text-muted-foreground" data-draft-excluded>
          경로를 만들지 못한 거래 · {reasons.map(([code, value]) => `${pathReasonText(code)} ${count(value)}건`).join(" · ")}
        </p>
      )}
      <Table aria-label="관측 경로 초안 표">
        <TableHeader>
          <TableRow>{["선택", "경로", "거래", "비율", "표시"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
        </TableHeader>
        <TableBody>
          {item.paths.map((path) => {
            const allowed = pathIndex(current, path.channels) >= 0;
            return (
              <TableRow key={path.pathKey} data-draft-path={path.pathKey} className={cn(allowed && "text-muted-foreground")}>
                <TableCell>
                  {allowed
                    ? <Badge variant="secondary">허용됨</Badge>
                    : <Checkbox aria-label={`${path.channels.join(" → ")} 선택`} checked={picked.includes(path.pathKey)} onCheckedChange={(value) => toggle(path.pathKey, value === true)} />}
                </TableCell>
                <TableCell className="whitespace-normal"><ChannelChain channels={path.channels} /></TableCell>
                <TableCell>{count(path.transactions)}건</TableCell>
                <TableCell>{ratioText(path.ratio)}</TableCell>
                <TableCell>
                  <span className="flex flex-wrap gap-1">
                    {path.rare && <Badge variant="warning" data-rare>드묾 · {drafts.parameters.rarePercent}% 미만</Badge>}
                    {path.fromBranching > 0 && <Badge variant="outline">갈라진 거래 {count(path.fromBranching)}건</Badge>}
                  </span>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {item.omittedPaths.paths > 0 && (
        <p className="text-sm text-muted-foreground" data-draft-omitted>
          표시하지 않은 경로 {count(item.omittedPaths.paths)}개 · 거래 {count(item.omittedPaths.transactions)}건(거래 수가 적은 경로부터 {drafts.parameters.maxOutputPaths}개 밖)
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Button size="sm" variant="outline" disabled={!fresh.length} onClick={() => setPicked(fresh.map((path) => path.pathKey))}>모두 선택</Button>
        <Button size="sm" disabled={!chosen.length || chosen.length > room} onClick={() => { onAdd(chosen); setPicked([]); }}>선택한 경로 {chosen.length}개 추가</Button>
        {chosen.length > room && <span className="text-warning-text">허용 경로는 {PATHS_MAX}개까지입니다(남은 자리 {room}개).</span>}
      </div>
    </div>
  );
};

/** 알림 [정상 프로토콜에 추가]가 넘긴 경로(protocol-settings-ui.md 5절). 이미 허용된 경로면 더하지 않는다. */
const withAlertPath = (edit: Edit, alertPath: AlertPath | undefined): Edit => {
  if (!alertPath || !Array.isArray(alertPath.channels) || !alertPath.channels.length || pathIndex(edit.paths.map((path) => path.channels), alertPath.channels) >= 0) return edit;
  return { ...edit, paths: [...edit.paths, { channels: [...alertPath.channels], source: { kind: "alert", decisionId: alertPath.decisionId } }] };
};
type AlertPath = { channels: string[]; decisionId: string };

const ProtocolEditor = ({ state, interfaceId, reload }: { state: RuleSetState; interfaceId: string; reload: () => Promise<RuleSetState | undefined> }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const alertPath = (location.state as { alertPath?: AlertPath } | null)?.alertPath;
  const entry = protocolEntry(state, interfaceId);
  const unregistered = unregisteredRule(state);
  const first = !(state.protocolRules ?? []).length;
  const [edit, setEdit] = useState<Edit>(() => withAlertPath(editOf(state, interfaceId), alertPath));
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [draftsRequested, setDraftsRequested] = useState(false);
  const drafts = useProtocolDrafts(interfaceId, draftsRequested);
  const available = drafts.data?.state === "available" ? drafts.data : null;
  // 항목 삭제 저장이 끝나면 목록으로 돌아간다(확인창 명령이 연 시점의 콜백을 쓰므로 ref로 넘긴다).
  const leaveAfterSave = useRef(false);
  const save = useRuleSetSave({ state, reload, onSaved: () => { if (leaveAfterSave.current) navigate(paths.detectionRuleProtocols); } });
  const basePaths = entry?.paths ?? [];
  const dirty = !entry || !samePaths(edit.paths, basePaths) || edit.enabled !== entry.enabled || edit.severity !== entry.severity;
  const issues = [...(edit.paths.length ? [] : [PATHS_EMPTY_ISSUE]), ...(edit.paths.length > PATHS_MAX ? [`허용 경로는 ${PATHS_MAX}개까지입니다`] : [])];
  const channelLists = edit.paths.map((path) => path.channels);
  // 경로 설명(protocol-explanation-job.md 1절 관측 경로 대상): 초안을 읽었고 그 인터페이스가 초안에 있거나 현재 운영 버전에 등록돼 있을 때만.
  // jobId = 초안을 만든 후보 작업, ruleSetRevision = 현재 운영 버전(저장된 허용 경로와 비교 — 편집 중인 값은 저장 전이라 쓰지 않음).
  const explainRequest = available?.sourceId && (available.interface || entry)
    ? observedPathsRequest(available.sourceId, available.job.id, interfaceId, state.revision) : null;

  // 저장·다른 화면 저장으로 운영 버전이 바뀌면 편집 중이 아닐 때만 새 상태로 맞춘다.
  useEffect(() => {
    if (state.revision === edit.revision) return;
    if (!entry || samePaths(edit.paths, basePaths) || !dirty) setEdit(editOf(state, interfaceId));
  }, [state.revision]); // eslint-disable-line react-hooks/exhaustive-deps

  const itemRequest = (): ProtocolRuleRequest => ({
    id: entry?.id ?? null,
    ...(entry ? {} : { clientRef: "protocol-new" }),
    interfaceId,
    paths: edit.paths.map(({ channels, source }) => ({ channels, ...(source === undefined ? {} : { source }) })),
    severity: edit.severity,
    enabled: edit.enabled,
    // 새 항목 출처: 초안을 읽었으면 그 작업(protocol-rules.md 4절 source, hash 입력 아님).
    ...(!entry && available ? { source: { jobId: available.job.id, snapshotId: available.snapshotId, period: available.period } } : {}),
  });
  const build = (without: boolean) => (confirm: boolean): RuleSetRequest => {
    const base = requestFromState(state, confirm);
    const others = base.protocolRules.filter((rule) => rule.id !== entry?.id);
    return {
      ...base,
      hardRules: !without && first && edit.turnOnUnregistered ? base.hardRules.map((rule) => (rule.id === UNREGISTERED_RULE_ID ? { ...rule, enabled: true } : rule)) : base.hardRules,
      protocolRules: without ? others : entry ? base.protocolRules.map((rule) => (rule.id === entry.id ? itemRequest() : rule)) : [...others, itemRequest()],
    };
  };
  const openSave = () => { leaveAfterSave.current = false; void save.open(build(false), { title: `${PROTOCOL_ITEM_TERM} 저장 · ${interfaceId}`, actionText: "저장하고 적용" }); };
  const addDraftPaths = (picked: DraftPath[]) => setEdit((current) => ({
    ...current, paths: [...current.paths, ...picked.map((path) => ({ channels: path.channels, source: { kind: "draft", jobId: available?.job.id ?? null } }))],
  }));

  return (
    <>
      {save.banner}
      <Card aria-label="현재 운영 룰">
        <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span>현재 운영 <strong className="font-semibold">버전 {state.revision}</strong></span>
          <span>{entry ? "등록됨" : <Badge variant="warning">새 항목</Badge>}</span>
          <span className="ml-auto flex gap-2">
            {entry && <Button size="sm" variant="outline" disabled={save.busy} onClick={() => setRemoving(true)}>항목 삭제</Button>}
            <Button size="sm" disabled={!dirty || issues.length > 0 || save.busy} onClick={openSave}>저장</Button>
          </span>
        </CardContent>
      </Card>
      {alertPath && pathIndex(basePaths.map((path) => path.channels), alertPath.channels) < 0 && pathIndex(channelLists, alertPath.channels) >= 0 && (
        <p data-alert-path className="text-sm">알림에서 가져온 경로를 허용 경로에 더했습니다. 저장하면 정상 프로토콜에 추가됩니다(그 뒤 판정부터 적용).</p>
      )}
      {issues.length > 0 && (
        <ul className="grid gap-1 text-sm text-warning-text">{issues.map((issue) => <li key={issue}>확인 필요 · {issue}</li>)}</ul>
      )}
      <fieldset disabled={save.busy} className="grid min-w-0 gap-6">
        <SectionCard title={<span className="font-mono whitespace-pre">{interfaceId}</span>} description={APPLIES_FROM}>
          <DescriptionList
            items={[
              { label: "인터페이스", value: <span className="font-mono whitespace-pre">{interfaceId}</span> },
              {
                label: "켜기",
                value: (
                  <label className="flex items-center gap-2 text-sm">
                    <Switch checked={edit.enabled} aria-label={`${interfaceId} ${PROTOCOL_ITEM_TERM} 켜기`} onCheckedChange={(enabled) => setEdit((current) => ({ ...current, enabled }))} />
                    {edit.enabled ? "켜짐" : "꺼짐 · 이 인터페이스는 검사하지 않음"}
                  </label>
                ),
              },
              {
                label: "위험도",
                value: (
                  <span className="block w-24">
                    <NativeSelect size="sm" aria-label={`${interfaceId} 위험도`} value={edit.severity} onChange={(event) => setEdit((current) => ({ ...current, severity: event.target.value as Severity }))}>
                      {severities.map((item) => <NativeSelectOption key={item} value={item}>{severityLabels[item]}</NativeSelectOption>)}
                    </NativeSelect>
                  </span>
                ),
              },
              ...(first && unregistered && !unregistered.enabled ? [{
                label: unregistered.displayName,
                value: (
                  <span className="flex items-start gap-2 text-sm">
                    <Checkbox id="protocol-first-unregistered" checked={edit.turnOnUnregistered} onCheckedChange={(value) => setEdit((current) => ({ ...current, turnOnUnregistered: value === true }))} />
                    <Label htmlFor="protocol-first-unregistered">{FIRST_ENTRY_UNREGISTERED}</Label>
                  </span>
                ),
              }] : []),
            ]}
          />
        </SectionCard>
        <SectionCard
          title={`허용 경로 ${edit.paths.length}/${PATHS_MAX}`}
          aria-label="허용 경로"
          action={
            <Button size="sm" variant="outline" disabled={edit.paths.length >= PATHS_MAX} onClick={() => setAdding(true)}>
              <IconPlus aria-hidden="true" />
              경로 추가
            </Button>
          }
        >
          {edit.paths.length === 0 ? (
            <EmptyState>허용 경로: 표시할 항목이 없습니다.</EmptyState>
          ) : (
            <Table aria-label="허용 경로 표">
              <TableHeader>
                <TableRow>{["경로", "단계", "삭제"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
              </TableHeader>
              <TableBody>
                {edit.paths.map((path, index) => {
                  const added = pathIndex(basePaths.map((item) => item.channels), path.channels) < 0;
                  return (
                    <TableRow key={JSON.stringify(path.channels)} data-allowed-path={path.channels.join("\u0001")} className={cn(added && "bg-warning-soft")}>
                      <TableCell className="whitespace-normal">
                        <span className="flex flex-wrap items-center gap-2">
                          <ChannelChain channels={path.channels} />
                          {added && <Badge variant="info">추가</Badge>}
                        </span>
                      </TableCell>
                      <TableCell>{path.channels.length}</TableCell>
                      <TableCell>
                        <Button size="sm" variant="outline" aria-label={`${path.channels.join(" → ")} 삭제`}
                          onClick={() => setEdit((current) => ({ ...current, paths: current.paths.filter((_, position) => position !== index) }))}>삭제</Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
          {entry && basePaths.filter((path) => pathIndex(channelLists, path.channels) < 0).map((path) => (
            <p key={JSON.stringify(path.channels)} data-removed-path className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground line-through">
              <ChannelChain channels={path.channels} /> 저장하면 삭제
            </p>
          ))}
        </SectionCard>
        <SectionCard
          title="관측 경로 초안"
          aria-label="관측 경로 초안"
          action={<Button size="sm" variant="outline" disabled={drafts.isFetching} onClick={() => (draftsRequested ? void drafts.refetch() : setDraftsRequested(true))}>{DRAFT_BUTTON}</Button>}
        >
          {!draftsRequested && <p className="text-sm text-muted-foreground">{DRAFT_LEAD}</p>}
          {draftsRequested && drafts.isPending && <LoadingState>관측 경로 초안 조회 중…</LoadingState>}
          {drafts.isError && <ErrorState title={`관측 경로 초안 조회 실패 · ${errorCode(drafts.error)}`} onRetry={() => void drafts.refetch()} retryLabel="다시 조회" />}
          {drafts.data?.state === "protocol_drafts_unavailable" && <EmptyState>{draftUnavailableText(drafts.data)}</EmptyState>}
          {available && <DraftPanel drafts={available} current={channelLists} onAdd={addDraftPaths} />}
        </SectionCard>
        {explainRequest && (
          <ProtocolExplanationCard key={JSON.stringify(explainRequest)} request={explainRequest} itemLabel="경로 설명" buttonLabel={EXPLAIN_PATHS_BUTTON} />
        )}
      </fieldset>
      {adding && (
        <AddPathDialog existing={channelLists} onCancel={() => setAdding(false)}
          onAdd={(channels) => { setEdit((current) => ({ ...current, paths: [...current.paths, { channels }] })); setAdding(false); }} />
      )}
      {removing && entry && (
        <ConfirmDialog
          title={`${PROTOCOL_ITEM_TERM} 삭제`} destructive actionText="다음"
          message={`${interfaceId} 항목(허용 경로 ${entry.paths.length}개)을 삭제합니다. 다음 확인창에서 저장하면 운영에 반영됩니다.`}
          onCancel={() => setRemoving(false)}
          onConfirm={() => {
            setRemoving(false);
            leaveAfterSave.current = true;
            void save.open(build(true), { title: `${PROTOCOL_ITEM_TERM} 삭제 · ${interfaceId}`, actionText: "삭제하고 적용" });
          }}
        />
      )}
      {save.dialog}
    </>
  );
};

/** 룰 설정 › 프로토콜 설정 › 인터페이스 상세(contracts/protocol-settings-ui.md 3절). 상단 뒤로가기는 DetectionLayout(넘겨받은 목록 주소). */
export const ProtocolDetailPage = () => {
  const { interfaceId = "" } = useParams();
  // 같은 상세에 있다가 알림에서 다시 열어도(주소 같음, 넘긴 경로 다름) 초안을 새로 만든다 — 이동마다 다른 location.key.
  const location = useLocation();
  const rules = useRuleSet();
  const reload = async () => {
    const result = await rules.refetch();
    return result.isError ? undefined : result.data;
  };
  return (
    <div className="grid min-w-0 gap-6">
      <ScreenTitle>{PROTOCOL_TERM} · {interfaceId}</ScreenTitle>
      {rules.isPending && <LoadingState>현재 운영 룰 조회 중…</LoadingState>}
      {rules.isError && <ErrorState title={`현재 운영 룰 조회 실패 · ${errorCode(rules.error)}`} onRetry={() => void rules.refetch()} retryLabel="다시 조회" />}
      {rules.data && <ProtocolEditor key={`${interfaceId}:${location.key}`} state={rules.data} interfaceId={interfaceId} reload={reload} />}
    </div>
  );
};
