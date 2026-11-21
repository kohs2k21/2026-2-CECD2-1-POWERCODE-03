import { useId, useState, type ReactNode } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { stampText } from "@/features/analysis/labels";
import { riskFloorLabels } from "@/features/notifications/notificationLabels";
import type { AuthUser } from "@/types/auth";
import { integrationMeta } from "../settingsConfig";
import {
  alertKinds,
  checkRecipient,
  errorCodeOf,
  riskLevels,
  transportUsable,
  type AlertKind,
  type EmailAlertSettings,
  type EmailAlertSettingsUpdate,
  type RecipientProblem,
  type RiskLevel,
  type SmtpSecurity,
} from "./emailAlertsApi";
import {
  deliveryKindLabels,
  deliveryStatusLabels,
  emailText,
  kindLabels,
  recipientCount,
  recipientProblemLabels,
  removeLabel,
  saveErrorDetail,
  securityLabels,
  testConfirmText,
  testErrorText,
  testResultText,
  transportDetail,
  transportLabels,
  transportVariant,
  verifyText,
} from "./emailAlertLabels";
import { useEmailAlertSettings, useProcessingLag, useRecentDeliveries, useSaveEmailAlertSettings, useSendTestEmail } from "./useEmailAlerts";

/** 위험도 하한 라벨은 알림 설정과 같은 기존 문구(위험 · 주의 이상 · 참고 이상). 선택 이름도 알림 설정의 기존 라벨. */
const riskFloorLabel = "위험도 하한";

/** 입력 중인 값. 비밀번호는 입력한 새 값만 들고 있고(저장값은 받지 않음), cleared = [비밀번호 지우기]를 눌렀음. */
type Draft = {
  enabled: boolean;
  kinds: AlertKind[];
  minRiskLevel: RiskLevel;
  recipients: string[];
  host: string;
  port: string;
  security: SmtpSecurity | "";
  username: string;
  password: string;
  passwordCleared: boolean;
  fromAddress: string;
  publicBaseUrl: string;
};
const draftOf = (settings: EmailAlertSettings): Draft => ({
  enabled: settings.enabled,
  kinds: settings.rules.kinds,
  minRiskLevel: settings.rules.minRiskLevel,
  recipients: settings.recipients,
  host: settings.smtp.host ?? "",
  port: settings.smtp.port === null ? "" : String(settings.smtp.port),
  security: settings.smtp.security ?? "",
  username: settings.smtp.username ?? "",
  password: "",
  passwordCleared: false,
  fromAddress: settings.smtp.fromAddress ?? "",
  publicBaseUrl: settings.publicBaseUrl ?? "",
});
const textOrNull = (value: string) => (value.trim() ? value.trim() : null);
/** PUT 본문(전체). 비밀번호: 새 값이면 문자열, 지움이면 null, 그 밖은 생략(저장값 유지). 계정이 비면 보내지 않는다(서버가 함께 지움). */
export const updateOf = (draft: Draft): EmailAlertSettingsUpdate => {
  const username = textOrNull(draft.username);
  const smtp: EmailAlertSettingsUpdate["smtp"] = {
    host: textOrNull(draft.host),
    port: draft.port ? Number(draft.port) : null,
    security: draft.security || null,
    username,
    fromAddress: textOrNull(draft.fromAddress),
  };
  if (username !== null && draft.password) smtp.password = draft.password;
  else if (username !== null && draft.passwordCleared) smtp.password = null;
  return {
    enabled: draft.enabled,
    rules: { kinds: alertKinds.filter((kind) => draft.kinds.includes(kind)), minRiskLevel: draft.minRiskLevel },
    recipients: draft.recipients,
    smtp,
    publicBaseUrl: textOrNull(draft.publicBaseUrl),
  };
};
const sameDraft = (a: Draft, b: Draft) => JSON.stringify(updateOf(a)) === JSON.stringify(updateOf(b));

const Field = ({ label, help, children }: { label: string; help?: string; children: (id: string, helpId: string | undefined) => ReactNode }) => {
  const id = useId();
  const helpId = help ? `${id}-help` : undefined;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children(id, helpId)}
      {help && (
        <p id={helpId} className="text-xs text-muted-foreground">
          {help}
        </p>
      )}
    </div>
  );
};

type TestOutcome = { kind: "result"; text: string } | { kind: "pending" };

/** 받는 사람 목록·추가·삭제·시험 발송. 시험 발송은 저장된 받는 사람만(서버 규칙), 보낼 수 있는 전송 상태일 때만. */
const Recipients = ({
  draft,
  settings,
  locked,
  onChange,
}: {
  draft: Draft;
  settings: EmailAlertSettings;
  locked: boolean;
  onChange: (recipients: string[]) => void;
}) => {
  const inputId = useId();
  const [input, setInput] = useState("");
  const [problem, setProblem] = useState<RecipientProblem | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, TestOutcome>>({});
  const test = useSendTestEmail();
  const max = settings.limits.recipients;
  const add = () => {
    const checked = checkRecipient(input, draft.recipients, max);
    if ("problem" in checked) {
      setProblem(checked.problem);
      return;
    }
    setProblem(null);
    setInput("");
    onChange([...draft.recipients, checked.address]);
  };
  const send = (address: string) => {
    setConfirm(null);
    setOutcomes((current) => ({ ...current, [address]: { kind: "pending" } }));
    test.mutate(address, {
      onSuccess: (result) => setOutcomes((current) => ({ ...current, [address]: { kind: "result", text: testResultText(result) } })),
      onError: (error) => setOutcomes((current) => ({ ...current, [address]: { kind: "result", text: testErrorText(errorCodeOf(error)) } })),
    });
  };
  const saved = (address: string) => settings.recipients.some((entry) => entry.toLowerCase() === address.toLowerCase());
  const canTest = transportUsable(settings.transport.status) && !test.isPending && !locked;
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={inputId} className="text-sm font-medium">
          {emailText.recipients}
        </Label>
        <span className="text-xs text-muted-foreground tabular-nums">{recipientCount(draft.recipients.length, max)}</span>
      </div>
      {draft.recipients.length > 0 && (
        <ul className="grid divide-y rounded-md border" aria-label={emailText.recipients}>
          {draft.recipients.map((address) => {
            const outcome = outcomes[address];
            return (
              <li key={address} className="grid gap-1 px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-sm">{address}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    <Button size="sm" variant="outline" disabled={!canTest || !saved(address)} onClick={() => setConfirm(address)}>
                      {emailText.testSend}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={locked}
                      aria-label={removeLabel(address)}
                      onClick={() => onChange(draft.recipients.filter((entry) => entry !== address))}
                    >
                      {emailText.remove}
                    </Button>
                  </div>
                </div>
                {outcome?.kind === "result" && (
                  <p role="status" className="text-xs text-muted-foreground" data-test-result={address}>
                    {outcome.text}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex gap-2">
        <Input
          id={inputId}
          type="email"
          value={input}
          disabled={locked}
          autoComplete="off"
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${inputId}-error` : undefined}
          onChange={(event) => {
            setInput(event.target.value);
            setProblem(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
        />
        <Button variant="outline" disabled={locked || !input.trim()} onClick={add}>
          {emailText.add}
        </Button>
      </div>
      {problem && (
        <p id={`${inputId}-error`} className="text-xs text-destructive-text">
          {recipientProblemLabels[problem]}
        </p>
      )}
      <TestSendConfirm address={confirm} onSend={send} onCancel={() => setConfirm(null)} />
    </div>
  );
};

/** 시험 발송 확인(받는 사람 1명). address null = 닫힘. Escape·바깥 누름 = 취소. */
export const TestSendConfirm = ({ address, onSend, onCancel }: { address: string | null; onSend: (address: string) => void; onCancel: () => void }) => (
  <Dialog open={address !== null} onOpenChange={(open) => !open && onCancel()}>
    <DialogContent showCloseButton={false} className="sm:max-w-sm">
      <DialogTitle>{emailText.testSend}</DialogTitle>
      <DialogDescription>{address ? testConfirmText(address) : ""}</DialogDescription>
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>
          {emailText.testSendCancel}
        </Button>
        <Button onClick={() => address && onSend(address)}>{emailText.testSendGo}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);

const RecentDeliveries = () => {
  const deliveries = useRecentDeliveries();
  return (
    <div className="grid gap-2">
      <h3 className="text-sm font-medium">{emailText.recent}</h3>
      {deliveries.isPending ? (
        <LoadingState />
      ) : deliveries.isError ? (
        <ErrorState title={errorCodeOf(deliveries.error)} onRetry={() => void deliveries.refetch()} />
      ) : deliveries.data.items.length === 0 ? (
        <EmptyState>{emailText.recentEmpty}</EmptyState>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {emailText.recentColumns.map((column) => (
                <TableHead key={column}>{column}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {deliveries.data.items.map((item) => (
              <TableRow key={item.id}>
                <TableCell className="tabular-nums">{stampText(item.createdAt)}</TableCell>
                <TableCell>{deliveryKindLabels[item.kind]}</TableCell>
                <TableCell>{item.recipient}</TableCell>
                <TableCell data-delivery-status={item.status}>{deliveryStatusLabels[item.status]}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
};

/** 관리자 화면: 서버 값을 불러와 입력값(draft)으로 고치고 [저장]으로 전체를 보낸다. */
const EmailAlertsForm = ({ currentUser }: { currentUser: AuthUser }) => {
  const settings = useEmailAlertSettings();
  const save = useSaveEmailAlertSettings();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [status, setStatus] = useState("");
  const lagging = useProcessingLag(currentUser.id, settings.data, settings.dataUpdatedAt);
  const switchId = useId();
  if (settings.isPending) return <LoadingState />;
  if (settings.isError) return <ErrorState title={errorCodeOf(settings.error)} onRetry={() => void settings.refetch()} />;
  const server = settings.data;
  const base = draftOf(server);
  const value = draft ?? base;
  const locked = save.isPending;
  const dirty = !sameDraft(value, base);
  const change = (patch: Partial<Draft>) => {
    setStatus("");
    setDraft({ ...value, ...patch });
  };
  const submit = () => {
    setStatus("");
    save.mutate(updateOf(value), {
      onSuccess: ({ saved }) => {
        setDraft(null);
        setStatus(verifyText(saved.verify));
      },
    });
  };
  const usable = transportUsable(server.transport.status);
  const detail = transportDetail(server.transport.checkedAt, server.transport.code, stampText);
  const passwordShown = server.smtp.passwordSet && !value.passwordCleared;
  const noUsername = !value.username.trim();
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={transportVariant[server.transport.status]} data-transport-status={server.transport.status}>
            {transportLabels[server.transport.status]}
          </Badge>
          {detail && <span className="text-xs text-muted-foreground tabular-nums">{detail}</span>}
          {lagging && <Badge variant="warning">{emailText.processingLag}</Badge>}
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor={switchId}>{emailText.enable}</Label>
          <Switch
            id={switchId}
            checked={value.enabled}
            // 꺼진 상태에서 켜기는 보낼 수 있는 전송 상태일 때만(API 409 email_transport_not_configured와 같음). 끄기는 언제나.
            disabled={locked || (!value.enabled && !usable)}
            onCheckedChange={(checked) => change({ enabled: checked })}
          />
        </div>
      </div>

      <fieldset className="grid gap-3 sm:grid-cols-2" disabled={locked}>
        <Field label={emailText.host}>
          {(id) => <Input id={id} value={value.host} autoComplete="off" onChange={(event) => change({ host: event.target.value })} />}
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label={emailText.port}>
            {(id) => (
              <NativeSelect id={id} value={value.port} onChange={(event) => change({ port: event.target.value })}>
                <NativeSelectOption value="" />
                {server.limits.ports.map((port) => (
                  <NativeSelectOption key={port} value={String(port)}>
                    {port}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            )}
          </Field>
          <Field label={emailText.security}>
            {(id) => (
              <NativeSelect id={id} value={value.security} onChange={(event) => change({ security: event.target.value as SmtpSecurity | "" })}>
                <NativeSelectOption value="" />
                {server.limits.security.map((security) => (
                  <NativeSelectOption key={security} value={security}>
                    {securityLabels[security]}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            )}
          </Field>
        </div>
        <Field label={emailText.username}>
          {(id) => <Input id={id} value={value.username} autoComplete="off" onChange={(event) => change({ username: event.target.value })} />}
        </Field>
        <Field label={emailText.password}>
          {(id) => (
            <div className="flex gap-2">
              <Input
                id={id}
                type="password"
                value={value.password}
                disabled={locked || noUsername}
                autoComplete="new-password"
                placeholder={passwordShown ? emailText.passwordSaved : undefined}
                onChange={(event) => change({ password: event.target.value })}
              />
              {passwordShown && (
                <Button variant="outline" disabled={locked || noUsername} onClick={() => change({ password: "", passwordCleared: true })}>
                  {emailText.clearPassword}
                </Button>
              )}
            </div>
          )}
        </Field>
        <Field label={emailText.fromAddress}>
          {(id) => <Input id={id} type="email" value={value.fromAddress} autoComplete="off" onChange={(event) => change({ fromAddress: event.target.value })} />}
        </Field>
        <Field label={emailText.publicBaseUrl} help={emailText.publicBaseUrlHelp}>
          {(id, helpId) => (
            <Input
              id={id}
              type="url"
              value={value.publicBaseUrl}
              autoComplete="off"
              aria-describedby={helpId}
              onChange={(event) => change({ publicBaseUrl: event.target.value })}
            />
          )}
        </Field>
      </fieldset>

      <div className="flex flex-wrap items-end gap-6">
        <fieldset className="grid gap-2" disabled={locked}>
          <legend className="mb-1 text-sm font-medium">{emailText.kinds}</legend>
          <div className="flex items-center gap-4">
            {alertKinds.map((kind) => (
              <div key={kind} className="flex items-center gap-2">
                <Checkbox
                  id={`email-kind-${kind}`}
                  checked={value.kinds.includes(kind)}
                  disabled={locked}
                  onCheckedChange={(checked) =>
                    change({ kinds: checked === true ? [...value.kinds.filter((entry) => entry !== kind), kind] : value.kinds.filter((entry) => entry !== kind) })
                  }
                />
                <Label htmlFor={`email-kind-${kind}`}>{kindLabels[kind]}</Label>
              </div>
            ))}
          </div>
        </fieldset>
        <div className="grid w-40 gap-1.5">
          <Label htmlFor="email-min-risk" className="text-xs text-muted-foreground">
            {riskFloorLabel}
          </Label>
          <NativeSelect
            id="email-min-risk"
            value={value.minRiskLevel}
            disabled={locked}
            onChange={(event) => change({ minRiskLevel: event.target.value as RiskLevel })}
          >
            {riskLevels.map((level) => (
              <NativeSelectOption key={level} value={level}>
                {riskFloorLabels[level]}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
      </div>

      <Recipients draft={value} settings={server} locked={locked} onChange={(recipients) => change({ recipients })} />

      <div className="flex flex-wrap items-center justify-end gap-3">
        <p role="status" className="text-sm text-muted-foreground">
          {locked ? "" : status}
        </p>
        <Button disabled={locked || !dirty} onClick={submit}>
          {emailText.save}
        </Button>
      </div>
      {save.isError && !locked && (
        <ErrorState title={emailText.saveError}>{saveErrorDetail(errorCodeOf(save.error))}</ErrorState>
      )}

      <RecentDeliveries />
    </div>
  );
};

/** 설정 › 연동 관리 › 이메일 (SMTP) 연동(contracts/email-alerts.md 11절). 관리자만 설정, 일반 사용자는 안내 한 줄만. */
export const EmailAlertsCard = ({ currentUser }: { currentUser: AuthUser }) => {
  const meta = integrationMeta.email;
  const Icon = meta.icon;
  const admin = currentUser.userType === "admin";
  return (
    <SectionCard title={meta.title}>
      {admin ? (
        <>
          <div className="flex items-start gap-3">
            <span className="flex size-10 items-center justify-center rounded-md bg-muted">
              <Icon className="size-6 text-muted-foreground" aria-hidden="true" />
            </span>
            <div className="grid gap-0.5">
              <strong className="text-sm font-medium">{meta.titleLabel}</strong>
              <p className="text-sm text-muted-foreground">{meta.description}</p>
            </div>
          </div>
          <EmailAlertsForm currentUser={currentUser} />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{emailText.adminOnly}</p>
      )}
    </SectionCard>
  );
};
