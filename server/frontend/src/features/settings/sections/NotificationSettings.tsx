import { IconAlertTriangle, IconFlame, IconInfoCircle } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { SettingRow } from "../components/SettingRow";
import { notificationTimeSettings, severityChannelSettings, type SeverityChannelConfig } from "../settingsConfig";

const severityTag = {
  critical: { variant: "critical", Icon: IconFlame },
  warning: { variant: "warning", Icon: IconAlertTriangle },
  info: { variant: "info", Icon: IconInfoCircle },
} as const;

const SeverityChannelRow = ({ severity, label, subLabel, description }: SeverityChannelConfig) => {
  const { variant, Icon } = severityTag[severity];
  return (
    <div className="grid gap-1 border-b py-3 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={variant}>
            <Icon aria-hidden="true" />
            {label}
          </Badge>
          <span className="text-xs text-muted-foreground">{subLabel}</span>
        </div>
        <Badge variant="outline">미지원</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
};

export const NotificationSettings = () => (
  <div className="grid gap-4">
    <SectionCard title="위험도별 알림 채널 지원">
      <div>
        {severityChannelSettings.map((setting) => (
          <SeverityChannelRow key={setting.severity} {...setting} />
        ))}
      </div>
    </SectionCard>
    <SectionCard title="알림 시간">
      <div>
        {notificationTimeSettings.map((setting) => (
          <SettingRow key={setting.label} {...setting} />
        ))}
      </div>
    </SectionCard>
  </div>
);
