import type { ChangeEventHandler, HTMLInputTypeAttribute, ReactNode } from "react";

type SettingsTextInputProps = {
  action?: ReactNode;
  disabled?: boolean;
  gridColumn?: string;
  label: string;
  onChange?: ChangeEventHandler<HTMLInputElement>;
  placeholder?: string;
  type?: HTMLInputTypeAttribute;
  value: string;
};

export const SettingsTextInput = ({
  action,
  disabled = false,
  gridColumn,
  label,
  onChange,
  placeholder,
  type = "text",
  value,
}: SettingsTextInputProps) => (
  <div style={{ display: "flex", flexDirection: "column", gap: "6px", gridColumn }}>
    <label style={{ fontSize: "11px", color: "var(--mute)", fontWeight: "bold" }}>
      {label}
    </label>
    <div style={{ display: action ? "flex" : "block", gap: "8px" }}>
      <input
        type={type}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        disabled={disabled}
        autoComplete="off"
        style={{
          width: "100%",
          flex: action ? 1 : undefined,
          padding: "8px 12px",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--hairline)",
          background: "var(--canvas-soft)",
          color: "var(--ink)",
          fontSize: "12px",
          opacity: disabled ? 0.7 : undefined,
        }}
      />
      {action}
    </div>
  </div>
);
