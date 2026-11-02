import { DescriptionList } from "@/components/layout/DescriptionList";
import { SectionCard } from "@/components/layout/SectionCard";
import type { AuthUser } from "@/types/auth";
import { SettingRow } from "../components/SettingRow";
import { feedbackSettings, permissionSettings } from "../settingsConfig";

const formatCreatedAt = (createdAt: string) => {
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) return createdAt;
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeZone: "Asia/Seoul" }).format(date);
};

/** 사용자 환경: 로그인 계정은 현재 세션 사용자(/api/auth/me) 실데이터. */
export const ProfileSettings = ({ currentUser }: { currentUser: AuthUser }) => (
  <div className="grid gap-4">
    <SectionCard title="로그인 계정">
      <DescriptionList
        items={[
          { label: "이메일", value: currentUser.email },
          { label: "계정 역할", value: currentUser.userType === "admin" ? "관리자" : "일반 사용자" },
          { label: "계정 ID", value: currentUser.id },
          { label: "계정 생성일", value: formatCreatedAt(currentUser.createdAt) },
        ]}
      />
    </SectionCard>
    <SectionCard title="권한 표시">
      <div>
        <SettingRow
          label="관리자 메뉴"
          description="탐지 설정 메뉴는 계정 역할에 따라 표시됩니다."
          enabled={currentUser.userType === "admin"}
          disabled
          disabledReason="계정 역할에 따라 자동 적용됩니다."
        />
        {permissionSettings.map((setting) => (
          <SettingRow key={setting.label} {...setting} />
        ))}
      </div>
    </SectionCard>
    <SectionCard title="피드백">
      <div>
        {feedbackSettings.map((setting) => (
          <SettingRow key={setting.label} {...setting} />
        ))}
      </div>
    </SectionCard>
  </div>
);
