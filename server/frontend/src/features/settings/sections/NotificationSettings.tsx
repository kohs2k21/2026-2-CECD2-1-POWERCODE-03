import { useState } from "react";
import { IconAlertTriangle, IconFlame, IconInfoCircle } from "@tabler/icons-react";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ErrorState, LoadingState } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { decisionBasisLabels, riskFloorLabels, savedSettingsMessage } from "@/features/notifications/notificationLabels";
import {
  NOTIFICATION_WINDOW_DAYS,
  decisionBases,
  isWindowDays,
  riskFloors,
  type DecisionBasis,
  type NotificationSettings as Settings,
  type RiskFloor,
} from "@/features/notifications/notificationsApi";
import { useMySettings, useSaveMySettings } from "@/features/notifications/useNotifications";
import type { AuthUser } from "@/types/auth";
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

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** 입력 문자열 → 조회 기간(일). 정수 1~30이 아니면 null(저장 버튼 비활성). */
const parseWindowDays = (text: string) => (/^\d{1,2}$/.test(text.trim()) && isWindowDays(Number(text.trim())) ? Number(text.trim()) : null);

/**
 * 알림 대상(와이어프레임 #93 notifSettings): 알릴 판정 기준 3택 · 위험도 하한 · 위험도 미정 포함 · 알림 조회 기간(일). 계정별 서버 저장(PUT /api/me/settings).
 * 앞의 세 값은 바꾸면 바로 저장해 알림 목록·배지에 적용한다. 조회 기간은 숫자 입력이라 [저장]으로 저장한다(1~30 정수 밖이면 입력 오류 표시·저장 비활성).
 * 저장 중에는 입력을 잠그고, 실패하면 서버 값으로 되돌려 오류를 보인다. 저장 성공 뒤 알림 목록·배지를 다시 조회한다(useSaveMySettings).
 */
const NotificationTargets = ({ currentUser }: { currentUser: AuthUser }) => {
  const settings = useMySettings();
  const save = useSaveMySettings();
  const [status, setStatus] = useState("");
  // 조회 기간 입력 중인 값(null = 저장값 표시). 저장에 성공하면 비운다.
  const [daysDraft, setDaysDraft] = useState<string | null>(null);
  if (settings.isPending) return <LoadingState>알림 설정을 불러오는 중...</LoadingState>;
  if (settings.isError) {
    return <ErrorState title="알림 설정을 불러오지 못했습니다" onRetry={() => void settings.refetch()}>{errorText(settings.error)}</ErrorState>;
  }
  const value: Settings = save.isPending && save.variables?.notifications ? save.variables.notifications : settings.data.notifications;
  const change = (patch: Partial<Settings>, onSaved?: () => void) => {
    setStatus("");
    const next = { decisionBasis: value.decisionBasis, minRiskLevel: value.minRiskLevel, includeUnrated: value.includeUnrated, windowDays: value.windowDays, ...patch };
    save.mutate({ notifications: next }, { onSuccess: () => { onSaved?.(); setStatus(savedSettingsMessage(currentUser.userType)); } });
  };
  const locked = save.isPending;
  const daysText = daysDraft ?? String(value.windowDays);
  const days = parseWindowDays(daysText);
  const daysInvalid = days === null;
  return (
    <div className="grid gap-4">
      <fieldset className="grid gap-2" disabled={locked}>
        <legend className="mb-1 text-sm font-medium">알릴 판정 기준</legend>
        <RadioGroup
          value={value.decisionBasis}
          onValueChange={(next) => change({ decisionBasis: next as DecisionBasis })}
          disabled={locked}
          className="grid gap-2"
          aria-label="알릴 판정 기준"
        >
          {decisionBases.map((basis) => (
            <div key={basis} className="flex items-center gap-2">
              <RadioGroupItem value={basis} id={`notif-basis-${basis}`} />
              <Label htmlFor={`notif-basis-${basis}`}>{decisionBasisLabels[basis]}</Label>
            </div>
          ))}
        </RadioGroup>
      </fieldset>
      <div className="grid max-w-xs gap-1.5">
        <Label htmlFor="notif-min-risk">위험도 하한</Label>
        <NativeSelect
          id="notif-min-risk"
          value={value.minRiskLevel}
          disabled={locked}
          onChange={(event) => change({ minRiskLevel: event.target.value as RiskFloor })}
        >
          {riskFloors.map((level) => (
            <NativeSelectOption key={level} value={level}>
              {riskFloorLabels[level]}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>
      <div className="grid gap-1">
        <div className="flex items-center gap-2">
          <Checkbox
            id="notif-unrated"
            checked={value.includeUnrated}
            disabled={locked}
            onCheckedChange={(checked) => change({ includeUnrated: checked === true })}
          />
          <Label htmlFor="notif-unrated">위험도 미정 포함</Label>
        </div>
        <p className="text-sm text-muted-foreground">단계가 정해지지 않은 근거(머신러닝 모델 등)가 있는 이상도 알립니다.</p>
      </div>
      <div className="grid max-w-xs gap-1.5">
        <Label htmlFor="notif-window-days">알림 조회 기간</Label>
        <div className="flex items-center gap-2">
          <Input
            id="notif-window-days"
            className="w-24"
            type="number"
            inputMode="numeric"
            min={NOTIFICATION_WINDOW_DAYS.min}
            max={NOTIFICATION_WINDOW_DAYS.max}
            step={1}
            value={daysText}
            disabled={locked}
            aria-invalid={daysInvalid || undefined}
            onChange={(event) => setDaysDraft(event.target.value)}
          />
          <span className="text-sm text-muted-foreground">일</span>
          <Button
            size="sm"
            variant="outline"
            disabled={locked || daysInvalid || days === value.windowDays}
            onClick={() => days !== null && change({ windowDays: days }, () => setDaysDraft(null))}
          >
            저장
          </Button>
        </div>
      </div>
      <p role="status" className="text-sm text-muted-foreground">
        {locked ? "알림 설정 저장 중..." : status}
      </p>
      {save.isError && !locked && (
        <ErrorState title="알림 설정을 저장하지 못했습니다">{errorText(save.error)} 저장된 설정을 그대로 유지합니다.</ErrorState>
      )}
    </div>
  );
};

export const NotificationSettings = ({ currentUser }: { currentUser: AuthUser }) => (
  <div className="grid gap-4">
    <SectionCard title="알림 대상">
      <NotificationTargets currentUser={currentUser} />
    </SectionCard>
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
