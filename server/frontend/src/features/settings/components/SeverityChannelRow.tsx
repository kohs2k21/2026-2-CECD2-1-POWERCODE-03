import {
  IconAlertTriangle,
  IconFlame,
  IconInfoCircle,
} from "@tabler/icons-react";

type SeverityChannelRowProps = {
  description: string;
  label: string;
  severity: "critical" | "warning" | "info";
  subLabel: string;
};

const severityTheme = {
  critical: {
    color: "#e03131",
    bg: "#ffe3e3",
    border: "rgba(224, 49, 49, 0.2)",
    Icon: IconFlame,
  },
  warning: {
    color: "#f08c00",
    bg: "#fff0d0",
    border: "rgba(240, 140, 0, 0.2)",
    Icon: IconAlertTriangle,
  },
  info: {
    color: "#3b82f6",
    bg: "#e7f0ff",
    border: "rgba(59, 130, 246, 0.2)",
    Icon: IconInfoCircle,
  },
};

const SeverityTag = ({ label, severity }: Pick<SeverityChannelRowProps, "label" | "severity">) => {
  const theme = severityTheme[severity];
  const Icon = theme.Icon;

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 8px",
        borderRadius: "4px",
        backgroundColor: theme.bg,
        border: `1px solid ${theme.border}`,
        color: theme.color,
        fontSize: "12px",
        fontWeight: "bold",
      }}
    >
      <Icon size={16} aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
};

export const SeverityChannelRow = ({
  severity,
  label,
  subLabel,
  description,
}: SeverityChannelRowProps) => {
  return (
    <div className="severity-channel-row">
      <div className="severity-channel-row__header">
        <div className="severity-channel-row__severity">
          <SeverityTag severity={severity} label={label} />
          <span className="severity-channel-row__sub-label">{subLabel}</span>
        </div>
        <span className="settings-unavailable-badge">미지원</span>
      </div>
      <p>{description}</p>
    </div>
  );
};
