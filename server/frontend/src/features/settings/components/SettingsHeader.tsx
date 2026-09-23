import {
  IconArrowsMaximize,
  IconArrowsMinimize,
} from "@tabler/icons-react";
import { Button } from "../../../components/ui/button";
import type { SettingsSection } from "../types";

type SettingsHeaderProps = {
  config: SettingsSection;
  isWide: boolean;
  onToggleWide: (val: boolean) => void;
};

export const SettingsHeader = ({
  config,
  isWide,
  onToggleWide,
}: SettingsHeaderProps) => (
  <header className="settings-header">
    <div>
      <h2>
        <span className={`settings-heading-icon ${config.className}`}>
          {config.icon}
        </span>
        {config.label}
      </h2>
    </div>
    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
      <Button
        variant="outline"
        size="icon"
        onClick={() => onToggleWide(!isWide)}
        aria-label={isWide ? "콤팩트 화면으로 보기" : "넓은 화면으로 보기"}
      >
        {isWide ? (
          <IconArrowsMinimize size={15} />
        ) : (
          <IconArrowsMaximize size={15} />
        )}
      </Button>
      <Button
        variant="outline"
        disabled
        title="설정 저장 기능은 현재 서버에서 지원하지 않습니다."
      >
        저장 미지원
      </Button>
    </div>
  </header>
);
