import { Fragment, useState, type ReactNode } from "react";
import { IconPlus } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DASH, stampText } from "@/features/operations/format";
import { cn } from "@/lib/utils";
import { FormulaPanel } from "./FormulaPanel";
import {
  HARD_TERM, SOFT_TERM, errorCode, historySummary, nameChanged, operatorLabels, operators, severities, severityBadge, severityGroups, severityLabels, thresholdChanged,
  type DraftSoftRule, type RuleDraft,
} from "./ruleDraft";
import { readRuleSetHistory, type HardRule, type HistoryItem, type RuleSetState, type SoftRuleField } from "./ruleSetApi";
import { useRuleHistory, useRuleRevision } from "./useRuleSet";

const changedCell = (changed: boolean) => cn(changed && "bg-warning-soft");
const SwitchCell = ({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) => (
  <label className="flex items-center gap-2 text-sm">
    <Switch checked={checked} onCheckedChange={onChange} aria-label={label} />
    {checked ? "켜짐" : "꺼짐"}
  </label>
);

const SeverityCell = ({ severity }: { severity: HardRule["severity"] }) => (
  <span className="grid gap-1 text-sm">
    {severityGroups(severity).map(({ level, codes }) => (
      <span key={level} data-severity={level} className="flex items-center gap-1">
        <Badge variant={severityBadge[level]}>{severityLabels[level]}</Badge>
        {codes.length > 0 && codes.join(", ")}
      </span>
    ))}
  </span>
);

export const HardRulesCard = ({ draft, onToggle }: { draft: RuleDraft; onToggle: (id: string, enabled: boolean) => void }) => (
  <SectionCard title={HARD_TERM}>
    <Table aria-label={HARD_TERM}>
      <TableHeader>
        <TableRow><TableHead>규칙</TableHead><TableHead>조건</TableHead><TableHead>위험도</TableHead><TableHead>켜기</TableHead></TableRow>
      </TableHeader>
      <TableBody>
        {draft.base.hardRules.map((rule) => {
          const enabled = draft.hard.find((item) => item.id === rule.id)?.enabled ?? rule.enabled;
          return (
            <TableRow key={rule.id} data-rule={rule.id} className={cn(!enabled && "text-muted-foreground")}>
              <TableCell className="whitespace-normal">
                <span className="grid">
                  {rule.displayName}
                  <span className="text-xs text-muted-foreground">{rule.id}</span>
                </span>
              </TableCell>
              <TableCell className="whitespace-normal">{rule.conditionText}</TableCell>
              <TableCell><SeverityCell severity={rule.severity} /></TableCell>
              <TableCell className={changedCell(enabled !== rule.enabled)}>
                <SwitchCell checked={enabled} label={`${rule.displayName} 켜기`} onChange={(value) => onToggle(rule.id, value)} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
    {/* 켜짐·꺼짐과 무관한 상시 안내라 role="alert"을 쓰지 않는다(사용자 11/04). */}
    <p data-hard-rule-warning className="text-sm text-warning-text">명백한 이상을 놓칠 수 있습니다. 변경을 권장하지 않습니다.</p>
  </SectionCard>
);

export const SoftRulesCard = ({
  draft, fields, fieldsError, aiControls, aiBox, onEdit, onAdd, onDelete,
}: {
  draft: RuleDraft;
  fields: SoftRuleField[];
  fieldsError: string | null;
  aiControls: ReactNode;
  aiBox: ReactNode;
  onEdit: (key: string, patch: Partial<DraftSoftRule>) => void;
  onAdd: () => void;
  onDelete: (rule: DraftSoftRule) => void;
}) => {
  const [open, setOpen] = useState<string[]>([]);
  const toggle = (key: string, show: boolean) => setOpen((keys) => (show ? [...keys.filter((item) => item !== key), key] : keys.filter((item) => item !== key)));
  return (
    <SectionCard
      title={<span className="whitespace-nowrap">{SOFT_TERM}</span>}
      action={
        <>
          {aiControls}
          <Button size="sm" variant="outline" disabled={!fields.length} onClick={onAdd}>
            <IconPlus aria-hidden="true" />
            규칙 추가
          </Button>
        </>
      }
    >
      {fieldsError && <ErrorState title={`등록 필드 조회 실패 · ${fieldsError}`} />}
      {aiBox}
      {draft.soft.length === 0 ? (
        <EmptyState>{SOFT_TERM}: 표시할 항목이 없습니다.</EmptyState>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {["규칙", "대상 필드", "조건", "임계값", "단위", "위험도", "켜기", "추천·삭제"].map((title) => <TableHead key={title}>{title}</TableHead>)}
            </TableRow>
          </TableHeader>
          <TableBody>
            {draft.soft.map((rule) => {
              const field = fields.find((item) => item.fieldId === rule.field);
              const base = draft.base.softRules.find((item) => item.id === rule.id);
              const name = rule.name || field?.displayName || rule.field;
              return (
                <Fragment key={rule.key}>
                  <TableRow data-rule={rule.key} className={cn(!rule.enabled && "text-muted-foreground")}>
                    <TableCell className={changedCell(!base || nameChanged(rule, base))}>
                      <span className="flex items-center gap-1">
                        <Input className="w-44" maxLength={120} aria-label="규칙 이름" value={rule.name} onChange={(event) => onEdit(rule.key, { name: event.target.value })} />
                        {!base && <Badge variant="info">추가</Badge>}
                      </span>
                    </TableCell>
                    <TableCell data-field={rule.field}>
                      <span className="grid">
                        {field?.displayName ?? rule.field}
                        <span className="text-xs text-muted-foreground">{rule.field}</span>
                      </span>
                    </TableCell>
                    <TableCell className={changedCell(!base || base.operator !== rule.operator)}>
                      <div className="w-20">
                        <NativeSelect aria-label={`${name} 조건`} value={rule.operator} onChange={(event) => onEdit(rule.key, { operator: event.target.value as DraftSoftRule["operator"] })}>
                          {operators.map((item) => <NativeSelectOption key={item} value={item}>{operatorLabels[item]}</NativeSelectOption>)}
                        </NativeSelect>
                      </div>
                    </TableCell>
                    <TableCell className={changedCell(!base || thresholdChanged(rule, base))}>
                      <Input className="w-28" type="number" step="any" aria-label={`${name} 임계값`} value={rule.threshold} onChange={(event) => onEdit(rule.key, { threshold: event.target.value })} />
                    </TableCell>
                    <TableCell>{field?.unit ?? DASH}</TableCell>
                    <TableCell className={changedCell(!base || base.severity !== rule.severity)}>
                      <div className="w-20">
                        <NativeSelect aria-label={`${name} 위험도`} value={rule.severity} onChange={(event) => onEdit(rule.key, { severity: event.target.value as DraftSoftRule["severity"] })}>
                          {severities.map((item) => <NativeSelectOption key={item} value={item}>{severityLabels[item]}</NativeSelectOption>)}
                        </NativeSelect>
                      </div>
                    </TableCell>
                    <TableCell className={changedCell(!base || base.enabled !== rule.enabled)}>
                      <SwitchCell checked={rule.enabled} label={`${name} 켜기`} onChange={(value) => onEdit(rule.key, { enabled: value })} />
                    </TableCell>
                    <TableCell>
                      <span className="flex gap-1">
                        <Button size="sm" variant="outline" onClick={() => toggle(rule.key, true)}>수식 추천</Button>
                        <Button size="sm" variant="outline" onClick={() => onDelete(rule)}>삭제</Button>
                      </span>
                    </TableCell>
                  </TableRow>
                  {open.includes(rule.key) && (
                    <TableRow data-formula={rule.key}>
                      <TableCell colSpan={8} className="whitespace-normal">
                        <FormulaPanel
                          rule={rule} field={field} onClose={() => toggle(rule.key, false)}
                          onApply={(threshold) => { onEdit(rule.key, { threshold }); toggle(rule.key, false); }}
                        />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}
    </SectionCard>
  );
};

const HistoryRow = ({ item, state, disabled, onRevert }: { item: HistoryItem; state: RuleSetState; disabled: boolean; onRevert: (revision: number) => void }) => {
  const current = state.revision;
  // 현재와 같은 상태면 되돌릴 것이 없다(와이어프레임 same). hash에 이름이 없어 hash가 같은 행만 스냅샷 이름을 비교한다.
  const sameHash = item.revision !== current && item.contentHash === state.contentHash;
  const snapshot = useRuleRevision(item.revision, sameHash).data?.snapshot;
  const same = sameHash && snapshot !== undefined && snapshot.softRules.length === state.softRules.length &&
    snapshot.softRules.every((rule) => state.softRules.find((item) => item.id === rule.id)?.name === rule.name);
  return (
    <TableRow data-revision={item.revision}>
      <TableCell>
        <span className="flex items-center gap-1">
          {item.revision}
          {item.revision === current && <Badge variant="success">현재</Badge>}
        </span>
      </TableCell>
      <TableCell className="whitespace-nowrap">{stampText(item.savedAt)}</TableCell>
      <TableCell className="whitespace-nowrap">{item.savedBy.displayName}</TableCell>
      <TableCell className="whitespace-normal">{historySummary(item)}</TableCell>
      <TableCell>
        {item.revision === current ? DASH : <Button size="sm" variant="outline" disabled={disabled || same} onClick={() => onRevert(item.revision)}>이 상태로 되돌리기</Button>}
      </TableCell>
    </TableRow>
  );
};

export const RuleHistoryCard = ({ state, disabled, onRevert }: { state: RuleSetState; disabled: boolean; onRevert: (revision: number) => void }) => {
  const history = useRuleHistory(state.revision);
  const [more, setMore] = useState<{ items: HistoryItem[]; cursor?: string | null }>({ items: [] });
  const [loading, setLoading] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  // 더 읽은 뒤에는 그 쪽의 커서만 쓴다. 마지막 쪽의 null은 끝이다(첫 쪽 커서로 돌아가지 않는다).
  const cursor = more.cursor === undefined ? (history.data?.pagination.nextCursor ?? null) : more.cursor;
  const loadMore = async () => {
    if (!cursor) return;
    setLoading(true);
    setMoreError(null);
    try {
      const page = await readRuleSetHistory(cursor);
      setMore((state) => ({ items: [...state.items, ...page.items], cursor: page.pagination.nextCursor }));
    } catch (error) {
      setMoreError(errorCode(error));
    } finally {
      setLoading(false);
    }
  };
  return (
    <SectionCard title="변경 기록">
      {history.isPending && <LoadingState>변경 기록 조회 중…</LoadingState>}
      {history.isError && <ErrorState title={`변경 기록 조회 실패 · ${errorCode(history.error)}`} onRetry={() => void history.refetch()} retryLabel="다시 조회" />}
      {history.data && (
        <Table>
          <TableHeader>
            <TableRow>{["버전", "시각", "변경자", "요약", "되돌리기"].map((title) => <TableHead key={title}>{title}</TableHead>)}</TableRow>
          </TableHeader>
          <TableBody>
            {[...history.data.items, ...more.items].map((item) => (
              <HistoryRow key={item.revision} item={item} state={state} disabled={disabled} onRevert={onRevert} />
            ))}
          </TableBody>
        </Table>
      )}
      {moreError && <ErrorState title={`변경 기록 조회 실패 · ${moreError}`} />}
      {cursor && <div><Button size="sm" variant="outline" disabled={loading} onClick={() => void loadMore()}>더 보기</Button></div>}
    </SectionCard>
  );
};
