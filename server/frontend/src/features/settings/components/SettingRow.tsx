import { useState } from "react";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

type SettingRowProps = {
  description: string;
  disabled?: boolean;
  disabledReason?: string;
  enabled?: boolean;
  label: string;
};

export const SettingRow = ({ description, disabled = false, disabledReason, enabled = false, label }: SettingRowProps) => {
  const [checked, setChecked] = useState(enabled);
  const displayedChecked = disabled ? enabled : checked;
  return (
    <div className={cn("flex items-start justify-between gap-4 border-b py-3 last:border-b-0", disabled && "opacity-80")}>
      <div className="grid gap-0.5">
        <strong className="text-sm font-medium">{label}</strong>
        <p className="text-sm text-muted-foreground">{description}</p>
        {disabledReason && <span className="text-xs text-muted-foreground">{disabledReason}</span>}
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
