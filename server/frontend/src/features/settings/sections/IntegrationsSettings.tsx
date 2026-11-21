import { useId, type ReactNode } from "react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { AuthUser } from "@/types/auth";
import { EmailAlertsCard } from "../email/EmailAlertsCard";
import { integrationMeta, type IntegrationMetaConfig } from "../settingsConfig";

const disabledReason = "서버 연동을 지원하지 않아 URL 입력, 연결 설정, 테스트를 사용할 수 없습니다.";
const placeholder = "서버 지원 후 설정할 수 있습니다.";

const DisabledField = ({ label, type = "text", action }: { label: string; type?: string; action?: ReactNode }) => {
  const id = useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </Label>
      <div className="flex gap-2">
        <Input id={id} type={type} value="" readOnly placeholder={placeholder} disabled autoComplete="off" />
        {action}
      </div>
    </div>
  );
};
const TestUnsupported = () => (
  <Button size="sm" variant="outline" disabled className="h-9">
    테스트 미지원
  </Button>
);

const IntegrationCard = ({ meta, children }: { meta: IntegrationMetaConfig; children: ReactNode }) => {
  const Icon = meta.icon;
  return (
    <SectionCard title={meta.title}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex size-10 items-center justify-center rounded-md bg-muted">
            <Icon className="size-6 text-muted-foreground" aria-hidden="true" />
          </span>
          <div className="grid gap-0.5">
            <strong className="text-sm font-medium">{meta.titleLabel}</strong>
            <p className="text-sm text-muted-foreground">{meta.description}</p>
          </div>
        </div>
        <Switch checked={false} disabled aria-label={`${meta.title} 꺼짐`} />
      </div>
      <p className="text-xs text-muted-foreground">{disabledReason}</p>
      {children}
    </SectionCard>
  );
};

/** 연동 관리: 메일 알림은 관리자 설정(EmailAlertsCard, 자체 [저장]). Slack·Webhook은 서버 연동이 없어 입력·테스트를 잠근다. */
export const IntegrationsSettings = ({ currentUser }: { currentUser: AuthUser }) => (
  <div className="grid gap-4">
    <IntegrationCard meta={integrationMeta.slack}>
      <DisabledField label="WEBHOOK URL" type="password" action={<TestUnsupported />} />
    </IntegrationCard>
    <EmailAlertsCard currentUser={currentUser} />
    <IntegrationCard meta={integrationMeta.webhook}>
      <DisabledField label="엔드포인트 URL" action={<TestUnsupported />} />
    </IntegrationCard>
  </div>
);
