import { useState } from "react";
import { Switch } from "../../../components/ui/switch";

type SettingRowProps = {
  description: string;
  disabled?: boolean;
  disabledReason?: string;
  enabled?: boolean;
  label: string;
};

export const SettingRow = ({
  description,
  disabled = false,
  disabledReason,
  enabled = false,
  label,
}: SettingRowProps) => {
  const [checked, setChecked] = useState(enabled);
  const displayedChecked = disabled ? enabled : checked;

  return (
    <div className={`settings-row${disabled ? " settings-row--disabled" : ""}`}>
      <div>
        <strong>{label}</strong>
        <p>{description}</p>
        {disabledReason ? (
          <span className="settings-row__disabled-reason">{disabledReason}</span>
        ) : null}
      </div>
      <Switch
        checked={displayedChecked}
        disabled={disabled}
        onCheckedChange={setChecked}
        aria-label={`${label} ${displayedChecked ? "켜짐" : "꺼짐"}`}
      />
    </div>
  );
};
