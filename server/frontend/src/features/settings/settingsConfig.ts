import {
  IconBrandSlack,
  IconMail,
  IconWebhook,
  type Icon,
} from "@tabler/icons-react";

export type SettingsToggleConfig = {
  description: string;
  disabled?: boolean;
  disabledReason?: string;
  enabled?: boolean;
  label: string;
};

export type SeverityChannelConfig = {
  description: string;
  label: string;
  severity: "critical" | "warning" | "info";
  subLabel: string;
};

export type IntegrationMetaConfig = {
  description: string;
  icon: Icon;
  iconBackground: string;
  iconColor: string;
  title: string;
  titleLabel: string;
};

export const severityChannelSettings: SeverityChannelConfig[] = [
  {
    severity: "critical",
    label: "위험",
    subLabel: "Critical",
    description: "위험도별 알림 채널과 외부 전송은 현재 서버에서 지원하지 않습니다.",
  },
  {
    severity: "warning",
    label: "주의",
    subLabel: "Warning",
    description: "위험도별 알림 채널과 외부 전송은 현재 서버에서 지원하지 않습니다.",
  },
  {
    severity: "info",
    label: "참고",
    subLabel: "Info",
    description: "위험도별 알림 채널과 외부 전송은 현재 서버에서 지원하지 않습니다.",
  },
];

export const notificationTimeSettings: SettingsToggleConfig[] = [
  {
    label: "운영 시간 우선",
    description: "알림 예약과 운영 시간 정책은 서버에서 지원하지 않습니다.",
    disabled: true,
    disabledReason: "서버 알림 정책과 연결되지 않습니다.",
  },
  {
    label: "야간 알림 축소",
    description: "야간 알림 필터링과 외부 발송은 서버에서 지원하지 않습니다.",
    disabled: true,
    disabledReason: "서버 알림 정책과 연결되지 않습니다.",
  },
];

export const dashboardDisplaySettings: SettingsToggleConfig[] = [
  {
    label: "밀도 높은 레이아웃",
    description: "운영 모니터링에 맞춰 카드 간격을 좁게 유지합니다.",
    enabled: true,
  },
  {
    label: "상단 헤더 축소",
    description: "분석 화면에서 콘텐츠 영역을 더 크게 사용합니다.",
    enabled: true,
  },
  {
    label: "숫자 강조",
    description: "위험도 점수와 처리시간 수치를 더 크게 표시합니다.",
  },
];

export const homeWidgetSettings: SettingsToggleConfig[] = [
  {
    label: "빈 위젯 catalog 표시",
    description: "숨긴 위젯과 추가 가능한 위젯을 갤러리에서 관리합니다.",
    enabled: true,
  },
];

export const permissionSettings: SettingsToggleConfig[] = [
  {
    label: "상태 변경 확인",
    description: "상태 변경은 현재 서버에 저장되지 않습니다.",
    disabled: true,
    disabledReason: "서버 지원 후 사용할 수 있습니다.",
  },
];

export const feedbackSettings: SettingsToggleConfig[] = [
  {
    label: "Toast 알림",
    description: "작업 결과 안내로 사용하며 끄기 설정은 제공하지 않습니다.",
    enabled: true,
    disabled: true,
    disabledReason: "화면 안내는 앱에서 계속 표시됩니다.",
  },
  {
    label: "효과음",
    description: "효과음 알림은 현재 지원하지 않습니다.",
    disabled: true,
    disabledReason: "지원 예정인 설정입니다.",
  },
];

export const integrationMeta = {
  slack: {
    title: "Slack 알림 연동",
    titleLabel: "Slack Incoming Webhook",
    description: "Slack 알림 연동은 현재 서버에서 지원하지 않습니다.",
    icon: IconBrandSlack,
    iconBackground: "#f4ede4",
    iconColor: "#4A154B",
  },
  email: {
    title: "이메일 (SMTP) 연동",
    titleLabel: "SMTP 아웃바운드 서버",
    description: "이메일 알림 연동은 현재 서버에서 지원하지 않습니다.",
    icon: IconMail,
    iconBackground: "var(--theme-soft, #e8f0ff)",
    iconColor: "var(--theme-color, #2f6fed)",
  },
  webhook: {
    title: "커스텀 Webhook 연동",
    titleLabel: "Webhook (JSON POST)",
    description: "Webhook 연동은 현재 서버에서 지원하지 않습니다.",
    icon: IconWebhook,
    iconBackground: "#eef2f6",
    iconColor: "#3b82f6",
  },
} satisfies Record<string, IntegrationMetaConfig>;
