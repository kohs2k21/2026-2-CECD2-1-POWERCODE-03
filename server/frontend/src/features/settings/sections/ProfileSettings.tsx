import type { AuthUser } from "../../../types/auth";
import { ProfileField } from "../components/ProfileField";
import { SettingsCard } from "../components/SettingsCard";
import { SettingRow } from "../components/SettingRow";
import {
  feedbackSettings,
  permissionSettings,
} from "../settingsConfig";

const formatCreatedAt = (createdAt: string) => {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) {
    return createdAt;
  }

  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeZone: "Asia/Seoul",
  }).format(date);
};

export const ProfileSettings = ({ currentUser }: { currentUser: AuthUser }) => (
  <div className="settings-grid">
    <SettingsCard title="로그인 계정">
      <ProfileField label="이메일" value={currentUser.email} />
      <ProfileField
        label="계정 역할"
        value={currentUser.userType === "admin" ? "관리자" : "일반 사용자"}
      />
      <ProfileField label="계정 ID" value={currentUser.id} />
      <ProfileField label="계정 생성일" value={formatCreatedAt(currentUser.createdAt)} />
    </SettingsCard>

    <SettingsCard title="권한 표시">
      <SettingRow
        label="관리자 메뉴"
        description="시스템 설정과 모델 관리 메뉴는 계정 역할에 따라 표시됩니다."
        enabled={currentUser.userType === "admin"}
        disabled
        disabledReason="계정 역할에 따라 자동 적용됩니다."
      />
      {permissionSettings.map((setting) => (
        <SettingRow key={setting.label} {...setting} />
      ))}
    </SettingsCard>

    <SettingsCard title="피드백">
      {feedbackSettings.map((setting) => (
        <SettingRow key={setting.label} {...setting} />
      ))}
    </SettingsCard>
  </div>
);
