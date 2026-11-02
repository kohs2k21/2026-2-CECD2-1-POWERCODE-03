import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SettingRow } from "../components/SettingRow";
import { dashboardDisplaySettings, homeWidgetSettings } from "../settingsConfig";

/** 테마: 현재 라이트만 지원(다크 미지원). 미리보기 색은 토큰만 사용. */
const ThemeOption = ({
  label,
  dark = false,
  current,
  disabled,
  describedBy,
}: {
  label: string;
  dark?: boolean;
  current: boolean;
  disabled?: boolean;
  describedBy?: string;
}) => (
  <Button
    variant="outline"
    aria-pressed={current}
    disabled={disabled}
    aria-describedby={describedBy}
    title={current ? "현재 적용 중인 테마" : "다크 테마는 아직 지원되지 않습니다."}
    className={cn(
      "grid h-auto justify-items-center gap-2 rounded-lg border-2 p-4 text-xs font-semibold whitespace-normal disabled:cursor-not-allowed disabled:opacity-55",
      current ? "border-primary" : "border-border",
      dark ? "bg-primary text-primary-foreground hover:bg-primary" : "bg-card text-foreground hover:bg-card",
    )}
  >
    <span aria-hidden="true" className={cn("grid h-14 w-24 grid-cols-[1fr_3fr] gap-1 rounded-md border p-1", dark ? "border-primary-foreground/30" : "")}>
      <span className={cn("rounded-sm", dark ? "bg-primary-foreground/20" : "bg-muted")} />
      <span className="grid content-start gap-1">
        <span className={cn("h-2 rounded-sm", dark ? "bg-primary-foreground/60" : "bg-primary")} />
        <span className={cn("h-1.5 w-2/3 rounded-sm", dark ? "bg-primary-foreground/30" : "bg-muted")} />
      </span>
    </span>
    {label}
  </Button>
);

export const DisplaySettings = () => (
  <div className="grid gap-4">
    <SectionCard title="대시보드 표시">
      <div>
        {dashboardDisplaySettings.map((setting) => (
          <SettingRow
            key={setting.label}
            {...setting}
            enabled={false}
            disabled
            disabledReason="아직 대시보드 화면에 적용되지 않는 설정입니다."
          />
        ))}
      </div>
    </SectionCard>
    <SectionCard title="테마 선택">
      <div className="grid grid-cols-2 gap-3 sm:max-w-md">
        <ThemeOption label="화이트 (라이트 테마)" current />
        <ThemeOption
          label="블랙 (다크 테마, 준비 중)"
          dark
          current={false}
          disabled
          describedBy="dark-theme-unavailable"
        />
      </div>
      <p id="dark-theme-unavailable" className="text-sm text-muted-foreground">
        현재 화면은 라이트 테마입니다. 다크 테마는 아직 지원되지 않아 선택할 수 없습니다.
      </p>
    </SectionCard>
    <SectionCard title="홈 위젯">
      <div>
        {homeWidgetSettings.map((setting) => (
          <SettingRow
            key={setting.label}
            {...setting}
            enabled={false}
            disabled
            disabledReason="아직 홈 화면에 적용되지 않는 설정입니다."
          />
        ))}
      </div>
    </SectionCard>
  </div>
);
