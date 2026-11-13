import { useState } from "react";
import { Link } from "react-router-dom";
import { paths } from "@/app/routes";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { useMySettings, useSaveMySettings } from "@/features/notifications/useNotifications";
import { stampText } from "@/features/operations/format";
import { defaultWidgetLayout, parseStoredLayout, serializeLayout } from "@/features/operations/data/widgetLayout";
import { cn } from "@/lib/utils";
import type { AuthUser } from "@/types/auth";
import { SettingRow } from "../components/SettingRow";
import { dashboardDisplaySettings } from "../settingsConfig";

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/**
 * 운영 현황 개요 위젯 배치(계정별 서버 저장): 저장 여부·위젯 수·저장 시각, 운영 현황에서 편집, 기본 배치로 되돌리기(기본 배치를 저장).
 * 편집 자체는 운영 현황 화면(위젯 편집)에서 한다.
 */
const WidgetLayoutSummary = ({ currentUser }: { currentUser: AuthUser }) => {
  const settings = useMySettings();
  const save = useSaveMySettings();
  const [status, setStatus] = useState("");
  if (settings.isPending) return <LoadingState>위젯 배치를 불러오는 중...</LoadingState>;
  if (settings.isError) {
    return <ErrorState title="저장된 위젯 배치를 불러오지 못했습니다" onRetry={() => void settings.refetch()}>{errorText(settings.error)}</ErrorState>;
  }
  const stored = parseStoredLayout(settings.data.widgetLayout.layout);
  const reset = () => {
    setStatus("");
    save.mutate(
      { widgetLayout: serializeLayout(defaultWidgetLayout) },
      { onSuccess: () => setStatus(`기본 배치로 저장됨 · ${currentUser.userType === "admin" ? "관리자" : "일반 사용자"} 계정 · 위젯 ${defaultWidgetLayout.length}개`) },
    );
  };
  return (
    <div className="grid gap-3">
      <p className="text-sm">
        {stored
          ? `계정에 저장된 배치 · 위젯 ${stored.length}개 · 저장 ${stampText(settings.data.widgetLayout.updatedAt)}`
          : `저장된 배치 없음 · 기본 배치(위젯 ${defaultWidgetLayout.length}개) 사용 중`}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline" size="sm">
          <Link to={paths.operations}>운영 현황에서 편집</Link>
        </Button>
        <Button variant="outline" size="sm" onClick={reset} disabled={save.isPending || !stored}>
          {save.isPending ? "저장 중" : "기본 배치로 되돌리기"}
        </Button>
      </div>
      <p role="status" className="text-sm text-muted-foreground">
        {status}
      </p>
      {save.isError && <ErrorState title="위젯 배치를 저장하지 못했습니다">{errorText(save.error)}</ErrorState>}
    </div>
  );
};

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

export const DisplaySettings = ({ currentUser }: { currentUser: AuthUser }) => (
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
    <SectionCard title="운영 현황 위젯 배치">
      <WidgetLayoutSummary currentUser={currentUser} />
    </SectionCard>
  </div>
);
