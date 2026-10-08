import type { ReactNode } from "react";
import { Switch } from "../../../components/ui/switch";
import { SettingsCard } from "./SettingsCard";

type IntegrationCardProps = {
  children?: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
  description: string;
  enabled: boolean;
  icon: ReactNode;
  iconBackground: string;
  title: string;
  titleLabel: string;
};

export const IntegrationCard = ({
  children,
  disabled = false,
  disabledReason,
  description,
  enabled,
  icon,
  iconBackground,
  title,
  titleLabel,
}: IntegrationCardProps) => (
  <SettingsCard title={title}>
    <div className="settings-integration__body">
      <div className="settings-integration__header">
        <div className="settings-integration__identity">
          <div
            className="settings-integration__icon"
            style={{ backgroundColor: iconBackground }}
          >
            {icon}
          </div>
          <div>
            <strong className="settings-integration__title">
              {titleLabel}
            </strong>
            <p className="settings-integration__description">{description}</p>
          </div>
        </div>
        <Switch
          checked={enabled}
          disabled={disabled}
          aria-label={`${title} ${enabled ? "켜짐" : "꺼짐"}`}
        />
      </div>

      {disabledReason ? (
        <p className="settings-integration__disabled-reason">
          {disabledReason}
        </p>
      ) : null}
      {children}
    </div>
  </SettingsCard>
);
