import { Fragment, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { paths } from "@/app/routes";
import { DASH } from "@/features/operations/format";
import { cn } from "@/lib/utils";
import { FormulaPanel } from "../FormulaPanel";
import { RuleAiBox, RuleAiControls, useRuleAi } from "../RuleAiPanel";
import { SOFT_TERM, errorCode, numberLabel, operatorLabels, thresholdChanged, type DraftSoftRule } from "../ruleDraft";
import type { RuleSetState, SoftRuleField } from "../ruleSetApi";
import { useSoftRuleFields } from "../useRuleSet";
import { useStoredRuleDraft } from "../useStoredRuleDraft";
import { useRowApproval, type RowApproval } from "./approvalFlow";
import type { Approval, ThresholdCandidate } from "./candidateApi";
import { approvedThreshold } from "./candidateApproval";
import {
  APPROVE, APPROVED, NEW_RULE, TAB_ANOMALY, THRESHOLD_BASIS, THRESHOLD_SECTION, limitationTexts, n, thresholdStatusLabels, thresholdText,
} from "./candidateLabels";
import { InterfacePicker } from "./InterfacePicker";
import type { CandidateView } from "./recommendations";

const COLUMNS = 5;
const changedCell = (changed: boolean) => cn(changed && "bg-warning-soft");
const GroupRow = ({ title }: { title: string }) => (
  <TableRow data-group-row>
    <TableHead colSpan={COLUMNS} scope="colgroup" className="bg-muted/50">{title}</TableHead>
  </TableRow>
);
const NoteRow = ({ children }: { children: ReactNode }) => (
  <TableRow>
    <TableCell colSpan={COLUMNS} className="whitespace-normal"><EmptyState>{children}</EmptyState></TableCell>
  </TableRow>
);

/**
 * (a) Trainer 처리 시간(이상 징후) 후보 행: 근거 칸은 Trainer 결과에 있는 값만(제안 임계값의 근거·근거 건수·END_TIME 없음).
 * 현재 룰 세트에 같은 조건(field + scope 판정 키)이 없으면 "새 규칙", 있으면 "승인됨"(rule-candidates-ui.md 7절). [승인]은 후보 상세와 같은 흐름.
 */
const ThresholdRow = ({ item, state, approval, available, detailState }: {
  item: ThresholdCandidate; state: RuleSetState | undefined; approval: RowApproval; available: Approval; detailState: CandidateView["detailState"];
}) => {
  const approved = state && item.field ? approvedThreshold(state, { field: item.field, scope: item.scope }) : null;
  const proposed = item.status === "candidate" && item.threshold !== null;
  const canApprove = available.available && proposed && approved === false && !approval.busy;
  return (
    <TableRow data-threshold-row={item.candidateId}>
      <TableCell className="whitespace-normal">
        <span className="grid">
          <Link to={paths.detectionRuleCandidate(item.candidateId)} state={detailState} className="font-mono text-sm text-link hover:underline">{item.scope.processId}</Link>
          <span className="text-xs text-muted-foreground">
            {item.scope.adapterType ?? "모든 어댑터"}
            {item.interfaceIds.length > 1 && ` · 인터페이스 ${item.interfaceIds.length}곳 공통`}
          </span>
        </span>
      </TableCell>
      <TableCell>
        <span className="font-mono">{thresholdText(item)}</span>
        {item.threshold !== null && <div className="text-xs text-muted-foreground">{THRESHOLD_BASIS}</div>}
      </TableCell>
      <TableCell className="whitespace-normal">
        근거 건수 {n(item.evidence.n)} · END_TIME 없음 {item.evidence.endTimeMissingReferenceRows ? n(item.evidence.endTimeMissingReferenceRows) : DASH}
      </TableCell>
      <TableCell>
        <span className="flex flex-wrap gap-1">
          <Badge variant={item.status === "candidate" ? "success" : "outline"}>{thresholdStatusLabels[item.status]}</Badge>
          {approved === false && proposed && <Badge variant="info" data-new-rule>{NEW_RULE}</Badge>}
          {approved && <Badge variant="secondary" data-approved>{APPROVED}</Badge>}
        </span>
      </TableCell>
      <TableCell>
        <span className="grid justify-items-start gap-1">
          <Button size="sm" disabled={!canApprove} onClick={() => approval.start(item.candidateId)}>{APPROVE}</Button>
          {approval.noteFor(item.candidateId)}
        </span>
      </TableCell>
    </TableRow>
  );
};

/** (b) 기존 이상 징후 규칙 행: 현재 초안의 조건·임계값(룰 설정 입력칸과 같은 값, 바뀐 값은 강조) + [수식 추천](규칙 행 수식 추천을 옮김). */
const SoftRow = ({ rule, base, field, open, onToggle, onApply }: {
  rule: DraftSoftRule; base: RuleSetState["softRules"][number] | undefined; field: SoftRuleField | undefined;
  open: boolean; onToggle: (show: boolean) => void; onApply: (threshold: string) => void;
}) => (
  <Fragment>
    <TableRow data-rule={rule.key} className={cn(!rule.enabled && "text-muted-foreground")}>
      <TableCell className="whitespace-normal">
        <span className="grid">
          {rule.name || field?.displayName || rule.field}
          <span className="text-xs text-muted-foreground">{field?.displayName ?? rule.field} · {rule.field}</span>
        </span>
      </TableCell>
      <TableCell data-rule-threshold className={cn("whitespace-nowrap", changedCell(!base || base.operator !== rule.operator || thresholdChanged(rule, base)))}>
        {operatorLabels[rule.operator]} {numberLabel(rule.threshold)} {field?.unit ?? ""}
      </TableCell>
      <TableCell>{DASH}</TableCell>
      <TableCell><Badge variant={rule.enabled ? "success" : "outline"}>{rule.enabled ? "켜짐" : "꺼짐"}</Badge></TableCell>
      <TableCell>
        <Button size="sm" variant="outline" onClick={() => onToggle(true)}>수식 추천</Button>
      </TableCell>
    </TableRow>
    {open && (
      <TableRow data-formula={rule.key}>
        <TableCell colSpan={COLUMNS} className="whitespace-normal">
          <FormulaPanel rule={rule} field={field} onClose={() => onToggle(false)} onApply={(threshold) => { onApply(threshold); onToggle(false); }} />
        </TableCell>
      </TableRow>
    )}
  </Fragment>
);

type HubAi = ReturnType<typeof useRuleAi>;

/**
 * 이상 징후 탭 본문: 한 표에 (a) Trainer 처리 시간 후보(새 규칙·승인)와 (b) 기존 이상 징후 규칙(임계값 재추천 = 수식 추천),
 * 머리에 AI 규칙 추천(룰 설정 이상 징후 규칙 머리에서 옮김 — LLM 꺼짐은 기존 표기 `AI 규칙 추천 · <코드>`). 룰 세트를 못 읽어도 (a)는 보인다.
 */
const AnomalyCard = ({ view, approval, state, ai, rulesError, onRulesRetry }: {
  view: CandidateView; approval: RowApproval; state: RuleSetState | undefined; ai: HubAi | null; rulesError: string | null; onRulesRetry: () => void;
}) => {
  const fields = useSoftRuleFields();
  const fieldList = fields.data ?? [];
  const stored = useStoredRuleDraft(state);
  const [open, setOpen] = useState<string[]>([]);
  const toggle = (key: string, show: boolean) => setOpen((keys) => (show ? [...keys.filter((item) => item !== key), key] : keys.filter((item) => item !== key)));
  const result = view.data?.result ?? null;
  const items = result?.thresholdCandidates ?? [];
  const draft = stored.draft;
  return (
    <SectionCard title={TAB_ANOMALY} aria-label={TAB_ANOMALY} action={ai && <RuleAiControls ai={ai} />}>
      {rulesError && <ErrorState title={`현재 운영 룰 조회 실패 · ${rulesError}`} onRetry={onRulesRetry} retryLabel="다시 조회" />}
      {fields.isError && <ErrorState title={`등록 필드 조회 실패 · ${errorCode(fields.error)}`} />}
      {ai && state && draft && (
        <RuleAiBox ai={ai} state={state} draft={draft} fields={fieldList} onApply={(updates) => stored.editSoft(updates)}
          approver={approval} available={view.data?.approval} />
      )}
      {approval.banner}
      {result && <InterfacePicker result={result} onInterface={view.onInterface} />}
      <Table aria-label={TAB_ANOMALY}>
        <TableHeader>
          <TableRow>
            {["규칙(대상 필드)", "임계값", "근거", "상태"].map((title) => <TableHead key={title}>{title}</TableHead>)}
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          <GroupRow title={THRESHOLD_SECTION} />
          {view.pending ? (
            <TableRow><TableCell colSpan={COLUMNS}><LoadingState /></TableCell></TableRow>
          ) : !result ? (
            view.data ? <NoteRow>후보 작업 결과가 없습니다.</NoteRow> : <TableRow><TableCell colSpan={COLUMNS}>{DASH}</TableCell></TableRow>
          ) : items.length === 0 ? (
            <NoteRow>이 인터페이스의 처리 시간 후보가 없습니다.</NoteRow>
          ) : (
            items.map((item) => (
              <ThresholdRow key={item.candidateId} item={item} state={state} approval={approval} available={view.data!.approval} detailState={view.detailState} />
            ))
          )}
          <GroupRow title={SOFT_TERM} />
          {!draft ? (
            <TableRow><TableCell colSpan={COLUMNS}>{rulesError ? DASH : <LoadingState>현재 운영 룰 조회 중…</LoadingState>}</TableCell></TableRow>
          ) : draft.soft.length === 0 ? (
            <NoteRow>{SOFT_TERM}: 표시할 항목이 없습니다.</NoteRow>
          ) : (
            draft.soft.map((rule) => (
              <SoftRow
                key={rule.key} rule={rule} base={draft.base.softRules.find((item) => item.id === rule.id)} field={fieldList.find((item) => item.fieldId === rule.field)}
                open={open.includes(rule.key)} onToggle={(show) => toggle(rule.key, show)}
                onApply={(threshold) => stored.editSoft([{ key: rule.key, patch: { threshold } }])}
              />
            ))
          )}
        </TableBody>
      </Table>
      {result && limitationTexts(result.thresholdLimitations).map((text) => (
        <p key={text} data-threshold-limitation className="text-sm text-muted-foreground">한계(Trainer) · {text}</p>
      ))}
      {result && result.thresholdsWithoutInterface > 0 && (
        <p data-threshold-unassigned className="text-sm text-muted-foreground">인터페이스를 알 수 없는 단계 후보 {n(result.thresholdsWithoutInterface)}건은 이 표에 없습니다.</p>
      )}
      {approval.dialogs}
    </SectionCard>
  );
};

/**
 * 룰 세트를 읽은 뒤: AI 규칙 추천 작업 상태를 붙인다(대상은 저장된 버전 — 룰 설정 초안에 미저장 변경이 있으면 `저장 후 추천`).
 * (10차) 지금 보이는 처리 시간 후보(선택한 인터페이스)도 대상이라 이상 징후 규칙이 0개여도 추천할 수 있다.
 */
const AnomalyWithRules = ({ state, ...props }: Omit<Parameters<typeof AnomalyCard>[0], "ai"> & { state: RuleSetState; reload: () => Promise<RuleSetState | undefined> }) => {
  const { reload, ...rest } = props;
  const stored = useStoredRuleDraft(state);
  const ai = useRuleAi({ state, dirty: stored.dirty, reload, candidateResult: props.view.data?.result });
  return <AnomalyCard {...rest} state={state} ai={ai} />;
};

export const AnomalyTab = ({ view }: { view: CandidateView }) => {
  const approval = useRowApproval();
  const rules = approval.rules;
  const reload = async () => {
    const result = await rules.refetch();
    return result.isError ? undefined : result.data;
  };
  const rulesError = rules.isError ? errorCode(rules.error) : null;
  const common = { view, approval, rulesError, onRulesRetry: () => void rules.refetch() };
  return rules.data ? <AnomalyWithRules {...common} state={rules.data} reload={reload} /> : <AnomalyCard {...common} state={undefined} ai={null} />;
};
