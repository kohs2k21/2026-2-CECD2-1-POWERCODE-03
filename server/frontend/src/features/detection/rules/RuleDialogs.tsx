import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/Modal";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { cn } from "@/lib/utils";
import { HARD_TERM, SOFT_TERM, changeLine, operatorLabels, severityLabels, severities, type DraftSoftRule } from "./ruleDraft";
import type { RuleSetChange, RuleSetWarning, Severity, SoftRuleField, SoftRuleOperator } from "./ruleSetApi";

export const ConfirmDialog = ({
  title, message, actionText, destructive = false, onCancel, onConfirm,
}: {
  title: string; message: ReactNode; actionText: string; destructive?: boolean; onCancel: () => void; onConfirm: () => void;
}) => (
  <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
    <DialogContent showCloseButton={false} className="sm:max-w-sm">
      <DialogTitle>{title}</DialogTitle>
      <DialogDescription>{message}</DialogDescription>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>돌아가기</Button>
        <Button variant={destructive ? "destructive" : "default"} onClick={onConfirm}>{actionText}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);

export type PendingSave = {
  title: string;
  lead: string;
  actionText: string;
  changes: RuleSetChange[];
  /** 저장은 허용하는 서버 경고(rule-set.md v3.6 `warnings[]`). 문장은 서버 값 그대로. */
  warnings?: RuleSetWarning[];
  /** 이번 저장으로 꺼진 채 남는 이상 규칙 이름(새로 끄지 않아도 강조, rule-set.md). */
  alreadyOff: string[];
  run: (confirmHardRuleDisable: boolean) => void;
};

/** 확인창 본문: 변경 목록(꺼지는 이상 규칙 강조)·확인 체크·적용 시점·명령. */
export const RuleSaveBody = ({ pending, onCancel }: { pending: PendingSave; onCancel: () => void }) => {
  const [checked, setChecked] = useState(false);
  const hardOff = pending.changes.filter((change) => change.type === "hard_disabled").length;
  return (
    <>
      <ul className="grid gap-1 text-sm" data-rule-change-list>
        {pending.changes.map((change, index) => {
          const off = change.type === "hard_disabled";
          return (
            <li key={`${change.type}:${change.id ?? index}`} data-kind={change.type} className={cn(off && "font-medium text-destructive-text")}>
              {changeLine(change)}{off ? " · 명백한 이상을 놓칠 수 있습니다" : ""}
            </li>
          );
        })}
        {pending.alreadyOff.map((name) => (
          <li key={name} data-kind="hard_off" className="font-medium text-destructive-text">
            {HARD_TERM} 꺼짐 · {name} · 명백한 이상을 놓칠 수 있습니다
          </li>
        ))}
      </ul>
      {(pending.warnings ?? []).length > 0 && (
        <ul className="grid gap-1 text-sm text-warning-text" data-rule-warnings>
          {pending.warnings!.map((warning, index) => (
            <li key={`${warning.code}:${warning.ruleId ?? warning.clientRef ?? index}`} data-warning={warning.code}>경고 · {warning.message}</li>
          ))}
        </ul>
      )}
      {hardOff > 0 && (
        <div className="flex items-start gap-2">
          <Checkbox id="rule-save-confirm" checked={checked} onCheckedChange={(value) => setChecked(value === true)} />
          <Label htmlFor="rule-save-confirm">꺼지는 {HARD_TERM} {hardOff}개를 확인했고 이대로 적용합니다</Label>
        </div>
      )}
      <p className="text-sm">저장 이후 새 판정부터 적용됩니다.</p>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>돌아가기</Button>
        <Button disabled={hardOff > 0 && !checked} onClick={() => pending.run(hardOff > 0)}>{pending.actionText}</Button>
      </DialogFooter>
    </>
  );
};

export const RuleSaveDialog = ({ pending, onCancel }: { pending: PendingSave; onCancel: () => void }) => (
  <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
    <DialogContent className="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>{pending.title}</DialogTitle>
        <DialogDescription>{pending.lead}</DialogDescription>
      </DialogHeader>
      <RuleSaveBody pending={pending} onCancel={onCancel} />
    </DialogContent>
  </Dialog>
);

type NewRule = Pick<DraftSoftRule, "name" | "field" | "operator" | "threshold" | "severity">;

/** 이상 징후 규칙 추가 입력(와이어프레임 ruleAddDialog). 추가는 초안에만 들어가고 저장해야 운영에 반영된다. */
export const RuleAddBody = ({ fields, onAdd, onCancel }: { fields: SoftRuleField[]; onAdd: (rule: NewRule) => void; onCancel: () => void }) => {
  const [field, setField] = useState(fields[0]?.fieldId ?? "");
  const [name, setName] = useState("");
  const [operator, setOperator] = useState<SoftRuleOperator>("gt");
  const [threshold, setThreshold] = useState("");
  const [severity, setSeverity] = useState<Severity>("warning");
  const [invalid, setInvalid] = useState(false);
  const selected = fields.find((item) => item.fieldId === field);
  const add = () => {
    if (!name.trim() || threshold.trim() === "" || !Number.isFinite(Number(threshold))) {
      setInvalid(true);
      return;
    }
    onAdd({ name: name.trim(), field, operator, threshold: threshold.trim(), severity });
  };
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label htmlFor="rule-add-field">대상 필드</Label>
          <NativeSelect id="rule-add-field" value={field} onChange={(event) => setField(event.target.value)}>
            {fields.map((item) => (
              <NativeSelectOption key={item.fieldId} value={item.fieldId}>{item.displayName} · {item.fieldId}</NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="rule-add-name">규칙 이름</Label>
          <Input id="rule-add-name" maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="rule-add-operator">조건</Label>
          <NativeSelect id="rule-add-operator" value={operator} onChange={(event) => setOperator(event.target.value as SoftRuleOperator)}>
            {(selected?.operators ?? []).map((item) => <NativeSelectOption key={item} value={item}>{operatorLabels[item]}</NativeSelectOption>)}
          </NativeSelect>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="rule-add-threshold">임계값 <span className="text-muted-foreground">{selected?.unit}</span></Label>
          <Input id="rule-add-threshold" type="number" step="any" value={threshold} onChange={(event) => setThreshold(event.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="rule-add-severity">위험도</Label>
          <NativeSelect id="rule-add-severity" value={severity} onChange={(event) => setSeverity(event.target.value as Severity)}>
            {severities.map((item) => <NativeSelectOption key={item} value={item}>{severityLabels[item]}</NativeSelectOption>)}
          </NativeSelect>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">대상 필드는 추가한 뒤 바꿀 수 없습니다.</p>
      {invalid && <p role="alert" className="text-sm text-destructive-text">규칙 이름과 임계값(숫자)을 입력해 주세요.</p>}
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>돌아가기</Button>
        <Button onClick={add}>추가</Button>
      </DialogFooter>
    </>
  );
};

export const RuleAddDialog = ({ fields, onAdd, onCancel }: { fields: SoftRuleField[]; onAdd: (rule: NewRule) => void; onCancel: () => void }) => (
  <Modal isOpen onOpenChange={(open) => { if (!open) onCancel(); }} title={`${SOFT_TERM} 추가`}>
    <RuleAddBody fields={fields} onAdd={onAdd} onCancel={onCancel} />
  </Modal>
);
